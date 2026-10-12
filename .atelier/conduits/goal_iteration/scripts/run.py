"""Durable outcome state and Git guards for automatic-goal (standard library only)."""

import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

import memory
import session_report


TERMINAL = {"ACHIEVED", "PARTIAL", "BLOCKED", "OPERATIONAL_FAILURE"}
AMBITIONS = {"bold", "normal", "polish"}
# Why work that was not kept failed: the hypothesis was wrong, the approach failed, or the budget ran out.
CAUSES = {"idea", "execution", "time"}
JUDGED = {"unmet", "partial", "met"}
# One scorecard command may take this long; the controller runs them, so they must stay cheap.
MEASURE_SECONDS = 120
# A reviewer may accept with small notes only when at least this share of the change is ready as is.
ACCEPT_READY_PERCENT = 70
# Set by `check`: validate a result without changing state, history or Git.
DRY = False


class Checked(Exception):
    """A dry check reached the first change to history or Git, so the result is valid."""


def effect():
    """Everything after this call changes history or Git; a dry check stops here."""
    if DRY:
        raise Checked


def git(*args):
    return memory.git(Path.cwd(), *args)


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(value)
    os.replace(name, path)


def write_json(path, value):
    atomic(path, json.dumps(value, indent=2) + "\n")


def read_json(path):
    # From bytes, json detects the encoding and skips the byte-order mark PowerShell writes.
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def dirty():
    return [line for line in git("status", "--porcelain").splitlines()
            if not line.startswith("?? .atelier/")]


def guard(clean=False):
    # An agent's own shell may not carry the variable, and a dry check starts no agent.
    if not DRY and os.environ.get("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS") != "1":
        raise RuntimeError("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS must be exactly 1")
    cwd = Path.cwd().resolve()
    if Path(git("rev-parse", "--show-toplevel")).resolve() != cwd:
        raise RuntimeError("run from the worktree root")
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir"))
    if Path(git("rev-parse", "--absolute-git-dir")) == common:
        raise RuntimeError("use a dedicated linked worktree")
    branch = git("symbolic-ref", "--short", "-q", "HEAD")
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", branch):
        raise RuntimeError("unsupported branch name")
    if any(line.startswith("160000 ") for line in git("ls-files", "-s").splitlines()):
        raise RuntimeError("submodules are not supported")
    if git("ls-files", ".atelier"):
        raise RuntimeError("tracked files under .atelier are not allowed")
    if clean and dirty():
        raise RuntimeError("worktree is not clean; work is preserved")
    return branch


def location():
    path = Path(".atelier/goal")
    if not memory.is_link(path):
        raise RuntimeError("no goal checkpoint; initialize a run first")
    state = read_json(path / "state.json")
    if state["worktree"] != str(Path.cwd().resolve()):
        raise RuntimeError("goal checkpoint belongs to another worktree")
    return path, state


def detailed_report(state):
    brief = state.get("brief") or {}
    final = state.get("final") or {}
    lines = ["# Outcome handoff", "", f"Status: {state['status']}",
             f"Branch: {state['branch']}", f"Run: {state['id']}",
             f"Accepted HEAD: {state['accepted_head']}",
             f"Elapsed seconds: {int(state.get('finished', time.time()) - state['started'])}", "",
             "## Intended outcome", brief.get("outcome", "Planning not completed."), "",
             "## Before", final.get("before", "\n".join(brief.get("baseline", [])) or "Not measured."), "",
             "## After", final.get("after", "No final demonstration recorded; inspect milestone evidence below."), "",
             "## Evidence", *[f"- {item}" for item in final.get("evidence", [])], "",
             "## Limitations and unfinished work", state.get("reason", ""),
             *[f"- {item}" for item in final.get("limitations", []) + final.get("remaining", [])], "",
             "## Milestones"]
    for item in state["milestones"]:
        lines += [f"### {item['id']}: {item['title']} — {item['status']}",
                  item["benefit"], f"Base: {item['base']} · HEAD: {item.get('head', 'uncommitted')}",
                  f"Worker turns: {item['turns']} · Repair rounds: {item['revisions']} · "
                  f"Time used: {int(item['spent_seconds'])}s of {item['budget_seconds']}s"]
        if item.get("ambition"):
            lines.append(f"Ambition: {item['ambition']} · Upside: {item['upside']} · Risk: {item['risk']}")
        lines.append(session_report.bet(item, brief))
        if item.get("cause"):
            lines.append(f"Why it was not kept: {session_report.CAUSES[item['cause']]}")
        if item.get("blocker"):
            lines.append(f"Blocked: {item['blocker']}")
        if item.get("checkpoint_ref"):
            lines.append(f"Preserved implementation: {item['checkpoint_ref']}")
        for review in item.get("reviews", []):
            ready = f" ({review['ready_percent']}% ready)" if "ready_percent" in review else ""
            lines.append(f"{review['verdict']}{ready}: {review['reason']}")
            if review["verdict"] == "CONTINUE":
                lines.append(f"Direction: {review['direction']}")
            else:
                lines += [f"Correctness: {review['correctness']}", f"Value: {review['value']}"]
            lines += [*[f"- {e}" for e in review["evidence"]],
                      *[f"- Note: {n}" for n in review.get("notes", [])]]
    if state.get("active") is not None:
        lines += ["", "An unfinished milestone remains. This branch is not reported as ready to merge."]
    lines += ["", "## Scorecard"]
    for row in session_report.scorecard(state):
        lines.append(f"- {row['outcome']}: {row['before']} → {row['after']}" +
                     (f" (target {row['target']})" if row["target"] else ""))
    lines += ["", "## Reproducible checks"]
    for check in final.get("checks", []):
        lines.append(f"- `{check['command']}` → exit {check['exit_code']}: {check['summary']}")
    return "\n".join(lines) + "\n"


