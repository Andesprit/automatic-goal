"""Plain-language session summaries shared by handoffs, exports, and the monitor."""

import time


LABELS = {
    "ACHIEVED": "Goal achieved", "PARTIAL": "Partly complete", "BLOCKED": "Needs attention",
    "OPERATIONAL_FAILURE": "Run interrupted", "ACTIVE": "In progress",
}


def unique(items):
    return list(dict.fromkeys(item.strip() for item in items if isinstance(item, str) and item.strip()))


def duration(seconds):
    if seconds is None:
        return "Time unavailable"
    minutes = int(max(0, seconds) // 60)
    if minutes < 1:
        return f"{int(max(0, seconds))} sec"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes} min"


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
                 "id": item.get("id", ""), "status": status}
        if status == "ACCEPT":
            entry["evidence"] = unique(latest.get("evidence", []))
            done.append(entry)
        elif status == "ABANDON":
            entry["detail"] = latest.get("reason") or "Approach abandoned."
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
            "before": final.get("before") or "\n".join(brief.get("baseline", [])),
            "after": final.get("after", ""),
            "evidence": unique(final.get("evidence", []) + [e for item in done for e in item["evidence"]]),
            "next": state.get("priority", "") if status == "ACTIVE" else "",
            "branch": state.get("branch", ""),
            "demonstrated": status == "ACHIEVED"}


def markdown(summary):
    lines = [f"**{summary['status']} · {summary['elapsed']}**", "", summary["headline"], "", "### Done", ""]
    for item in summary["done"]:
        lines.append(f"- **{item['title']}** — {item['detail']}")
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
        lines += ["", "### Explored, not kept", "",
                  *[f"- **{item['title']}** — {item['detail']}" for item in summary["abandoned"]]]
    if summary["branch"]:
        lines += ["", f"Branch: `{summary['branch']}`"]
    return "\n".join(lines) + "\n"
