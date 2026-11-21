"""Plain-language session summaries shared by handoffs, exports, and the monitor."""

import time


LABELS = {
    "ACHIEVED": "Goal achieved", "PARTIAL": "Partly complete", "BLOCKED": "Needs attention",
    "OPERATIONAL_FAILURE": "Run interrupted", "ACTIVE": "In progress",
}
# Why work that was not kept failed: the hypothesis, the approach, or the budget.
CAUSES = {"idea": "Wrong idea", "execution": "Execution failed", "time": "Out of time"}
STATES = {"unmet": "Not met", "partial": "Partly met", "met": "Met"}
STAGES = {"SUPERVISE": "Planning", "IMPLEMENT": "Building", "REVIEW": "Check-ins and reviews",
          "FINALIZE": "Final demonstration"}
FEEDBACK = ('Your verdict steers the next run: `python3 .atelier/conduits/goal_iteration/scripts/memory.py '
            'feedback <id> keep|discard "why"` (use `-` as the id for a general remark).')


def unique(items):
    return list(dict.fromkeys(item.strip() for item in items if isinstance(item, str) and item.strip()))


def sentence(value):
    value = value.strip()
    return value + "." if value and not value.endswith((".", "!", "?")) else value


def duration(seconds):
    if seconds is None:
        return "Time unavailable"
    minutes = int(max(0, seconds) // 60)
    if minutes < 1:
        return f"{int(max(0, seconds))} sec"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes} min"


def amount(value, unit=""):
    if value is None:
        return "not measured"
    return f"{value:g}{unit}" if unit == "%" else f"{value:g} {unit}".strip()


def bet(item, brief):
    """One sentence for the bet a milestone made: the action, the outcome it should move, and why."""
    hypothesis = item.get("hypothesis")
    if not hypothesis:
        return ""
    names = {outcome["id"]: outcome["outcome"] for outcome in (brief or {}).get("scorecard") or []}
    return (f"Bet: {hypothesis['action']} should move \"{names.get(hypothesis['outcome'], hypothesis['outcome'])}\" "
            f"({hypothesis['expected']}), because {hypothesis['because']}")


def moved(item, brief):
    """What the milestone's own outcome read before it started and after its last turn."""
    target = (item.get("hypothesis") or {}).get("outcome")
    outcome = next((o for o in (brief or {}).get("scorecard") or [] if o["id"] == target), None)
    if not outcome:
        return ""
    if outcome["kind"] == "judged":
        now = (item.get("judged") or {}).get(target)
        return f"Judged: {outcome['outcome']}: {STATES[now['state']]} ({now['evidence']})" if now else ""
    readings = item.get("readings") or []
    if not readings:
        return ""
    unit = outcome.get("unit", "")
    return (f"Measured: {outcome['outcome']}: {amount((item.get('before') or {}).get(target), unit)} → "
            f"{amount(readings[-1]['values'].get(target), unit)}")


def scorecard(state):
    """One row per outcome: where it started and where it is now."""
    readings = state.get("scorecard") or {}
    first = (readings.get("baseline") or {}).get("values", {})
    last = (readings.get("final") or readings.get("latest") or {}).get("values", {})
    judged = state.get("judged") or {}
    rows = []
    for item in (state.get("brief") or {}).get("scorecard") or []:
        if item["kind"] == "measured":
            unit = item.get("unit", "")
            rows.append({"outcome": item["outcome"], "before": amount(first.get(item["id"]), unit),
                         "after": amount(last.get(item["id"]), unit),
                         "target": amount(item["target"], unit) if "target" in item else ""})
        else:
            now = judged.get(item["id"])
            rows.append({"outcome": item["outcome"], "before": item["baseline"],
                         "after": f"{STATES[now['state']]}: {now['evidence']}" if now else "Not judged yet",
                         "target": ""})
    return rows


def ledger(state):
    """Where the time went: per kind of stage, per milestone, and what was left unused."""
    stages = {}
    for event in state.get("events", []):
        if "duration_seconds" in event:
            stages[event["stage"]] = stages.get(event["stage"], 0) + event["duration_seconds"]
    lines = [" · ".join(f"{STAGES.get(stage, stage)} {duration(seconds)}" for stage, seconds in stages.items())] if stages else []
    for item in state.get("milestones", []):
        if item.get("spent_seconds"):
            turns = item.get("turns", 0)
            lines.append(f"{item.get('title', 'Untitled improvement')}: {duration(item['spent_seconds'])} "
                         f"over {turns} worker turn{'s' if turns != 1 else ''}")
    unused = (state.get("deadline") or 0) - (state.get("finished") or 0)
    if state.get("finished") and unused >= 60:
        lines.append(f"Unused before the deadline: {duration(unused)}")
    return lines


