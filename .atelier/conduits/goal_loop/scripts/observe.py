"""Read-only automatic-goal reports and localhost dashboard. Requires PyYAML."""
import argparse
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import subprocess
import time

import yaml


def read(path, warnings):
    try:
        value = yaml.safe_load(path.read_text())
        if not isinstance(value, dict):
            raise ValueError("expected a mapping")
        return value
    except (OSError, ValueError, yaml.YAMLError) as error:
        warnings.append(f"{path.name}: {error.__class__.__name__}; record unavailable")
        return {}


def worktrees(root):
    try:
        result = subprocess.check_output(
            ["git", "-C", str(root), "worktree", "list", "--porcelain", "-z"])
        return [Path(item[9:].decode()) for item in result.split(b"\0")
                if item.startswith(b"worktree ")]
    except (OSError, subprocess.CalledProcessError):
        return [root]


def decision(project, identifier, modern):
    if not re.fullmatch(r"\d+", identifier):
        return ""
    root = project / ".atelier/implementations"
    key = project.name + "-" + hashlib.sha256(str(project).encode()).hexdigest()[:8]
    # Old worktrees reused numeric IDs. Prefer their own imported namespace.
    candidates = ([root / f"{identifier}.md"] if modern else [
        root / "imported" / key / f"{identifier}.md",
        project / ".atelier/implementations-local-backup" / f"{identifier}.md",
        root / f"{identifier}.md"])
    for path in candidates:
        try:
            return path.read_text(errors="replace")
        except OSError:
            pass
    return ""


def run_snapshot(path, project):
    warnings = []
    progress = read(path / "progress.json", warnings)
    inputs = read(path / "input.yaml", warnings)
    outputs = read(path / "outputs.yaml", warnings) if (path / "outputs.yaml").exists() else {}
    run_id = str(outputs.get("initialize") or "").strip()
    outcome = None
    handoff = ""
    if re.fullmatch(r"[a-f0-9]{32}", run_id):
        record = project / ".atelier/implementations/runs" / run_id
        outcome = read(record / "state.json", warnings) or None
        try:
            handoff = (record / "handoff.md").read_text()
        except OSError:
            warnings.append("Outcome handoff unavailable")
    ticks = []
    for child in (path / "flows").glob("*_goal_iteration"):
        cp = read(child / "progress.json", warnings)
        out = read(child / "outputs.yaml", warnings)
        identifier = str(out.get("next_id") or "").strip()
        verdict = "UNREVIEWED"
        # A reviewer ruling and successful application are different events.
        if out.get("keep") is not None:
            verdict = "KEEP"
        elif out.get("discard") is not None:
            verdict = "DISCARD"
        ticks.append({"id": child.name, "started": cp.get("started_at", ""),
                      "finished": cp.get("finished_at"), "status": cp.get("status", "unknown"),
                      "stages": cp.get("tasks", {}), "current": cp.get("current_tasks", []),
                      "phase": str(out.get("route") or "").strip(),
                      "idea": identifier, "verdict": verdict,
                      "document": decision(project, identifier, "propose" in out) if identifier else "",
                      "commit": str(out.get("verify") or "").strip(),
                      "usage": str(out.get("usage") or "").strip(),
                      "paused": "USAGE_PAUSE" in str(out.get("usage") or "") or out.get("route") == "USAGE_PAUSED"})
    ticks.sort(key=lambda tick: (tick["started"], tick["id"]))
    ideas = [tick for tick in ticks if tick["idea"]]
    usage_tick = next((tick for tick in reversed(ticks) if tick["usage"]), None)
    stage_seconds = {}
    if outcome:
        for event in outcome.get("events", []):
            if "duration_seconds" in event:
                stage = event["stage"]
                stage_seconds[stage] = stage_seconds.get(stage, 0) + event["duration_seconds"]
    counts = {"ticks": len(ticks), "ideas": len(ideas),
              "kept": sum(t["verdict"] == "KEEP" for t in ideas),
              "discarded": sum(t["verdict"] == "DISCARD" for t in ideas),
              "unreviewed": sum(t["verdict"] == "UNREVIEWED" for t in ideas),
              "pauses": sum(t["paused"] for t in ticks)}
    if outcome:
        milestones = outcome.get("milestones", [])
        counts = {"stage passes": len(ticks), "milestones": len(milestones),
                  "accepted": sum(m["status"] == "ACCEPT" for m in milestones),
                  "abandoned": sum(m["status"] == "ABANDON" for m in milestones),
                  "deferred": sum(m["status"] == "DEFERRED" for m in milestones),
                  "repairs": sum(m.get("revisions", 0) for m in milestones),
                  "usage pauses": sum(t["paused"] for t in ticks)}
    return {"id": str(path), "name": path.name, "project": str(project),
            "goal": inputs.get("goal", "Unknown goal"), "hours": inputs.get("hours"),
            "status": progress.get("status", "unknown"), "started": progress.get("started_at"),
            "finished": progress.get("finished_at"), "current": progress.get("current_tasks", []),
            "counts": counts,
            "outcome": outcome, "handoff": handoff, "stage_seconds": stage_seconds,
            "elapsed_seconds": max(0, (outcome.get("finished") or time.time()) - outcome["started"]) if outcome else None,
            "usage": outcome.get("usage_text", "No recorded usage available") if outcome else
                     usage_tick["usage"] if usage_tick else "No recorded usage available",
            "usage_tick_started": (outcome.get("usage") or {}).get("at") if outcome else
                                  usage_tick["started"] if usage_tick else None,
            "ticks": ticks, "warnings": sorted(set(warnings))}