def report(state):
    return ("# Session report\n\n" + session_report.markdown(session_report.summarize(state)) +
            "\n<details>\n<summary>Full review and validation record</summary>\n\n" +
            detailed_report(state) + "\n</details>\n")


def save(path, state):
    write_json(path / "state.json", state)
    atomic(path / "handoff.md", report(state))


def initialize(deadline, reserve=10, revisions=2, buffer=5):
    branch = guard(clean=True)
    now = time.time()
    if deadline <= now:
        raise ValueError("deadline must be in the future")
    if not 5 <= reserve <= 50 or not 0 <= revisions <= 5 or not 0 <= buffer <= 25:
        raise ValueError("reserve must be 5–50, max_revisions 0–5, usage buffer 0–25")
    local = Path(".atelier/goal")
    if local.exists() or memory.is_link(local):
        _, old = location()
        if old["status"] not in TERMINAL:
            raise RuntimeError("unfinished goal exists; resume that flow or inspect its checkpoint first")
        local.unlink()
    root = memory.attach(Path.cwd().resolve())
    identifier = uuid.uuid4().hex
    path = root / "runs" / identifier
    path.mkdir(parents=True)
    memory.link(local, path)
    state = {"version": 3, "id": identifier, "worktree": str(Path.cwd().resolve()),
             "branch": branch, "initial_base": git("rev-parse", "HEAD"),
             "accepted_head": git("rev-parse", "HEAD"), "started": now,
             "deadline": deadline, "finish_at": now + (deadline - now) * (1 - reserve / 100),
             "handoff_seconds": min(300, max(1, (deadline - now) * .05)),
             "reserve_percent": reserve, "max_revisions": revisions, "usage_buffer": buffer,
             "phase": "SUPERVISE", "status": "ACTIVE", "brief": None,
             "scorecard": None, "judged": {},
             "active": None, "milestones": [], "events": [], "request": None,
             "protocol_failures": 0, "reason": "Initial observation and prioritization pending."}
    save(path, state)
    memory.index(root)
    return identifier


def finish(path, state, status, reason):
    state.update(status=status, reason=reason, finished=time.time(), request=None)
    save(path, state)
    return "GOAL_DONE"


def check_git(state, clean=True, expected=None):
    if guard(clean=clean) != state["branch"]:
        raise RuntimeError("branch changed; work is preserved")
    if expected and git("rev-parse", "HEAD") != expected:
        raise RuntimeError("unexpected HEAD; work is preserved")


def verify_range(base):
    head = git("rev-parse", "HEAD")
    if head == base:
        raise RuntimeError("no commit was made; work is preserved")
    git("merge-base", "--is-ancestor", base, head)
    if git("rev-list", "--merges", f"{base}..{head}"):
        raise RuntimeError("merge commits are not allowed")
    if not git("diff", "--name-only", base, head):
        raise RuntimeError("empty milestone; work is preserved")
    # Even adding and then deleting a journal would leak it into history.
    if git("log", "--format=%H", f"{base}..{head}", "--", ".atelier"):
        raise RuntimeError(".atelier was committed; work is preserved")
    return head


def active(state):
    return state["milestones"][state["active"]]


def journal(state):
    effect()
    if state["active"] is None:
        return
    item = active(state)
    root = Path(".atelier/implementations")
    body = [f"# Idea\n{item['title']}", f"## Goal\n{state['brief']['outcome']}",
            f"## Prior decisions\nRun {state['id']}; opportunity {item['opportunity']}",
            "## Hypothesis\n" + "\n".join([session_report.bet(item, state["brief"]), *item["acceptance"]]),
            f"## Value\n{item['benefit']}", f"## Status\n{item['status']}",
            f"## Checkpoint\n{item['base']}..{item.get('head', 'pending')}",
            "## Record\n" + json.dumps(item, indent=2)]
    if item["status"] in {"ACCEPT", "ABANDON", "DEFERRED", "BLOCKED"}:
        # Keep why an idea was not kept next to the verdict, so later checkpoints do not retry it blindly.
        why = item.get("blocker") or (item["reviews"][-1]["reason"] if item["reviews"] else "")
        if item.get("cause"):
            why = f"{session_report.CAUSES[item['cause']]}. {why}"
        body.append(f"## Verdict\nVERDICT: {item['status']}\n{why}".rstrip())
    atomic(root / f"{item['id']}.md", "\n\n".join(body) + "\n")
    memory.index(root)