def summarize(state, now=None):
    """Describe recorded work without treating unfinished or abandoned work as delivered."""
    brief, final = state.get("brief") or {}, state.get("final") or {}
    done, pending, abandoned = [], [], []
    for item in state.get("milestones", []):
        reviews = item.get("reviews") or []
        latest = reviews[-1] if reviews else {}
        implementation = item.get("implementation") or {}
        status = item.get("status", "UNREVIEWED")
        entry = {"title": item.get("title", "Untitled improvement"),
                 "detail": implementation.get("summary") or item.get("benefit", ""),
                 "id": item.get("id", ""), "status": status, "cause": CAUSES.get(item.get("cause"), ""),
                 "extra": [line for line in (bet(item, brief), moved(item, brief)) if line]}
        if item.get("checkpoint_ref"):
            entry["extra"].append(f"Saved at `{item['checkpoint_ref']}`")
        if status == "ACCEPT":
            entry["evidence"] = unique(latest.get("evidence", []))
            notes = unique(latest.get("notes", []))
            if notes:
                entry["extra"].append("Reviewer notes: " + "; ".join(notes))
            if item.get("depends_on"):
                entry["extra"].append("Builds on: " + ", ".join(item["depends_on"]))
            if item.get("base") and item.get("head"):
                entry["extra"].append(f"Take it: `git cherry-pick {item['base'][:12]}..{item['head'][:12]}`")
            done.append(entry)
        elif status in {"ABANDON", "BLOCKED"}:
            reason = (item.get("blocker") if status == "BLOCKED" else latest.get("reason")) or "No reason recorded."
            risk = f"Expected risk: {sentence(item['risk'])}" if item.get("risk") else ""
            entry["detail"] = " ".join(part for part in (sentence(entry["detail"]), risk, f"Not kept: {reason}") if part)
            abandoned.append(entry)
        else:
            entry["detail"] = ("Deferred. " if status == "DEFERRED" else "Not yet accepted. ") + (
                "; ".join(latest.get("findings", [])) or entry["detail"])
            pending.append(entry)
    now = time.time() if now is None else now
    started = state.get("started")
    elapsed = max(0, (state.get("finished") or now) - started) if isinstance(started, (int, float)) else None
    status = state.get("status", "ACTIVE")
    remaining = unique(final.get("remaining", []) + final.get("limitations", []))
    if status in {"BLOCKED", "OPERATIONAL_FAILURE"} or (status == "PARTIAL" and not remaining):
        remaining = unique(remaining + [state.get("reason", "")])
    count = len(done)
    headline = f"{count} improvement{'s' if count != 1 else ''} completed and reviewed."
    if not done:
        headline = "No completed improvements recorded yet." if status == "ACTIVE" else "No completed improvements recorded."
    return {"status": LABELS.get(status, "Outcome not assessed"), "status_code": status,
            "headline": headline, "outcome": brief.get("outcome", ""),
            "elapsed": duration(elapsed), "done": done, "pending": pending,
            "abandoned": abandoned, "remaining": remaining,
            "scorecard": scorecard(state), "time": ledger(state),
            "next_run": unique(final.get("next_run", [])), "questions": unique(final.get("questions", [])),
            "before": final.get("before") or "\n".join(brief.get("baseline", [])),
            "after": final.get("after", ""),
            "evidence": unique(final.get("evidence", []) + [e for item in done for e in item["evidence"]]),
            "next": state.get("priority", "") if status == "ACTIVE" else "",
            "branch": state.get("branch", ""),
            "demonstrated": status == "ACHIEVED"}


def cell(value):
    return " ".join(value.split()).replace("|", "\\|")


def entry_lines(item):
    extra = item.get("extra", []) + ([f"Id: {item['id']}"] if item.get("id") else [])
    return [f"- {line}" for line in extra] + [""] if extra else []


def markdown(summary):
    lines = [f"**{summary['status']} · {summary['elapsed']}**", "", summary["headline"]]
    # The scorecard comes first: it is what the run took the goal to mean, so one line corrects it.
    if summary.get("scorecard"):
        lines += ["", "### Scorecard", "", "| Outcome | Before | After | Target |", "|---|---|---|---|"]
        lines += [f"| {cell(row['outcome'])} | {cell(row['before'])} | {cell(row['after'])} | {cell(row['target'])} |"
                  for row in summary["scorecard"]]
    lines += ["", "### Done", ""]
    for item in summary["done"]:
        lines += [f"**{item['title']}**", "", item["detail"], ""] + entry_lines(item)
    if not summary["done"]:
        lines.append("No reviewed improvements recorded.")
    if summary["pending"] or summary["remaining"]:
        lines += ["", "### Still open", ""]
        lines += [f"- **{item['title']}** — {item['detail']}" for item in summary["pending"]]
        lines += [f"- {item}" for item in summary["remaining"]]
    if summary["next"]:
        lines += ["", f"Current priority: {summary['next']}"]
    if summary["after"]:
        lines += ["", "### Result", "", summary["after"]]
    if not summary["demonstrated"]:
        lines += ["", "The overall goal has not been demonstrated as complete."]
    if summary["evidence"]:
        lines += ["", "### Evidence", "", *[f"- {item}" for item in summary["evidence"]]]
    if summary["abandoned"]:
        lines += ["", "### Explored, not kept", ""]
        for item in summary["abandoned"]:
            title = f"**{item['title']}**" + (f" ({item['cause']})" if item.get("cause") else "")
            lines += [title, "", item["detail"], ""] + entry_lines(item)
    if summary.get("time"):
        lines += ["", "### Where the time went", "", *[f"- {line}" for line in summary["time"]]]
    if summary.get("next_run") or summary.get("questions"):
        lines += ["", "### Next run", "", *[f"- {item}" for item in summary.get("next_run", [])]]
        if summary.get("questions"):
            lines += ["", "Questions for you:", "", *[f"- {item}" for item in summary["questions"]]]
    if summary["done"] or summary["abandoned"] or summary["pending"]:
        lines += ["", FEEDBACK]
    if summary["branch"]:
        lines += ["", f"Branch: `{summary['branch']}`"]
    return "\n".join(lines) + "\n"
