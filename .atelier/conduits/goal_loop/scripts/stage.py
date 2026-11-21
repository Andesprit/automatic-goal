"""Report controller exits to goal_loop while Atelier retries interrupted agent turns."""
import os
from pathlib import Path
import re
import subprocess
import sys
import time


CONTROLLER = Path(__file__).resolve().parents[2] / "goal_iteration/scripts/run.py"
STAGES = {"SUPERVISE", "IMPLEMENT", "REVIEW", "FINALIZE"}


def main(args):
    mode, *args = args
    if mode == "standalone":
        os.execv(sys.executable, [sys.executable, str(CONTROLLER), *args])

    run_id = args[0] if mode == "watch" else mode
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        sys.exit("invalid parent run ID")
    # Keep the failure channel usable even if the checkpoint itself is damaged.
    status = Path(".atelier") / f"{run_id}.stage-status"
    pending = status.with_suffix(".pending-turn")
    if mode == "watch":
        done = Path(".atelier/implementations/runs") / run_id / "loop-done"
        while not done.exists():
            if status.exists():
                message = status.read_text(encoding="utf-8")
                status.unlink()
                sys.exit(message)
            time.sleep(0.5)
        return

    def run(command):
        result = subprocess.run([sys.executable, str(CONTROLLER), *command], capture_output=True)
        sys.stderr.buffer.write(result.stderr)
        if result.returncode:
            reason = result.stderr.decode(errors="replace").strip()
            temp = status.with_suffix(".tmp")
            temp.write_text(f"run.py {command[0]} exited {result.returncode}: {reason}", encoding="utf-8")
            temp.replace(status)
            # The parent fails and cancels this pass before Atelier can start another.
            while True:
                time.sleep(60)
        return result.stdout.decode().strip()

    if args[0] == "route" and pending.exists():
        run(["apply"])
        pending.unlink()
    output = run(args)
    if args[0] == "route" and output in STAGES:
        pending.touch()
    elif args[0] == "apply":
        pending.unlink(missing_ok=True)
    print(output, end="")


if __name__ == "__main__":
    main(sys.argv[1:])