def retire(state, status):
    """Preserve rejected/deferred/blocked commits under a ref before rolling back this milestone."""
    effect()
    item = active(state)
    check_git(state, expected=item["head"])
    ref = f"refs/automatic-goal/{state['id']}/{item['id']}"
    git("update-ref", ref, item["head"])
    item.update(status=status, checkpoint_ref=ref)
    journal(state)
    git("reset", "--hard", item["base"])
    # The tree was checked clean; no untracked user files need deleting.
    state["active"] = None


def shelve(state, status):
    """Close the active milestone without accepting it; a ref is needed only when it has commits."""
    item = active(state)
    if item.get("head", item["base"]) != item["base"]:
        retire(state, status)
        return
    item["status"] = status
    journal(state)
    state["active"] = None


def text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")


def texts(value, name, nonempty=True):
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f"{name} must be a {'nonempty ' if nonempty else ''}list")
    for item in value:
        text(item, name)


def positive(value, name):
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def number(value, name):
    if type(value) not in (float, int) or not math.isfinite(value):
        raise ValueError(f"{name} must be a number")


def cause(result):
    if result["cause"] not in CAUSES:
        raise ValueError("cause must be idea (the hypothesis was wrong), execution (the approach "
                         "failed) or time (the budget ran out)")


def checks(value, passing=False):
    if not isinstance(value, list) or not value:
        raise ValueError("checks must include reproducible validation")
    for item in value:
        text(item["command"], "check command")
        text(item["summary"], "check summary")
        if type(item["exit_code"]) is not int or (passing and item["exit_code"] != 0):
            raise ValueError("passing checks with integer exit codes required")
        if (type(item["duration_seconds"]) not in (float, int) or
                not math.isfinite(item["duration_seconds"]) or item["duration_seconds"] < 0):
            raise ValueError("check duration_seconds must be nonnegative")


def validate_scorecard(card):
    if not isinstance(card, list) or not 1 <= len(card) <= 6:
        raise ValueError("scorecard must list one to six outcomes")
    for item in card:
        text(item["id"], "scorecard id")
        text(item["outcome"], "scorecard outcome")
        if item["kind"] == "measured":
            text(item["command"], "scorecard command")
            if item["direction"] not in {"up", "down"}:
                raise ValueError("a measured outcome needs direction up or down")
            if "target" in item:
                number(item["target"], "scorecard target")
        elif item["kind"] == "judged":
            text(item["how"], "scorecard how")
            text(item["baseline"], "scorecard baseline")
        else:
            raise ValueError("scorecard kind must be measured or judged")
    if len({item["id"] for item in card}) != len(card):
        raise ValueError("scorecard ids must be unique")
    if not any(item["kind"] == "judged" for item in card):
        raise ValueError("scorecard needs at least one judged outcome, read by using the result, "
                         "so a number alone cannot stand for the goal")


def measure(card):
    """Read every measured outcome. The controller runs the commands, so no agent reports its own
    numbers. Call only on a clean tree: whatever a command leaves behind is removed again."""
    values, errors = {}, {}
    for item in card:
        if item["kind"] != "measured":
            continue
        try:
            proc = subprocess.run([shutil.which("bash") or "bash", "-c", item["command"]],
                                  capture_output=True, text=True, timeout=MEASURE_SECONDS,
                                  stdin=subprocess.DEVNULL)
            if proc.returncode:
                raise ValueError(f"exit code {proc.returncode}")
            lines = [line for line in proc.stdout.splitlines() if line.strip()]
            if not lines:
                raise ValueError("no output")
            values[item["id"]] = float(lines[-1])
            number(values[item["id"]], "the last line of output")
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            values[item["id"]] = None
            errors[item["id"]] = f"{type(error).__name__}: {error}"[:300]
    left = dirty()
    if left:
        git("checkout", "--", ".")
        git("clean", "-fdq", "-e", ".atelier")
        errors["worktree"] = ("a command changed the worktree (restored): " + "; ".join(left))[:300]
    return {"at": time.time(), "head": git("rev-parse", "HEAD"), "values": values, "errors": errors}


def judged(state, entries, complete):
    """The coordinator's own reading of the judged outcomes, each backed by what it observed."""
    wanted = {item["id"] for item in state["brief"]["scorecard"] if item["kind"] == "judged"}
    if not isinstance(entries, list):
        raise ValueError("judged must be a list")
    for entry in entries:
        if entry["id"] not in wanted:
            raise ValueError("judged entries must name judged scorecard ids")
        if entry["state"] not in JUDGED:
            raise ValueError("judged state must be unmet, partial or met")
        text(entry["evidence"], "judged evidence")
    if complete and {entry["id"] for entry in entries} != wanted:
        raise ValueError("judge every judged scorecard outcome: " + ", ".join(sorted(wanted)))
    return {entry["id"]: {"state": entry["state"], "evidence": entry["evidence"]} for entry in entries}


