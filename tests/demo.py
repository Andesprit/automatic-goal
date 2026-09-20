"""Generate a saved end-to-end demonstration with scripted agents, not real models.

Run with Atelier's Python from the package root: python tests/demo.py
The printed directory contains disposable repositories, run checkpoints and flow logs.
"""
from pathlib import Path
import sys
import unittest

from test_conduits import NativeLoop


def main():
    case = NativeLoop('test_complete_native_run_with_repair_multiple_commits_and_no_markers')
    try:
        case.setUp()
        case.test_complete_native_run_with_repair_multiple_commits_and_no_markers()
    except unittest.SkipTest as error:
        case.doCleanups()
        sys.exit(str(error))
    except BaseException:
        case.doCleanups()
        raise
    # Intentionally retain the fixture for inspecting the real runner's saved outputs.
    print(f'Scripted demonstration (no real model calls): {case.tmp}')
    print(f'Project: {case.main}')
    print(f'Worktree: {case.wt}')
    print(f'Handoff: {case.wt / ".atelier/goal/handoff.md"}')
    print(f'Monitor: python {Path(__file__).resolve().parents[1] / ".atelier/conduits/goal_loop/scripts/observe.py"} serve --root {case.main}')


if __name__ == '__main__':
    main()
