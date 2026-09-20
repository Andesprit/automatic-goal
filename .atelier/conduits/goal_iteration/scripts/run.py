"""Durable outcome state and Git guards for automatic-goal (standard library only)."""

import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid

import memory
import session_report


TERMINAL = {"ACHIEVED", "PARTIAL", "BLOCKED", "OPERATIONAL_FAILURE"}


def git(*args):
    return memory.git(Path.cwd(), *args)


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    with os.fdopen(fd, "w") as out:
        out.write(value)
    os.replace(name, path)


def write_json(path, value):
    atomic(path, json.dumps(value, indent=2) + "\n")


def read_json(path):
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def dirty():
    return [line for line in git("status", "--porcelain").splitlines()
            if not line.startswith("?? .atelier/")]


def guard(clean=False):
    if os.environ.get("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS") != "1":
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
    if not path.is_symlink():
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
                  f"Repair rounds: {item['revisions']}"]
        if item.get("checkpoint_ref"):
            lines.append(f"Preserved implementation: {item['checkpoint_ref']}")
        for review in item.get("reviews", []):
            lines += [f"{review['verdict']}: {review['reason']}",
                      f"Correctness: {review['correctness']}", f"Value: {review['value']}",
                      *[f"- {e}" for e in review["evidence"]]]
    if state.get("active") is not None:
        lines += ["", "An unfinished milestone remains. This branch is not reported as ready to merge."]
    lines += ["", "## Success criteria"]
    for criterion in final.get("criteria", []):
        lines.append(f"- [{'x' if criterion.get('met') else ' '}] {criterion['criterion']}: {criterion.get('evidence', 'Not demonstrated')}")
    if not final.get("criteria"):
        lines.extend(f"- [ ] {criterion} — not demonstrated" for criterion in brief.get("success_criteria", []))
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