def baseline(state):
    """Take the starting reading when a brief is adopted; a command that fails goes back to its author."""
    reading = measure(state["brief"]["scorecard"])
    if reading["errors"]:
        raise ValueError("scorecard commands must print one number on their last line, exit 0 and "
                         "leave the worktree alone: " +
                         "; ".join(f"{key}: {value}" for key, value in reading["errors"].items()))
    old = state.get("scorecard") or {}
    # A replan keeps the first reading of outcomes it still tracks, so gains stay comparable.
    first = {**reading, "values": {**reading["values"],
                                   **{key: value for key, value in (old.get("baseline") or {}).get("values", {}).items()
                                      if key in reading["values"]}}}
    state["scorecard"] = {"baseline": first, "latest": reading}


def validate_brief(brief):
    for key in ("goal_interpretation", "outcome", "selection_reason", "research"):
        text(brief[key], key)
    for key in ("baseline", "preserve"):
        texts(brief[key], key)
    validate_scorecard(brief["scorecard"])
    texts(brief["assumptions"], "assumptions", False)
    opportunities = brief["opportunities"]
    if not isinstance(opportunities, list) or len(opportunities) < 2:
        raise ValueError("compare at least two credible opportunities")
    ids = []
    for item in opportunities:
        for key in ("id", "title", "benefit", "evidence", "effort", "uncertainty"):
            text(item[key], key)
        ids.append(item["id"])
    if len(set(ids)) != len(ids) or brief["primary"] not in ids or brief["fallback"] not in ids:
        raise ValueError("primary and fallback must reference unique opportunity IDs")
    if brief["primary"] == brief["fallback"]:
        raise ValueError("fallback must differ from primary")


def validate_milestone(item, state):
    for key in ("title", "benefit", "upside", "risk"):
        text(item[key], key)
    for key in ("acceptance", "scope", "checks"):
        texts(item[key], key)
    positive(item["budget_seconds"], "budget_seconds")
    if item["ambition"] not in AMBITIONS:
        raise ValueError("ambition must be bold, normal, or polish")
    hypothesis = item["hypothesis"]
    for key in ("action", "expected", "because", "failure_mode"):
        text(hypothesis[key], f"hypothesis {key}")
    if hypothesis["outcome"] not in {o["id"] for o in state["brief"]["scorecard"]}:
        raise ValueError("hypothesis outcome must name a scorecard id")
    # Small polish may skip it; anything larger first tests the way it is most likely to fail.
    if item["ambition"] != "polish" or "probe" in item:
        text(item["probe"], "probe")
    if item["opportunity"] not in {o["id"] for o in state["brief"]["opportunities"]}:
        raise ValueError("milestone must serve a compared opportunity")
    depends = item.get("depends_on", [])
    texts(depends, "depends_on", False)
    if not set(depends) <= {m["id"] for m in state["milestones"] if m["status"] == "ACCEPT"}:
        raise ValueError("depends_on must list accepted milestone IDs")


def start_milestone(state, proposal, integration=False):
    validate_milestone(proposal, state)
    limit = state["deadline"] - state["handoff_seconds"] if integration else state["finish_at"]
    if time.time() + proposal["budget_seconds"] > limit:
        if integration:
            state.update(phase="FINALIZE", reason="Proposed work cannot finish within its budget.")
        else:
            # Too big is not a reason to stop: ask the supervisor for something that fits.
            state["reason"] = (f"Proposed milestone needs {proposal['budget_seconds']}s but only "
                               f"{max(0, int(limit - time.time()))}s of feature time remain; choose a smaller one.")
        if DRY:
            raise ValueError(state["reason"])
        return
    effect()
    item = {**proposal, "id": memory.reserve(Path(".atelier/implementations")),
            "base": state["accepted_head"], "status": "BUILDING", "revisions": 0,
            "reviews": [], "integration": integration, "turns": 0, "spent_seconds": 0,
            "before": state["scorecard"]["latest"]["values"], "readings": []}
    state["milestones"].append(item)
    state.update(active=len(state["milestones"]) - 1, phase="IMPLEMENT")
    journal(state)


def supervise(state, result):
    if state["brief"] is None:
        validate_brief(result["brief"])
        state["brief"] = result["brief"]
        baseline(state)
    elif "brief" in result and result["brief"] != state["brief"]:
        # An evidence-backed replan is allowed; the earlier brief remains in the event log.
        text(result["replan_reason"], "replan_reason")
        validate_brief(result["brief"])
        remeasure = result["brief"]["scorecard"] != state["brief"]["scorecard"]
        state["brief"] = result["brief"]
        if remeasure:
            baseline(state)
    added = result.get("new_opportunities", [])
    if added:
        # Ideas grow at every checkpoint, so meeting the first brief never runs the list dry.
        state["brief"] = {**state["brief"], "opportunities": state["brief"]["opportunities"] + added}
        validate_brief(state["brief"])
    for key in ("reason", "learning", "priority"):
        text(result[key], key)
    state.update(reason=result["reason"], priority=result["priority"])
    action = result["action"]
    if action in {"BUILD", "SIMPLIFY", "SWITCH"}:
        start_milestone(state, result["milestone"])
    elif action == "FINISH":
        raise ValueError("FINISH is not available: keep improving until the finishing reserve; "
                         "the controller starts the final demonstration")
    elif action == "BLOCKED":
        state.update(phase="FINALIZE", reason=result["reason"])
    else:
        raise ValueError("unknown supervisor action")


