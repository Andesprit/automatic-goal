"""Stream controller diagnostics and report script failures to Atelier."""
from pathlib import Path
import re
import subprocess
import sys


CONTROLLER = Path(__file__).resolve().parents[2] / "goal_iteration/scripts/run.py"


def main(args):
    mode, *args = args
    if mode == "standalone":
        sys.exit(subprocess.call([sys.executable, str(CONTROLLER), *args]))

    if not re.fullmatch(r"[a-f0-9]{32}", mode):
        sys.exit("invalid parent run ID")
    with subprocess.Popen([sys.executable, str(CONTROLLER), *args], stderr=subprocess.PIPE) as controller:
        tail = b""
        while chunk := controller.stderr.read1(8192):
            sys.stderr.buffer.write(chunk)
            sys.stderr.buffer.flush()
            tail = (tail + chunk)[-4096:]
        code = controller.wait()
    if code:
        reason = tail.decode(errors="replace").strip()
        print(f"\nrun.py {args[0]} exited {code}: {reason}", file=sys.stderr)
        sys.exit(code)


if __name__ == "__main__":
    main(sys.argv[1:])