def snapshot(root):
    runs = [run_snapshot(path, project) for project in worktrees(root)
            for path in (project / ".atelier/flows").glob("*_goal_loop")]
    runs.sort(key=lambda run: run["started"] or "", reverse=True)
    return {"observed_at": datetime.now(timezone.utc).isoformat(), "runs": runs}


def report(data):
    lines = ["# Automatic goal report", "", f"Read at {data['observed_at']}", "",
             "Statuses are recorded runner states; they do not prove the process is still alive.", ""]
    for run in data["runs"]:
        lines += [f"## {run['name']}", "", str(run["goal"]), "",
                  f"Recorded status: {run['status']} · {run['started']} → {run['finished'] or 'not finished'}",
                  json.dumps(run["counts"]), "", "### Last recorded usage", "",
                  f"Tick started: {run['usage_tick_started']}", "", run["usage"], ""]
        if run.get("outcome"):
            state = run["outcome"]
            lines += [f"Outcome status: {state['status']}",
                      f"Current priority: {state.get('priority', 'Initial observation')}",
                      f"Elapsed seconds: {int(run['elapsed_seconds'])}",
                      f"Stage seconds (including recovery and waits within a stage): {json.dumps(run['stage_seconds'])}",
                      "", run["handoff"], ""]
        for tick in run["ticks"]:
            if tick["idea"]:
                lines += [f"### Idea {tick['idea']} — {tick['verdict']}", "",
                          f"Commit: {tick['commit'] or 'not recorded'}", "",
                          tick["document"] or "Decision document unavailable.", ""]
        lines += run["warnings"]
    return "\n".join(lines)


def serve(root, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get("Host") not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}:
                self.send_error(403)
                return
            if self.path == "/api/snapshot":
                body, mime = json.dumps(snapshot(root)).encode(), "application/json"
            elif self.path == "/":
                body, mime = Path(__file__).with_name("monitor.html").read_bytes(), "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Goal monitor: http://127.0.0.1:{server.server_port}", flush=True)
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["report", "json", "serve"])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.mode == "serve":
        serve(args.root.resolve(), args.port)
    else:
        data = snapshot(args.root.resolve())
        print(report(data) if args.mode == "report" else json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