def initialize(deadline, reserve=20, revisions=2, buffer=5):
    branch = guard(clean=True)
    now = time.time()
    if deadline <= now:
        raise ValueError("deadline must be in the future")
    if not 5 <= reserve <= 50 or not 0 <= revisions <= 5 or not 0 <= buffer <= 25:
        raise ValueError("reserve must be 5–50, max_revisions 0–5, usage buffer 0–25")
    local = Path(".atelier/goal")
    if local.exists() or local.is_symlink():
        _, old = location()
        if old["status"] not in TERMINAL:
            raise RuntimeError("unfinished goal exists; resume that flow or inspect its checkpoint first")
        local.unlink()
    root = memory.attach(Path.cwd().resolve())
    identifier = uuid.uuid4().hex
    path = root / "runs" / identifier
    path.mkdir(parents=True)
    local.symlink_to(path, target_is_directory=True)
    state = {"version": 2, "id": identifier, "worktree": str(Path.cwd().resolve()),
             "branch": branch, "initial_base": git("rev-parse", "HEAD"),
             "accepted_head": git("rev-parse", "HEAD"), "started": now,
             "deadline": deadline, "finish_at": now + (deadline - now) * (1 - reserve / 100),
             "handoff_seconds": min(300, max(1, (deadline - now) * .05)),
             "reserve_percent": reserve, "max_revisions": revisions, "usage_buffer": buffer,
             "phase": "SUPERVISE", "status": "ACTIVE", "brief": None,
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
    if state["active"] is None:
        return
    item = active(state)
    root = Path(".atelier/implementations")
    body = [f"# Idea\n{item['title']}", f"## Goal\n{state['brief']['outcome']}",
            f"## Prior decisions\nRun {state['id']}; opportunity {item['opportunity']}",
            "## Hypothesis\n" + "\n".join(item["acceptance"]),
            f"## Value\n{item['benefit']}", f"## Status\n{item['status']}",
            f"## Checkpoint\n{item['base']}..{item.get('head', 'pending')}",
            "## Record\n" + json.dumps(item, indent=2)]
    if item["status"] in {"ACCEPT", "ABANDON", "DEFERRED"}:
        body.append(f"## Verdict\nVERDICT: {item['status']}")
    atomic(root / f"{item['id']}.md", "\n\n".join(body) + "\n")
    memory.index(root)


def retire(state, status):
    """Preserve rejected/deferred commits under a ref before rolling back this milestone."""
    item = active(state)
    check_git(state, expected=item["head"])
    ref = f"refs/automatic-goal/{state['id']}/{item['id']}"
    git("update-ref", ref, item["head"])
    item.update(status=status, checkpoint_ref=ref)
    journal(state)
    git("reset", "--hard", item["base"])
    # The tree was checked clean; no untracked user files need deleting.
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


def validate_brief(brief):
    for key in ("goal_interpretation", "outcome", "selection_reason", "research"):
        text(brief[key], key)
    for key in ("baseline", "success_criteria", "preserve"):
        texts(brief[key], key)
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
    for key in ("title", "benefit"):
        text(item[key], key)
    for key in ("acceptance", "scope", "checks"):
        texts(item[key], key)
    positive(item["estimated_seconds"], "estimated_seconds")
    if item["opportunity"] not in {o["id"] for o in state["brief"]["opportunities"]}:
        raise ValueError("milestone must serve a compared opportunity")


def start_milestone(state, proposal, integration=False):
    validate_milestone(proposal, state)
    limit = state["deadline"] - state["handoff_seconds"] if integration else state["finish_at"]
    if time.time() + proposal["estimated_seconds"] > limit:
        state.update(phase="FINALIZE", reason="Proposed work cannot finish within its budget.")
        return
    item = {**proposal, "id": memory.reserve(Path(".atelier/implementations")),
            "base": state["accepted_head"], "status": "BUILDING", "revisions": 0,
            "reviews": [], "integration": integration}
    state["milestones"].append(item)
    state.update(active=len(state["milestones"]) - 1, phase="IMPLEMENT")
    journal(state)


def supervise(state, result):
    if state["brief"] is None:
        validate_brief(result["brief"])
        state["brief"] = result["brief"]
    elif "brief" in result and result["brief"] != state["brief"]:
        # An evidence-backed replan is allowed; the earlier brief remains in the event log.
        text(result["replan_reason"], "replan_reason")
        validate_brief(result["brief"])
        state["brief"] = result["brief"]
    for key in ("reason", "learning", "priority"):
        text(result[key], key)
    state.update(reason=result["reason"], priority=result["priority"])
    action = result["action"]
    if action in {"BUILD", "SIMPLIFY", "SWITCH"}:
        start_milestone(state, result["milestone"])
    elif action == "FINISH":
        state["phase"] = "FINALIZE"
    elif action == "BLOCKED":
        state.update(phase="FINALIZE", reason=result["reason"])
    else:
        raise ValueError("unknown supervisor action")


def implemented(state, result):
    if result["status"] == "BLOCKED":
        text(result["reason"], "reason")
        state.update(status="BLOCKED", reason=result["reason"], finished=time.time())
        active(state)["status"] = "BLOCKED"
        return
    if result["status"] != "READY":
        raise ValueError("implementation status must be READY or BLOCKED")
    check_git(state)
    head = verify_range(active(state)["base"])
    git("merge-base", "--is-ancestor", state["request"]["head"], head)
    if result["head"] != head:
        raise ValueError("implementation head does not match Git")
    text(result["summary"], "summary")
    checks(result["checks"], passing=True)
    texts(result["evidence"], "evidence")
    active(state).update(head=head, implementation=result, status="REVIEWING")
    state["phase"] = "REVIEW"


def reviewed(state, result):
    item = active(state)
    if result["head"] != item["head"]:
        raise ValueError("review must identify the reviewed HEAD")
    for key in ("correctness", "value", "reason"):
        text(result[key], key)
    texts(result["evidence"], "evidence")
    texts(result["findings"], "findings", False)
    verdict = result["verdict"]
    if verdict not in {"ACCEPT", "REVISE", "ABANDON"}:
        raise ValueError("verdict must be ACCEPT, REVISE, or ABANDON")
    checks(result["checks"], passing=verdict == "ACCEPT")
    if verdict == "ACCEPT" and result["findings"]:
        raise ValueError("required findings must be resolved before ACCEPT")
    if verdict == "REVISE":
        texts(result["findings"], "repair findings")
        positive(result["repair_estimate_seconds"], "repair_estimate_seconds")
    item["reviews"].append(result)
    state["reason"] = result["reason"]
    if verdict == "ACCEPT":
        item["status"] = "ACCEPT"
        journal(state)
        state.update(accepted_head=item["head"], active=None,
                     phase="FINALIZE" if item["integration"] else "SUPERVISE")
    elif verdict == "ABANDON":
        retire(state, "ABANDON")
        state["phase"] = "FINALIZE" if item["integration"] else "SUPERVISE"
    elif (item["revisions"] >= state["max_revisions"] or
          time.time() + result["repair_estimate_seconds"] > state["deadline"] - state["handoff_seconds"]):
        retire(state, "DEFERRED")
        state.update(phase="FINALIZE", reason="Repair budget exhausted; implementation checkpoint preserved.")
    else:
        item.update(revisions=item["revisions"] + 1, status="REVISING")
        state["phase"] = "IMPLEMENT"


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
    criteria = result.get("criteria", [])
    if not isinstance(criteria, list):
        raise ValueError("criteria must be a list")
    for criterion in criteria:
        text(criterion["criterion"], "criterion")
        text(criterion["evidence"], "criterion evidence")
        if type(criterion["met"]) is not bool:
            raise ValueError("criterion met must be boolean")
    if status == "ACHIEVED":
        expected = state["brief"]["success_criteria"]
        if [c["criterion"] for c in criteria] != expected or any(c["met"] is not True for c in criteria):
            raise ValueError("ACHIEVED requires evidence for every original success criterion")
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


def apply_result():
    path, state = location()
    request = state["request"]
    if not request or state["status"] in TERMINAL:
        # A process can stop after saving the transition but before Atelier saves stdout.
        # Replaying that completed apply must not create another milestone or commit.
        return "GOAL_DONE" if state["status"] in TERMINAL else "CONTINUE"
    stage = request["stage"]
    try:
        check_git(state, clean=stage != "IMPLEMENT", expected=request["head"] if stage != "IMPLEMENT" else None)
        result = read_json(path / "result.json")
        if result["request_id"] != request["id"]:
            raise ValueError("stale result: request_id does not match request.json")
        # Validate and transition a copy so a malformed result cannot partly change canonical state.
        candidate = json.loads(json.dumps(state))
        {"SUPERVISE": supervise, "IMPLEMENT": implemented,
         "REVIEW": reviewed, "FINALIZE": finalized}[stage](candidate, result)
        state = candidate
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
    state["events"].append({"stage": stage, "kind": "RESULT", "at": time.time(),
                            "duration_seconds": time.time() - request["started"], "result": result})
    state.update(request=None, protocol_failures=0)
    journal(state)
    save(path, state)
    return "GOAL_DONE" if state["status"] in TERMINAL else "CONTINUE"


def route(run_id, deadline, floors, poll):
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
        proc = subprocess.run([sys.executable, str(Path(__file__).with_name("usage.py")), *map(str, floors)],
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
    if not ready:
        state["events"].append({"kind": "USAGE_PAUSE", "at": time.time()})
        save(path, state)
        time.sleep(min(poll, max(0, deadline - time.time())))
        if time.time() >= deadline:
            return finish(path, state, "PARTIAL", "Deadline reached while usage was unavailable or below a floor.")
        return "USAGE_PAUSED"
    headroom = all(v >= min(100, floor + state["usage_buffer"]) for v, floor in zip(values, floors))
    if state["phase"] in {"SUPERVISE", "IMPLEMENT"} and not state["request"]:
        item = active(state) if state["active"] is not None else None
        new_work = item is None or (not item["revisions"] and not item["integration"])
        if new_work and (not headroom or time.time() >= state["finish_at"] or
                         (item and time.time() + item["estimated_seconds"] > state["finish_at"])):
            if item:
                item["status"] = "DEFERRED"
                journal(state)
                state["active"] = None
            state.update(phase="FINALIZE", reason="Finishing reserve reached; no new features will start.")
        elif item and not new_work:
            estimate = (item["reviews"][-1]["repair_estimate_seconds"]
                        if item["revisions"] else item["estimated_seconds"])
            if time.time() + estimate > deadline - state["handoff_seconds"]:
                if item.get("head"):
                    retire(state, "DEFERRED")
                else:
                    item["status"] = "DEFERRED"
                    journal(state)
                    state["active"] = None
                state.update(phase="FINALIZE", reason="Repair no longer fits after waiting; checkpoint preserved.")
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
    sub.add_parser("apply")
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = initialize(args.deadline, args.reserve, args.revisions, args.buffer)
        elif args.command == "route":
            if not all(1 <= n <= 100 for n in args.floors) or not 1 <= args.poll <= 3600:
                raise ValueError("floors must be 1–100 and poll 1–3600")
            if not re.fullmatch(r"auto|[a-f0-9]{32}", args.run_id):
                raise ValueError("invalid run ID")
            result = route(args.run_id, args.deadline, args.floors, args.poll)
        else:
            result = apply_result()
        print(result, end="")
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(f"automatic-goal: {error}; work preserved", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