def implemented(state, result):
    item = active(state)
    if result["status"] == "BLOCKED":
        # A blocked or disproven idea is a normal outcome: save the attempt, log why, keep going.
        text(result["reason"], "reason")
        cause(result)
        check_git(state, clean=False)
        git("merge-base", "--is-ancestor", item["base"], "HEAD")
        effect()
        if dirty():
            # An exclude pathspec fails when .atelier is ignored, so stage all and unstage .atelier.
            git("add", "-A")
            git("reset", "-q", "--", ".atelier")
            git("commit", "-q", "--no-verify", "-m", f"automatic-goal: preserve blocked attempt {item['id']}")
        item.update(head=git("rev-parse", "HEAD"), blocker=result["reason"], cause=result["cause"])
        shelve(state, "BLOCKED")
        state.update(phase="FINALIZE" if item["integration"] else "SUPERVISE", reason=result["reason"])
        return
    ready = result["status"] == "READY"
    if not ready and result["status"] != "CHECKPOINT":
        raise ValueError("implementation status must be READY, CHECKPOINT or BLOCKED")
    if ready and "probe" in item and not item["turns"]:
        raise ValueError("the first turn of this milestone is its probe: return CHECKPOINT with what "
                         "the probe showed, or BLOCKED if it shows the idea cannot work")
    check_git(state)
    head = git("rev-parse", "HEAD")
    # A checkpoint may report findings before any commit exists; whatever was committed must be sound.
    if ready or head != item["base"]:
        head = verify_range(item["base"])
    git("merge-base", "--is-ancestor", state["request"]["head"], head)
    if result["head"] != head:
        raise ValueError("implementation head does not match Git")
    text(result["summary"], "summary")
    texts(result["evidence"], "evidence")
    if ready:
        checks(result["checks"], passing=True)
        item["implementation"] = result
    elif "question" in result:
        text(result["question"], "question")
    item.update(head=head, progress=result, turns=item["turns"] + 1,
                status="REVIEWING" if ready else "CHECKPOINT")
    if not DRY:
        # The coordinator steers on numbers the controller read after this turn, not on a self-report.
        item["readings"].append(measure(state["brief"]["scorecard"]))
    state["phase"] = "REVIEW"


def reviewed(state, result):
    item = active(state)
    if result["head"] != item["head"]:
        raise ValueError("review must identify the reviewed HEAD")
    verdict = result["verdict"]
    if verdict not in {"ACCEPT", "REVISE", "ABANDON", "CONTINUE"}:
        raise ValueError("verdict must be ACCEPT, REVISE, ABANDON, or CONTINUE")
    text(result["reason"], "reason")
    texts(result["evidence"], "evidence")
    readings = judged(state, result.get("judged", []), complete=verdict == "ACCEPT")
    if verdict == "CONTINUE":
        # A check-in inside the milestone: steer or answer, then hand the same conversation back.
        text(result["direction"], "direction")
        positive(result["turn_seconds"], "turn_seconds")
        limit = state["deadline"] - state["handoff_seconds"] if item["integration"] else state["finish_at"]
        if item["spent_seconds"] + result["turn_seconds"] > item["budget_seconds"]:
            raise ValueError("this milestone's time budget is used up: ACCEPT what is worth keeping, "
                             "or ABANDON with cause time")
        if time.time() + result["turn_seconds"] > limit:
            raise ValueError("another turn does not fit in the time left: ACCEPT what is worth "
                             "keeping, or ABANDON with cause time")
        item["reviews"].append(result)
        item.update(status="BUILDING", direction=result["direction"])
        state.update(reason=result["reason"], phase="IMPLEMENT")
        return
    for key in ("correctness", "value"):
        text(result[key], key)
    texts(result["findings"], "findings", False)
    # Only critical and high findings are worth a repair round; anything below is approved as it is.
    if not all(re.match(r"(critical|high): \S", finding) for finding in result["findings"]):
        raise ValueError('each finding must start with "critical: " or "high: "; '
                         "medium and low issues are approved as they are and go in notes")
    texts(result.get("notes", []), "notes", False)
    ready = result["ready_percent"]
    if type(ready) is not int or not 0 <= ready <= 100:
        raise ValueError("ready_percent must be an integer from 0 to 100")
    checks(result["checks"], passing=verdict == "ACCEPT")
    if verdict == "ACCEPT" and result["findings"]:
        raise ValueError("required findings must be resolved before ACCEPT; small issues go in notes")
    if verdict == "ACCEPT" and ready < ACCEPT_READY_PERCENT:
        raise ValueError(f"ACCEPT requires ready_percent of at least {ACCEPT_READY_PERCENT}")
    if verdict == "ACCEPT" and item["head"] == item["base"]:
        raise ValueError("nothing was committed, so there is nothing to ACCEPT")
    if verdict == "REVISE":
        texts(result["findings"], "repair findings")
        positive(result["repair_estimate_seconds"], "repair_estimate_seconds")
    if verdict == "ABANDON":
        cause(result)
    item["reviews"].append(result)
    state["reason"] = result["reason"]
    if verdict == "ACCEPT":
        item.update(status="ACCEPT", judged=readings)
        state["judged"] = {**state["judged"], **readings}
        if item["readings"]:
            state["scorecard"] = {**state["scorecard"], "latest": item["readings"][-1]}
        journal(state)
        state.update(accepted_head=item["head"], active=None,
                     phase="FINALIZE" if item["integration"] else "SUPERVISE")
    elif verdict == "ABANDON":
        item["cause"] = result["cause"]
        shelve(state, "ABANDON")
        state["phase"] = "FINALIZE" if item["integration"] else "SUPERVISE"
    elif (item["revisions"] >= state["max_revisions"] or
          time.time() + result["repair_estimate_seconds"] > state["deadline"] - state["handoff_seconds"]):
        item["cause"] = "time"
        shelve(state, "DEFERRED")
        state.update(phase="FINALIZE" if item["integration"] else "SUPERVISE",
                     reason="Repair budget exhausted; implementation checkpoint preserved.")
    else:
        item.update(revisions=item["revisions"] + 1, status="REVISING")
        state["phase"] = "IMPLEMENT"


def reached(outcome, state, reading):
    """Whether one scorecard outcome is where the brief wanted it: judged met, or the target reached."""
    if outcome["kind"] == "judged":
        return state["judged"].get(outcome["id"], {}).get("state") == "met"
    value = reading["values"].get(outcome["id"])
    if "target" not in outcome:
        return True
    if value is None:
        return False
    return value >= outcome["target"] if outcome["direction"] == "up" else value <= outcome["target"]


def finalized(state, result):
    status = result["status"]
    if status not in {"ACHIEVED", "PARTIAL", "BLOCKED", "REVISE"}:
        raise ValueError("unknown final status")
    if result["head"] != state["accepted_head"]:
        raise ValueError("final demonstration must identify the accepted HEAD")
    for key in ("before", "after", "reason"):
        text(result[key], key)
    for key in ("evidence", "limitations", "remaining"):
        texts(result[key], key, key == "evidence")
    checks(result["checks"], passing=status == "ACHIEVED")
    texts(result.get("next_run", []), "next_run", False)
    texts(result.get("questions", []), "questions", False)
    if len(result.get("questions", [])) > 3:
        raise ValueError("ask the owner at most three questions")
    if state["brief"] is None:
        if status == "ACHIEVED":
            raise ValueError("ACHIEVED needs a brief and its scorecard")
    else:
        state["judged"] = {**state["judged"], **judged(state, result.get("judged", []), status == "ACHIEVED")}
        final = measure(state["brief"]["scorecard"])
        state["scorecard"] = {**state["scorecard"], "final": final}
        short = [item["id"] for item in state["brief"]["scorecard"] if not reached(item, state, final)]
        if status == "ACHIEVED" and short:
            raise ValueError("ACHIEVED requires every judged outcome met and every measured target "
                             "reached; not there yet: " + ", ".join(short))
    state["final"] = result
    if status == "REVISE":
        repairs = sum(m["integration"] for m in state["milestones"])
        if repairs >= state["max_revisions"]:
            state.update(status="PARTIAL", reason="Integration repair budget exhausted.", finished=time.time())
        else:
            start_milestone(state, result["milestone"], integration=True)
            if state["phase"] == "FINALIZE":
                state.update(status="PARTIAL", reason="Insufficient time for integration repair.", finished=time.time())
    else:
        state.update(status=status, reason=result["reason"], finished=time.time())


def learned(result):
    """Optional notes for later agents, so nobody rediscovers the same thing."""
    items = result.get("learned", [])
    if not isinstance(items, list) or len(items) > 5:
        raise ValueError("learned must be a list of at most 5 notes")
    for item in items:
        if item["kind"] not in memory.NOTE_KINDS:
            raise ValueError("learned kind must be fact, pitfall, or decision")
        text(item["note"], "learned note")
        text(item["evidence"], "learned evidence")
    return [{key: item[key] for key in ("kind", "note", "evidence")} for item in items]


def transition(path, state):
    """Read result.json and apply it to a copy, so a malformed result cannot partly change canonical state."""
    request = state["request"]
    stage = request["stage"]
    check_git(state, clean=stage != "IMPLEMENT", expected=request["head"] if stage != "IMPLEMENT" else None)
    result = read_json(path / "result.json")
    if result["request_id"] != request["id"]:
        raise ValueError("stale result: request_id does not match request.json")
    learned(result)
    candidate = json.loads(json.dumps(state))
    {"SUPERVISE": supervise, "IMPLEMENT": implemented,
     "REVIEW": reviewed, "FINALIZE": finalized}[stage](candidate, result)
    return candidate, result


def check_result():
    """Let an agent validate its result before ending its turn; nothing is changed."""
    global DRY
    path, state = location()
    if not state["request"] or state["status"] in TERMINAL:
        raise ValueError("no stage is waiting for a result")
    DRY = True
    try:
        transition(path, state)
    except Checked:
        pass
    return f"OK: result.json is valid for this {state['request']['stage']} request"


def session():
    """Name one agent conversation per milestone, so a repair continues the build that made it."""
    return f"milestone-{active(location()[1])['id']}"


def stop(reason):
    """Close a run early at the operator's request; all work, including an unfinished milestone, is kept."""
    path, state = location()
    if state["status"] in TERMINAL:
        return "GOAL_DONE"
    return finish(path, state, "PARTIAL", f"Stopped by the operator: {reason}")


def apply_result():
    path, state = location()
    request = state["request"]
    if not request or state["status"] in TERMINAL:
        # A process can stop after saving the transition but before Atelier saves stdout.
        # Replaying that completed apply must not create another milestone or commit.
        return "GOAL_DONE" if state["status"] in TERMINAL else "CONTINUE"
    stage = request["stage"]
    milestone = active(state)["id"] if state["active"] is not None else None
    try:
        state, result = transition(path, state)
    except (ValueError, KeyError, TypeError, OSError) as error:
        state["protocol_failures"] += 1
        state["reason"] = f"Invalid {stage} result ({type(error).__name__}): {error}"
        state["events"].append({"stage": stage, "kind": "PROTOCOL_ERROR", "at": time.time(),
                                "reason": state["reason"]})
        if state["protocol_failures"] > 2:
            return finish(path, state, "OPERATIONAL_FAILURE", state["reason"] + "; recovery exhausted; work preserved.")
        # Keep the same request ID: repair only the stage artifact, never redo committed work.
        save(path, state)
        return "RECOVER"
    except (RuntimeError, subprocess.CalledProcessError) as error:
        return finish(path, state, "OPERATIONAL_FAILURE", f"Git guard failed: {error}; work preserved.")
    spent = time.time() - request["started"]
    state["events"].append({"stage": stage, "kind": "RESULT", "at": time.time(),
                            "duration_seconds": spent, "milestone": milestone, "result": result})
    for item in state["milestones"]:
        # Agent time, not wall time, is charged to a milestone, so a usage pause costs it nothing.
        if item["id"] == milestone and stage in {"IMPLEMENT", "REVIEW"}:
            item["spent_seconds"] += spent
    state.update(request=None, protocol_failures=0)
    if result.get("learned"):
        root = Path(".atelier/implementations")
        memory.learn(root, learned(result), {"run": state["id"], "stage": stage, "milestone": milestone})
        memory.index(root)
    journal(state)
    save(path, state)
    return "GOAL_DONE" if state["status"] in TERMINAL else "CONTINUE"


def route(run_id, deadline, floors, poll, weekly="Fable"):
    if not Path(".atelier/goal").exists() and run_id == "auto":
        initialize(deadline)
    path, state = location()
    if run_id != "auto" and run_id != state["id"]:
        raise RuntimeError("run ID differs from the current checkpoint")
    if state["status"] in TERMINAL:
        return "GOAL_DONE"
    if deadline != state["deadline"]:
        raise RuntimeError("deadline differs from the saved run; resume with the original deadline")
    if time.time() >= deadline:
        return finish(path, state, "PARTIAL", "Deadline reached. Unfinished work is preserved; no achievement inferred.")
    # Use the existing telemetry reader; stdout and this JSON contain only safe summaries.
    env = {**os.environ, "GOAL_USAGE_FILE": str((path / "usage.json").resolve())}
    usage_file = path / "usage.json"
    usage_file.unlink(missing_ok=True)
    try:
        proc = subprocess.run([sys.executable, str(Path(__file__).with_name("usage.py")), *map(str, floors), weekly],
                              env=env, capture_output=True, text=True, timeout=120)
        state["usage_text"] = proc.stdout.strip() or "Usage unavailable: reader produced no summary."
        reader_ok = proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as error:
        reader_ok = False
        state["usage_text"] = f"Usage unavailable: {type(error).__name__}."
    try:
        usage = read_json(usage_file)
        values = usage["remaining"]
        ready = (reader_ok and len(values) == 3 and
                 all(type(v) in (float, int) and 0 <= v <= 100 and v >= floor
                     for v, floor in zip(values, floors)))
    except (OSError, ValueError, KeyError, TypeError):
        ready, values = False, []
    state["usage"] = {"at": time.time(), "remaining": values, "floors": floors, "ready": ready}
    if time.time() >= deadline:
        return finish(path, state, "PARTIAL", "Deadline reached during telemetry check.")
    headroom = ready and all(v >= min(100, floor + state["usage_buffer"]) for v, floor in zip(values, floors))
    item = active(state) if state["active"] is not None else None
    last = item["reviews"][-1] if item and item["reviews"] else {}
    # A repair answers a review or the final demonstration; every other turn is feature work.
    repair = item is not None and (item["integration"] or last.get("verdict") == "REVISE")
    new_work = state["phase"] in {"SUPERVISE", "IMPLEMENT"} and not state["request"] and not repair
    # The next turn needs its repair estimate, the time the coordinator gave it, or the whole budget.
    turn = (last.get("repair_estimate_seconds") or last.get("turn_seconds") or item["budget_seconds"]) if item else 0
    # Below a floor, or inside the buffer before new work, wait for the meters to reset; never finish early.
    if not ready or (new_work and not headroom and time.time() < state["finish_at"]):
        state["events"].append({"kind": "USAGE_PAUSE", "at": time.time()})
        save(path, state)
        time.sleep(min(poll, max(0, deadline - time.time())))
        if time.time() >= deadline:
            return finish(path, state, "PARTIAL", "Deadline reached while usage was unavailable or below a floor.")
        return "USAGE_PAUSED"
    if state["phase"] in {"SUPERVISE", "IMPLEMENT"} and not state["request"]:
        if new_work and item and (time.time() >= state["finish_at"] or
                                  time.time() + turn > state["finish_at"]):
            item["cause"] = "time"
            shelve(state, "DEFERRED")
            state.update(phase="SUPERVISE",
                         reason="The selected milestone no longer fits after waiting; choose a smaller one.")
        if new_work and time.time() >= state["finish_at"]:
            state.update(phase="FINALIZE", reason="Finishing reserve reached; no new features will start.")
        elif item and not new_work:
            if time.time() + turn > deadline - state["handoff_seconds"]:
                item["cause"] = "time"
                shelve(state, "DEFERRED")
                late = item["integration"] or time.time() >= state["finish_at"]
                state.update(phase="FINALIZE" if late else "SUPERVISE",
                             reason="Repair no longer fits after waiting; checkpoint preserved.")
    try:
        expected = active(state).get("head", active(state)["base"]) if state["active"] is not None else state["accepted_head"]
        # A pending implementation request may already have committed work before a protocol retry.
        pending_build = state["request"] and state["phase"] == "IMPLEMENT"
        check_git(state, clean=not pending_build, expected=None if pending_build else expected)
    except (RuntimeError, subprocess.CalledProcessError) as error:
        return finish(path, state, "OPERATIONAL_FAILURE", f"Git guard failed: {error}; work preserved.")
    if not state["request"]:
        state["request"] = {"id": uuid.uuid4().hex, "stage": state["phase"],
                            "started": time.time(), "head": git("rev-parse", "HEAD")}
    request = {**state["request"], "remaining_seconds": max(0, deadline - time.time()),
               "feature_seconds": max(0, state["finish_at"] - time.time()),
               "recovery": state["reason"] if state["protocol_failures"] else "",
               "result_path": ".atelier/goal/result.json"}
    if state["phase"] == "IMPLEMENT":
        item = active(state)
        verdict = item["reviews"][-1]["verdict"] if item["reviews"] else ""
        request["turn"] = {"number": item["turns"] + 1,
                           "kind": ("probe" if "probe" in item and not item["turns"] else
                                    "repair" if verdict == "REVISE" else "build"),
                           "direction": item.get("direction", ""),
                           "budget_left_seconds": max(0, int(item["budget_seconds"] - item["spent_seconds"]))}
    write_json(path / "request.json", request)
    save(path, state)
    return state["phase"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("deadline", type=int)
    init.add_argument("reserve", type=int)
    init.add_argument("revisions", type=int)
    init.add_argument("buffer", type=int)
    gate = sub.add_parser("route")
    gate.add_argument("run_id")
    gate.add_argument("deadline", type=int)
    gate.add_argument("floors", type=int, nargs=3)
    gate.add_argument("poll", type=int)
    gate.add_argument("weekly", nargs="?", default="Fable")
    sub.add_parser("apply")
    sub.add_parser("check")
    sub.add_parser("session")
    sub.add_parser("stop").add_argument("reason")
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = initialize(args.deadline, args.reserve, args.revisions, args.buffer)
        elif args.command == "route":
            if not all(1 <= n <= 100 for n in args.floors) or not 1 <= args.poll <= 3600:
                raise ValueError("floors must be 1–100 and poll 1–3600")
            if not re.fullmatch(r"auto|[a-f0-9]{32}", args.run_id):
                raise ValueError("invalid run ID")
            if not re.fullmatch(r"[A-Za-z0-9 ._-]{1,40}", args.weekly):
                raise ValueError("invalid Claude weekly meter name")
            result = route(args.run_id, args.deadline, args.floors, args.poll, args.weekly)
        elif args.command == "check":
            try:
                result = check_result()
            except (ValueError, KeyError, TypeError, OSError, RuntimeError, subprocess.CalledProcessError) as error:
                detail = f"missing field {error}" if isinstance(error, KeyError) else error
                print(f"INVALID ({type(error).__name__}): {detail}")
                return 1
        elif args.command == "session":
            result = session()
        elif args.command == "stop":
            result = stop(args.reason)
        else:
            result = apply_result()
        print(result, end="")
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f"automatic-goal: {error}; work preserved", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
