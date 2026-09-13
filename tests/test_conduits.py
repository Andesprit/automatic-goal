"""Runs the real shell from the conduit YAML inside throwaway git worktrees.

Needs git and PyYAML (the flow-atelier interpreter has PyYAML). From the package root:
    uv run --with pyyaml python -m unittest discover -s tests -v
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required; see README.md for the test command")

ROOT = Path(__file__).resolve().parents[1]
CONDUITS = ROOT / ".atelier" / "conduits"
GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
}
NO_ENV = {k: v for k, v in GIT_ENV.items() if k != "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"}
FAKE_AGENT = Path(__file__).resolve().parent / "fake_agent.py"


def load(name):
    tasks = yaml.safe_load((CONDUITS / name / "conduit.yaml").read_text())["tasks"]
    return {k: v for t in tasks for k, v in t.items()}


LOOP, ITER = load("goal_loop"), load("goal_iteration")
LOOP_INPUTS = yaml.safe_load((CONDUITS / "goal_loop" / "conduit.yaml").read_text())["inputs"]
ITER_INPUTS = yaml.safe_load((CONDUITS / "goal_iteration" / "conduit.yaml").read_text())["inputs"]
USAGE_INPUTS = {
    "min_claude_fable_remaining": "50",
    "min_claude_5h_remaining": "50",
    "min_codex_remaining": "30",
    "usage_poll_seconds": "300",
}

# Stands in for scripts/usage.py: logs arguments, optionally sleeps,
# and answers with the next line of verdict.txt.
STUB = """\
import pathlib, sys, time
here = pathlib.Path(__file__).parent
with (here / "calls.log").open("a") as f:
    f.write(" ".join(sys.argv[1:]) + "\\n")
if (here / "sleep.txt").exists():
    time.sleep(float((here / "sleep.txt").read_text()))
verdicts = (here / "verdict.txt").read_text().split()
(here / "verdict.txt").write_text("\\n".join(verdicts[1:] or verdicts))
print("stub meter: 99% remaining (pause below 50% remaining)")
print(verdicts[0])
"""


def render(text, **vars):
    """Fill {{name}} templates; a missing name raises so typos in the YAML surface here."""
    return re.sub(r"\{\{\s*([\w.]+)\s*\}\}", lambda m: vars[m.group(1)], text)


def regex_of(condition):
    return re.fullmatch(r"[\w.]*match\((.*)\)", condition).group(1)


def sh(script, cwd, env=GIT_ENV):
    return subprocess.run(["bash", "-c", script], cwd=cwd, env=env, capture_output=True, text=True)


def acp_python():
    """Find an interpreter for fake_agent.py: Atelier's shebang interpreter, else ours."""
    atelier = shutil.which("atelier")
    if not atelier:
        return None
    first = Path(atelier).read_bytes().split(b"\n", 1)[0].decode(errors="replace")
    candidates = [first[2:].split()[0]] if first.startswith("#!") else []
    for py in candidates + [sys.executable]:
        if subprocess.run([py, "-c", "import acp"], capture_output=True).returncode == 0:
            return py
    return None


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, env=GIT_ENV, capture_output=True, text=True, check=True
    ).stdout.strip()


class Repo(unittest.TestCase):
    """A fresh main checkout plus a dedicated linked worktree on branch 'work'."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.main, self.wt = self.tmp / "main", self.tmp / "wt"
        git(self.tmp, "init", "-q", "-b", "main", "main")
        (self.main / "a.txt").write_text("a\n")
        git(self.main, "add", "a.txt")
        git(self.main, "commit", "-qm", "init")
        git(self.main, "worktree", "add", "-q", str(self.wt), "-b", "work")
        self.base = git(self.wt, "rev-parse", "HEAD")
        self.docs = self.wt / ".atelier" / "implementations"
        self.docs.mkdir(parents=True)

    def commit(self, name="b.txt"):
        (self.wt / name).write_text(name + "\n")
        git(self.wt, "add", name)
        git(self.wt, "commit", "-qm", "change " + name)
        return git(self.wt, "rev-parse", "HEAD")

    def run_task(self, task, cwd=None, env=GIT_ENV, **vars):
        return sh(render(task["task"], **{"conduit_dir": str(CONDUITS / "goal_iteration"), **vars}), cwd or self.wt, env)

    def add_submodule(self):
        sub = self.tmp / "subrepo"
        git(self.tmp, "init", "-q", "-b", "main", "subrepo")
        (sub / "s.txt").write_text("s\n")
        git(sub, "add", "s.txt")
        git(sub, "commit", "-qm", "s")
        git(self.wt, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "sub")
        git(self.wt, "commit", "-qm", "add submodule")

    def verify(self, id="0001"):
        # Upstream steps use printf '%s': Atelier passes outputs without a trailing newline.
        return self.run_task(
            ITER["verify"],
            **{"base.output": self.base, "branch.output": "work", "next_id.output": id},
        )


class Setup(Repo):
    def setup(self, hours="2", cwd=None, env=GIT_ENV):
        return self.run_task(LOOP["setup"], cwd, env, **{"inputs.hours": hours})

    def test_clean_linked_worktree_prints_deadline_and_creates_dir(self):
        shutil.rmtree(self.wt / ".atelier")
        r = self.setup("2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"^\d+$")
        self.assertAlmostEqual(int(r.stdout), time.time() + 2 * 3600, delta=60)
        self.assertTrue(self.docs.is_dir())

    def test_hours_accepts_decimals_with_exact_seconds_and_rejects_bad_values(self):
        for hours, seconds in [("1.5", 5400), ("0.5", 1800), ("0.25", 900), ("1.0001", 3600)]:
            r = self.setup(hours)
            self.assertEqual(r.returncode, 0, (hours, r.stderr))
            self.assertAlmostEqual(int(r.stdout), time.time() + seconds, delta=5, msg=hours)
        for bad in [
            "0",
            "0.0",
            "0.0001",
            "-1",
            "08",
            "abc",
            "721",
            "720.1",
            "99999999999",
            "",
            "1 2",
            " 1",
            "1\n",
            "+1",
            "1\t",
            "1.",
            ".5",
            "1.5.1",
            "1e2",
            "1.00001",
            "1,5",
        ]:
            r = self.setup(bad)
            self.assertNotEqual(r.returncode, 0, bad)
            self.assertIn("hours", r.stderr, bad)
            self.assertEqual(r.stdout, "", bad)
        self.assertEqual(self.setup("720").returncode, 0)

    def test_refuses_to_start_unless_background_tasks_are_disabled(self):
        shutil.rmtree(self.wt / ".atelier")
        for value in [None, "", "0", "true", "yes", " 1"]:
            env = (
                dict(NO_ENV)
                if value is None
                else {**NO_ENV, "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": value}
            )
            r = self.setup(env=env)
            self.assertNotEqual(r.returncode, 0, value)
            self.assertEqual(r.stdout, "", value)
            self.assertIn("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", r.stderr, value)
            self.assertIn("code.claude.com", r.stderr, value)
            self.assertFalse(self.docs.exists(), value)
        self.assertEqual(self.setup().returncode, 0)

    def test_rejects_main_checkout_subdir_and_detached_head(self):
        self.assertIn("linked worktree", self.setup(cwd=self.main).stderr)
        (self.wt / "sub").mkdir()
        self.assertIn("worktree root", self.setup(cwd=self.wt / "sub").stderr)
        (self.wt / "sub").rmdir()
        git(self.wt, "checkout", "-q", "--detach")
        self.assertIn("detached", self.setup().stderr)

    def test_rejects_dirty_tree_and_tracked_atelier_but_allows_untracked_atelier(self):
        (self.docs / "0001.md").write_text("doc\n")
        self.assertEqual(self.setup().returncode, 0)
        (self.wt / "junk.txt").write_text("x")
        self.assertIn("not clean", self.setup().stderr)
        (self.wt / "junk.txt").unlink()
        git(self.wt, "add", ".atelier/implementations/0001.md")
        self.assertIn("tracked files under .atelier", self.setup().stderr)

    def test_rejects_submodules(self):
        self.add_submodule()
        self.assertIn("submodule", self.setup().stderr)
        self.assertIn("submodule", self.setup(cwd=self.wt / "sub").stderr)


class Iteration(Repo):
    def test_deadline_skips_after_deadline(self):
        past = self.run_task(ITER["deadline"], **{"inputs.deadline": "1"})
        self.assertEqual(past.stdout.strip(), "ATELIER_DEADLINE")
        future = self.run_task(
            ITER["deadline"], **{"inputs.deadline": str(int(time.time()) + 3600)}
        )
        self.assertEqual(future.stdout.strip(), "CONTINUE")
        for bad in ["", "abc", "12; echo x", "1" * 13, "1 2", " 1", "1\n", "+1"]:
            r = self.run_task(ITER["deadline"], **{"inputs.deadline": bad})
            self.assertNotEqual(r.returncode, 0, bad)
            self.assertEqual(r.stdout, "", bad)

    def test_base_run_directly_rejects_main_checkout_subdir_detached_head_and_submodule(self):
        self.assertIn("linked worktree", self.run_task(ITER["base"], cwd=self.main).stderr)
        (self.wt / "sub").mkdir()
        self.assertIn("worktree root", self.run_task(ITER["base"], cwd=self.wt / "sub").stderr)
        (self.wt / "sub").rmdir()
        git(self.wt, "checkout", "-q", "--detach")
        self.assertIn("detached", self.run_task(ITER["base"]).stderr)
        git(self.wt, "checkout", "-q", "work")
        self.add_submodule()
        self.assertIn("submodule", self.run_task(ITER["base"]).stderr)
        self.assertIn("submodule", self.run_task(ITER["base"], cwd=self.wt / "sub").stderr)

    def test_branch_with_quote_or_dollar_fails_before_emitting(self):
        for name in [
            "x'$(touch${IFS}marker)'",
            "x$(touch${IFS}marker)",
            "x;touch${IFS}marker",
        ]:  # all git-valid
            git(self.wt, "checkout", "-qb", name)
            r = self.run_task(ITER["branch"])
            self.assertNotEqual(r.returncode, 0, name)
            self.assertEqual(r.stdout, "", name)
            self.assertIn("branch name", r.stderr, name)
            self.assertEqual(git(self.wt, "status", "--porcelain"), "", name)
            git(self.wt, "checkout", "-q", "work")
        self.assertFalse((self.wt / "marker").exists())

    def test_submodule_guard_survives_a_large_index(self):
        # grep -q exits early; pipefail used to turn git ls-files' SIGPIPE into a missed match.
        self.add_submodule()
        big = self.wt / "zz"
        big.mkdir()
        for i in range(4000):
            (big / f"file-{i:04d}-{'x' * 40}.txt").write_text("x")
        git(self.wt, "add", "zz")
        git(self.wt, "commit", "-qm", "many files")
        self.assertIn("submodule", self.run_task(ITER["base"]).stderr)
        self.assertIn("submodule", self.run_task(LOOP["setup"], **{"inputs.hours": "1"}).stderr)

    def test_next_id_is_max_plus_one_across_gaps(self):
        self.assertEqual(self.run_task(ITER["next_id"]).stdout, "0001")
        for f in ["0001.md", "0003.md", "0007-slug.md", "notes.txt", "12.md"]:
            (self.docs / f).write_text("x")
        self.assertEqual(self.run_task(ITER["next_id"]).stdout, "0013")
        shutil.rmtree(self.wt / ".atelier")
        self.assertEqual(self.run_task(ITER["next_id"]).stdout, "0014")

    def test_base_and_branch(self):
        self.assertEqual(self.run_task(ITER["base"]).stdout, self.base)
        self.assertEqual(self.run_task(ITER["branch"]).stdout, "work")
        (self.wt / "junk.txt").write_text("x")
        self.assertIn("not clean", self.run_task(ITER["base"]).stderr)

    def test_base_refuses_agents_unless_background_tasks_are_disabled(self):
        for env in [NO_ENV, {**NO_ENV, "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "true"}]:
            r = self.run_task(ITER["base"], env=env)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(r.stdout, "")
            self.assertIn("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", r.stderr)

    def test_verify_accepts_exactly_one_commit(self):
        (self.docs / "0001.md").write_text("# Idea\n")
        head = self.commit()
        r = self.verify()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, head)

    def test_verify_fails_and_preserves_work_without_commit(self):
        (self.docs / "0001.md").write_text("# Idea\nneeds a human decision\n")
        (self.wt / "leftover.py").write_text("wip\n")
        r = self.verify()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("uncommitted", r.stderr)
        self.assertIn("preserved", r.stderr)
        (self.wt / "leftover.py").unlink()
        self.assertIn("no commit", self.verify().stderr)
        self.assertTrue((self.docs / "0001.md").exists())

    def test_verify_fails_and_preserves_dirty_leftovers_after_commit(self):
        (self.docs / "0001.md").write_text("# Idea\n")
        self.commit()
        (self.wt / "leftover.py").write_text("wip\n")
        self.assertIn("uncommitted", self.verify().stderr)
        self.assertEqual((self.wt / "leftover.py").read_text(), "wip\n")

    def test_verify_rejects_unexpected_graph_branch_doc_and_tracked_atelier(self):
        (self.docs / "0001.md").write_text("# Idea\n")
        self.commit("b.txt")
        self.assertIn("missing", self.verify("0002").stderr)
        self.commit("c.txt")
        self.assertIn("exactly one commit", self.verify().stderr)
        git(self.wt, "reset", "-q", "--hard", self.base)
        git(self.wt, "commit", "-q", "--allow-empty", "-m", "empty")
        self.assertIn("empty", self.verify().stderr)
        git(self.wt, "reset", "-q", "--hard", self.base)
        git(self.wt, "checkout", "-qb", "other")
        self.commit()
        self.assertIn("branch changed", self.verify().stderr)
        git(self.wt, "checkout", "-q", "work")
        self.commit()
        git(self.wt, "add", ".atelier/implementations/0001.md")
        self.assertIn("added to git", self.verify().stderr)


class Verdict(unittest.TestCase):
    """atelier evaluates output.match(regex) like Python re.search (checked against the CLI)."""

    KEEP = regex_of(ITER["keep"]["depends_on"][1])
    DISCARD = regex_of(ITER["discard"]["depends_on"][1])
    VALID = regex_of(ITER["no_verdict"]["depends_on"][0])  # no_verdict is gated on not_match(VALID)

    def route(self, text):
        keep, discard, valid = (
            bool(re.search(rx, text)) for rx in (self.KEEP, self.DISCARD, self.VALID)
        )
        return keep, discard, not valid

    def test_only_the_final_line_counts(self):
        self.assertEqual(
            self.route("I leaned to VERDICT: KEEP, but tests fail.\nVERDICT: DISCARD\n"),
            (False, True, False),
        )
        self.assertEqual(self.route("All good.\nVERDICT: KEEP"), (True, False, False))
        self.assertEqual(self.route("VERDICT: KEEP\n\n"), (True, False, False))
        self.assertEqual(self.route("Nope.\r\nVERDICT: DISCARD\r\n"), (False, True, False))

    def test_missing_or_malformed_verdict_routes_to_failure(self):
        for text in [
            "looks fine",
            "VERDICT: KEEP\nbut wait",
            "verdict: keep",
            "VERDICT: MAYBE",
            "VERDICT: KEEPish",
            "**VERDICT: KEEP**",
            "",
        ]:
            self.assertEqual(self.route(text), (False, False, True), text)


class KeepDiscard(Repo):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.run_task(ITER["next_id"]).returncode, 0)

    def judged(self, head):
        return self.run_task(
            ITER["judged"],
            **{"verify.output": head, "branch.output": "work", "next_id.output": "0001"},
        )

    def test_discard_resets_to_base_cleans_untracked_and_keeps_docs(self):
        (self.docs / "0001.md").write_text("# Idea\n## Verdict\nVERDICT: DISCARD\n")
        self.commit()
        (self.wt / "junk.txt").write_text("x")
        r = self.run_task(ITER["discard"], **{"base.output": self.base, "next_id.output": "0001"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(git(self.wt, "rev-parse", "HEAD"), self.base)
        self.assertFalse((self.wt / "junk.txt").exists())
        self.assertIn("VERDICT: DISCARD", (self.docs / "0001.md").read_text())
        self.assertEqual(git(self.wt, "status", "--porcelain"), "")

    def test_keep_leaves_commit_and_echoes_id(self):
        head = self.commit()
        r = self.run_task(ITER["keep"], **{"next_id.output": "0007"})
        self.assertEqual(r.stdout.strip(), "KEPT 0007")
        self.assertEqual(git(self.wt, "rev-parse", "HEAD"), head)

    def test_judged_guard_catches_a_judge_that_touched_the_tree(self):
        (self.docs / "0001.md").write_text("# Idea\n")
        head = self.commit()
        self.assertEqual(self.judged(head).returncode, 0, self.judged(head).stderr)
        (self.wt / "a.txt").write_text("edited by judge\n")
        self.assertIn("uncommitted", self.judged(head).stderr)
        git(self.wt, "checkout", "-q", "a.txt")
        (self.docs / "0001.md").unlink()
        self.assertIn("missing", self.judged(head).stderr)
        self.commit("c.txt")
        self.assertIn("moved HEAD", self.judged(head).stderr)


class Wiring(unittest.TestCase):
    def test_parent_loop_wiring(self):
        it = LOOP["iterate"]
        self.assertEqual(
            (it["tool"], it["task"], it["repeat"], it["on_exhaust"], it["timeout"]),
            ("tool:conduit", "goal_iteration", 500, "fail", 7200),
        )
        self.assertEqual(
            it["inputs"],
            {
                "goal": "{{inputs.goal}}",
                "deadline": "{{setup.output}}",
                **{k: "{{inputs." + k + "}}" for k in USAGE_INPUTS},
            },
        )
        until = regex_of(it["until"])
        self.assertTrue(re.search(until, "ATELIER_DEADLINE\n"))
        self.assertFalse(re.search(until, "CONTINUE\n"))

    def test_goal_never_reaches_shell_and_agents_are_non_interactive(self):
        for name, task in {**LOOP, **ITER}.items():
            if task["tool"].startswith("harness:"):
                self.assertIs(task["interactive"], False, name)
            else:
                self.assertNotIn("inputs.goal", task["task"], name)

    def test_agent_turns_are_bounded_and_end_on_real_markers_only(self):
        imp, judge = ITER["implement"], ITER["judge"]
        self.assertEqual((imp["repeat"], imp["on_exhaust"]), (3, "fail"))
        self.assertEqual(judge["repeat"], 2)
        self.assertNotIn("on_exhaust", judge)  # exhausting falls through to judged + no_verdict
        for task in (imp, judge):
            self.assertIn("{{loop.previous}}", task["task"])
            self.assertIn("foreground", task["task"])
        commit = regex_of(imp["until"])
        sha = "a" * 40
        for done in [
            f"Done.\nCOMMIT: {sha}",
            f"COMMIT: {sha}\n",
            "needs a human\nCOMMIT: none\n\n",
        ]:
            self.assertTrue(re.search(commit, done), done)
        for early in [
            "I'll wait for the completion notification from the background waiter.",
            f"COMMIT: {sha}\nnext I will",
            f"COMMIT: {sha[:39]}",
            f"COMMIT: {sha}z",
            "COMMIT: None",
            "commit: none",
            f"COMMIT:{sha}",
            "",
        ]:
            self.assertFalse(re.search(commit, early), early)
        self.assertEqual(regex_of(judge["until"]), Verdict.VALID)
        # Per-turn caps share the parent's pass budget: one full implement turn plus one full
        # judge turn must fit; repeats are maxima, not a promise that every retry gets its cap.
        self.assertGreaterEqual(
            LOOP["iterate"]["timeout"],
            imp["timeout"] + judge["timeout"] + ITER["usage"]["timeout"],
        )

    def test_usage_gate_wiring_and_defaults(self):
        ready, pause = (
            regex_of(ITER["base"]["depends_on"][0]),
            regex_of(ITER["pause"]["depends_on"][0]),
        )
        self.assertEqual(ITER["usage"]["depends_on"], ["deadline.output.match(CONTINUE)"])
        for name in ("base", "branch"):
            self.assertEqual(ITER[name]["depends_on"], ITER["base"]["depends_on"], name)
        self.assertEqual(ITER["next_id"]["depends_on"], ["base", "branch"])
        self.assertEqual(ITER["propose"]["tool"], "harness:codex")
        self.assertEqual(ITER["implement"]["depends_on"], ["proposed", "base", "branch", "next_id"])
        self.assertTrue(
            re.search(ready, "claude 5h: 99% remaining (pause below 70% remaining)\nUSAGE_READY\n")
        )
        self.assertTrue(
            re.search(pause, "codex weekly: unavailable (x), limit 40% -> pause\nUSAGE_PAUSE\n")
        )
        for text in ["USAGE_READY\nUSAGE_PAUSE", "USAGE_PAUSED", "ATELIER_DEADLINE", ""]:
            self.assertFalse(re.search(ready, text), text)
        for text in [
            "USAGE_PAUSE\nUSAGE_READY",
            "USAGE_PAUSED",
            "ATELIER_DEADLINE",
            "claude 5h: 1% (limit 70%)\nATELIER_DEADLINE",
        ]:
            self.assertFalse(re.search(pause, text), text)
        loop_in, iter_in = LOOP_INPUTS, ITER_INPUTS
        for k, default in USAGE_INPUTS.items():
            self.assertEqual((loop_in[k]["default"], iter_in[k]["default"]), (default, default), k)
        self.assertGreaterEqual(ITER["pause"]["timeout"], 3600)
        self.assertGreaterEqual(
            LOOP["iterate"]["timeout"], ITER["pause"]["timeout"] + ITER["usage"]["timeout"]
        )

    def test_atelier_check(self):
        if not shutil.which("atelier"):
            self.skipTest("atelier CLI not on PATH")
        for name in ("goal_loop", "goal_iteration"):
            r = subprocess.run(["atelier", "check", name], cwd=ROOT, capture_output=True, text=True)
            self.assertIn(name + " [project] — OK", r.stdout + r.stderr)


class UsageGate(unittest.TestCase):
    """Run the usage and pause shell steps with a stub, without querying any provider."""

    READY = regex_of(ITER["base"]["depends_on"][0])
    PAUSE = regex_of(ITER["pause"]["depends_on"][0])

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.scripts = self.dir / "scripts"
        self.scripts.mkdir()
        (self.scripts / "usage.py").write_text(STUB)
        self.verdicts("USAGE_READY")

    def verdicts(self, *lines):
        (self.scripts / "verdict.txt").write_text("\n".join(lines))

    def calls(self):
        log = self.scripts / "calls.log"
        return log.read_text().splitlines() if log.exists() else []

    def step(self, name, deadline=None, fable="50", five="50", codex="30", poll="300"):
        vars = {
            "inputs.deadline": str(deadline if deadline is not None else int(time.time()) + 3600),
            "inputs.min_claude_fable_remaining": fable,
            "inputs.min_claude_5h_remaining": five,
            "inputs.min_codex_remaining": codex,
            "inputs.usage_poll_seconds": poll,
            "conduit_dir": str(self.dir),
        }
        return sh(render(ITER[name]["task"], **vars), self.dir)

    def tick(self, deadline, poll):
        """Run a pass up to the agent gate; return the output seen by the parent loop."""
        out = self.step("deadline", deadline).stdout
        if "CONTINUE" not in out:
            return out.strip()
        out = self.step("usage", deadline, poll=poll).stdout
        if re.search(self.READY, out):
            return "READY"
        if not re.search(self.PAUSE, out):  # the deadline passed during the read
            self.assertEqual(out.splitlines()[-1], "ATELIER_DEADLINE")
            return "ATELIER_DEADLINE"
        return self.step("pause", deadline, poll=poll).stdout.strip()

    def test_usage_forwards_limits_and_passes_the_verdict_through(self):
        r = self.step("usage")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), ["50 50 30"])
        self.assertRegex(r.stdout, self.READY)
        self.assertNotRegex(r.stdout, self.PAUSE)
        self.verdicts("USAGE_PAUSE")
        r = self.step("usage", fable="1", five="100", codex="99", poll="3600")
        self.assertEqual(self.calls()[-1], "1 100 99")
        self.assertRegex(r.stdout, self.PAUSE)
        self.assertNotRegex(r.stdout, self.READY)
        self.assertIn("stub meter", r.stdout)

    def test_usage_rejects_bad_numbers_before_reading_anything(self):
        for bad in ["0", "101", "-1", "1.5", "abc", "", "1 2", "050", "1000", "50; touch x"]:
            for field in ("fable", "five", "codex"):
                r = self.step("usage", **{field: bad})
                self.assertNotEqual(r.returncode, 0, (field, bad))
                self.assertIn("1 to 100", r.stderr, (field, bad))
        for bad in ["0", "3601", "abc", "", "-5", "30.0", "10000"]:
            r = self.step("usage", poll=bad)
            self.assertNotEqual(r.returncode, 0, bad)
            self.assertIn("1 to 3600", r.stderr, bad)
        self.assertIn("epoch digits", self.step("usage", deadline="12; echo x").stderr)
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.dir / "x").exists())

    def test_deadline_during_the_read_prevents_a_pass(self):
        (self.scripts / "sleep.txt").write_text("1.5")
        r = self.step("usage", deadline=int(time.time()) + 1)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), ["50 50 30"])
        self.assertEqual(r.stdout.splitlines()[-1], "ATELIER_DEADLINE")
        self.assertNotIn("USAGE_READY", r.stdout)
        self.assertNotRegex(r.stdout, self.READY)
        self.assertNotRegex(r.stdout, self.PAUSE)

    def test_pause_sleeps_the_shorter_of_poll_and_time_left(self):
        started = time.monotonic()
        self.assertEqual(self.step("pause", poll="1").stdout.strip(), "USAGE_PAUSED")
        self.assertAlmostEqual(time.monotonic() - started, 1, delta=0.8)
        started = time.monotonic()
        self.assertEqual(
            self.step("pause", deadline=int(time.time()) + 1, poll="3600").stdout.strip(),
            "ATELIER_DEADLINE",
        )
        self.assertLess(time.monotonic() - started, 2.5)
        started = time.monotonic()
        self.assertEqual(
            self.step("pause", deadline=1, poll="3600").stdout.strip(), "ATELIER_DEADLINE"
        )
        self.assertLess(time.monotonic() - started, 1)
        for bad in ["0", "3601", "abc", "1; touch x"]:
            r = self.step("pause", poll=bad)
            self.assertNotEqual(r.returncode, 0, bad)
            self.assertIn("1 to 3600", r.stderr, bad)
        self.assertFalse((self.dir / "x").exists())

    def test_ticks_pause_until_usage_drops_then_resume(self):
        self.verdicts("USAGE_PAUSE", "USAGE_PAUSE", "USAGE_READY")
        deadline = int(time.time()) + 3600
        self.assertEqual(
            [self.tick(deadline, "1") for _ in range(3)], ["USAGE_PAUSED", "USAGE_PAUSED", "READY"]
        )
        self.assertEqual(len(self.calls()), 3)

    def test_deadline_while_paused_ends_the_loop_without_further_reads(self):
        self.verdicts("USAGE_PAUSE")
        deadline = int(time.time()) + 3  # at least one full 1 s pause fits before the deadline
        seen = []
        while not seen or seen[-1] != "ATELIER_DEADLINE":
            self.assertLess(len(seen), 6)
            seen.append(self.tick(deadline, "1"))
        self.assertEqual(set(seen[:-1]), {"USAGE_PAUSED"})
        reads = len(self.calls())
        self.assertEqual(self.tick(deadline, "1"), "ATELIER_DEADLINE")
        self.assertEqual(len(self.calls()), reads)

    def test_atelier_runs_a_paused_pass_and_an_expired_pass_without_agents(self):
        if not shutil.which("atelier"):
            self.skipTest("atelier CLI not on PATH")
        proj = self.dir / "proj"
        conduit = proj / ".atelier" / "conduits" / "goal_iteration"
        shutil.copytree(CONDUITS / "goal_iteration", conduit)
        shutil.copy(self.scripts / "usage.py", conduit / "scripts" / "usage.py")
        self.scripts = conduit / "scripts"
        self.verdicts("USAGE_PAUSE")

        def run(deadline, poll=1):
            r = subprocess.run(
                [
                    "atelier",
                    "run",
                    "goal_iteration",
                    "--hide-steps",
                    "-i",
                    "goal=g",
                    "-i",
                    f"deadline={deadline}",
                    "-i",
                    f"usage_poll_seconds={poll}",
                ],
                cwd=proj,
                capture_output=True,
                text=True,
            )
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            flow = re.search(r"flow_id: (\S+)", r.stdout).group(1)
            return yaml.safe_load((proj / ".atelier" / "flows" / flow / "outputs.yaml").read_text())

        agents = (
            "base",
            "branch",
            "next_id",
            "implement",
            "verify",
            "judge",
            "judged",
            "no_verdict",
            "keep",
            "discard",
        )
        out = run(int(time.time()) + 3600)
        self.assertRegex(out["usage"], self.PAUSE)
        self.assertEqual(out["pause"].strip(), "USAGE_PAUSED")
        self.assertEqual([out[t] for t in agents], [None] * len(agents))
        out = run(1)
        self.assertEqual(out["deadline"].strip(), "ATELIER_DEADLINE")
        self.assertEqual([out[t] for t in ("usage", "pause") + agents], [None] * (len(agents) + 2))
        out = run(
            int(time.time()) + 3, poll=5
        )  # deadline lands inside the pause sleep (atelier startup takes under 3 s)
        self.assertRegex(out["usage"], self.PAUSE)
        self.assertEqual(out["pause"].strip(), "ATELIER_DEADLINE")
        self.assertEqual([out[t] for t in agents], [None] * len(agents))
        self.assertEqual(len(self.calls()), 2)


class NativeLoop(Repo):
    """Run the real goal_iteration conduit with atelier. A scripted fake ACP agent
    (fake_agent.py) plays both claude-code and codex; no model, no network."""

    DOC = (
        "mkdir -p .atelier/implementations && printf '# Idea\\n' > .atelier/implementations/0001.md"
    )
    DRAFT = DOC + " && printf 'b\\n' > b.txt && git add b.txt"
    COMMIT = "git commit -qm 'add b'"
    VERDICT = "printf '## Verdict\\nVERDICT: %s\\n' >> .atelier/implementations/0001.md"

    def setUp(self):
        super().setUp()
        self.py = acp_python()
        if not self.py:
            self.skipTest("atelier CLI with its acp package not available")
        conduit = self.wt / ".atelier" / "conduits" / "goal_iteration"
        shutil.copytree(CONDUITS / "goal_iteration", conduit)
        (conduit / "scripts" / "usage.py").write_text(STUB)
        (conduit / "scripts" / "verdict.txt").write_text("USAGE_READY")
        self.state = self.tmp / "agents"
        self.state.mkdir()
        self.script("planner", ("printf '# Idea\\nA small improvement\\n## Prior decisions\\nNone\\n## Hypothesis\\nCheck b.txt\\n' > .atelier/implementations/0001.md", "IDEA: READY"))

    def script(self, name, *turns):
        turns = [{"run": run, "reply": reply} for run, reply in turns]
        (self.state / f"{name}.json").write_text(json.dumps({"turns": turns}))

    def calls(self, name):
        f = self.state / f"{name}.calls"
        return int(f.read_text()) if f.exists() else 0

    def prompt(self, name, n):
        return (self.state / f"{name}.prompt.{n}").read_text()

    def flow(self, env=GIT_ENV):
        agent = [self.py, str(FAKE_AGENT), str(self.state)]
        r = subprocess.run(
            [
                "atelier",
                "run",
                "goal_iteration",
                "--hide-steps",
                "-i",
                "goal=g",
                "-i",
                f"deadline={int(time.time()) + 3600}",
            ],
            cwd=self.wt,
            env={
                **env,
                "ATELIER_CLAUDE_LAUNCH_CMD": json.dumps(agent + ["claude"]),
                "ATELIER_CODEX_LAUNCH_CMD": json.dumps(agent + ["codex"]),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        flows = list((self.wt / ".atelier" / "flows").glob("*_goal_iteration"))
        self.assertEqual(len(flows), 1, r.stdout + r.stderr)
        out = yaml.safe_load((flows[0] / "outputs.yaml").read_text())
        progress = json.loads((flows[0] / "progress.json").read_text())
        return r.returncode, out, progress, r.stdout + r.stderr

    def assert_one_commit(self):
        head = git(self.wt, "rev-parse", "HEAD")
        self.assertEqual(
            git(self.wt, "rev-list", "--parents", "-n", "1", "HEAD"), f"{head} {self.base}"
        )
        self.assertEqual(git(self.wt, "status", "--porcelain"), "")
        return head

    def test_early_return_then_same_idea_finished_commits_once_and_judges_after(self):
        self.script(
            "claude",
            (
                self.DRAFT,
                "Draft staged. Tests run in the background; "
                "I'll wait for the completion notification.",
            ),
            (self.COMMIT, "Tests: 12 passed, exit 0. Committed.\nCOMMIT: {head}"),
        )
        self.script(
            "codex",
            ("true", "Reading the diff first."),
            (self.VERDICT % "KEEP", "Checks pass.\nVERDICT: KEEP"),
        )
        code, out, progress, log = self.flow()
        self.assertEqual((code, progress["status"]), (0, "completed"), log)
        head = self.assert_one_commit()
        self.assertEqual((self.calls("claude"), self.calls("codex")), (2, 2))
        self.assertEqual(progress["tasks"]["implement"]["iteration"], 2)
        self.assertEqual((out["verify"], out["keep"].strip()), (head, "KEPT 0001"))
        first, second = self.prompt("claude", 1), self.prompt("claude", 2)
        self.assertNotIn("completion notification", first)
        self.assertIn("I'll wait for the completion notification", second)
        for text in (first, second):
            self.assertIn("implementations/0001.md", text)
            self.assertIn(self.base, text)
        self.assertIn(head, self.prompt("codex", 1))  # judged only once the commit existed
        self.assertIn("Reading the diff first.", self.prompt("codex", 2))
        self.assertIn("VERDICT: KEEP", (self.docs / "0001.md").read_text())

    def test_three_turns_without_marker_fail_and_preserve_the_draft(self):
        self.script("claude", (self.DRAFT, "Still going; will report when the suite finishes."))
        self.script("codex", (self.VERDICT % "KEEP", "VERDICT: KEEP"))
        code, out, progress, log = self.flow()
        self.assertNotEqual(code, 0)
        self.assertEqual(progress["status"], "failed")
        self.assertIn("exhausted 3 iterations", log)
        self.assertEqual((self.calls("claude"), self.calls("codex")), (3, 0))
        self.assertEqual(git(self.wt, "rev-parse", "HEAD"), self.base)
        self.assertEqual(
            sorted(git(self.wt, "status", "--porcelain").splitlines()), ["A  b.txt"]
        )
        self.assertTrue((self.docs / "0001.md").exists())
        self.assertEqual([out.get(t) for t in ("verify", "judge", "keep", "discard")], [None] * 4)

    def test_commit_made_but_marker_missing_is_reported_without_a_second_commit(self):
        self.script(
            "claude",
            (
                self.DRAFT + " && " + self.COMMIT,
                "Committed; the suite is running in the background.",
            ),
            ("true", "HEAD is already one commit on top of base; nothing to redo.\nCOMMIT: {head}"),
        )
        self.script("codex", (self.VERDICT % "KEEP", "VERDICT: KEEP"))
        code, out, progress, log = self.flow()
        self.assertEqual((code, progress["status"]), (0, "completed"), log)
        head = self.assert_one_commit()
        self.assertEqual((self.calls("claude"), self.calls("codex")), (2, 1))
        self.assertIn("running in the background", self.prompt("claude", 2))
        self.assertEqual((out["verify"], out["keep"].strip()), (head, "KEPT 0001"))

    def test_explicit_commit_none_is_not_retried_and_verify_fails_preserving_work(self):
        self.script(
            "claude", (self.DRAFT, "This needs a product decision, see the document.\nCOMMIT: none")
        )
        self.script("codex", (self.VERDICT % "KEEP", "VERDICT: KEEP"))
        code, out, progress, log = self.flow()
        self.assertNotEqual(code, 0)
        self.assertEqual(progress["status"], "failed")
        self.assertEqual((self.calls("claude"), self.calls("codex")), (1, 0))
        self.assertIn("preserved", log)
        self.assertEqual(git(self.wt, "rev-parse", "HEAD"), self.base)
        self.assertIn("A  b.txt", git(self.wt, "status", "--porcelain"))
        self.assertEqual([out.get(t) for t in ("verify", "judge", "keep", "discard")], [None] * 4)

    def test_judge_discard_is_final_and_resets_to_base_keeping_the_document(self):
        self.script("claude", (self.DRAFT + " && " + self.COMMIT, "COMMIT: {head}"))
        self.script(
            "codex",
            (self.VERDICT % "DISCARD", "Hypothesis not met.\nVERDICT: DISCARD"),
            ("true", "VERDICT: KEEP"),
        )
        code, out, progress, log = self.flow()
        self.assertEqual((code, progress["status"]), (0, "completed"), log)
        self.assertEqual((self.calls("claude"), self.calls("codex")), (1, 1))
        self.assertEqual(out["discard"].strip(), "DISCARDED 0001")
        self.assertIsNone(out["keep"])
        self.assertEqual(git(self.wt, "rev-parse", "HEAD"), self.base)
        self.assertFalse((self.wt / "b.txt").exists())
        self.assertIn("VERDICT: DISCARD", (self.docs / "0001.md").read_text())

    def test_judge_without_a_verdict_after_two_turns_fails_and_keeps_the_commit_for_review(self):
        self.script("claude", (self.DRAFT + " && " + self.COMMIT, "COMMIT: {head}"))
        self.script("codex", ("true", "I'll run the suite in the background and report back."))
        code, out, progress, log = self.flow()
        self.assertNotEqual(code, 0)
        self.assertEqual(progress["status"], "failed")
        self.assertEqual(self.calls("codex"), 2)
        self.assertIn("no valid final VERDICT", log)
        self.assert_one_commit()
        self.assertEqual([out.get(t) for t in ("keep", "discard")], [None, None])

    def test_agents_never_start_unless_background_tasks_are_disabled(self):
        self.script("claude", (self.DRAFT + " && " + self.COMMIT, "COMMIT: {head}"))
        self.script("codex", (self.VERDICT % "KEEP", "VERDICT: KEEP"))
        code, out, progress, log = self.flow(
            env={**NO_ENV, "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "true"}
        )
        self.assertNotEqual(code, 0)
        self.assertEqual(progress["status"], "failed")
        self.assertIn("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", log)
        self.assertEqual((self.calls("claude"), self.calls("codex")), (0, 0))
        self.assertEqual(git(self.wt, "rev-parse", "HEAD"), self.base)
        self.assertNotIn("implement", out)

    def test_planner_cannot_implement_code_before_claude(self):
        self.script("planner", ("printf changed > a.txt", "IDEA: READY"))
        code, out, progress, log = self.flow()
        self.assertNotEqual(code, 0, log)
        self.assertEqual(self.calls("planner"), 1)
        self.assertEqual(self.calls("claude"), 0)
        self.assertEqual(self.calls("codex"), 0)
        self.assertEqual((self.wt / "a.txt").read_text(), "changed")
        self.assertTrue(self.docs.is_symlink())

    def test_unfinished_proposal_stops_before_implementation_but_is_preserved(self):
        self.script("planner", ("true", "Need more investigation"))
        code, out, progress, log = self.flow()
        self.assertNotEqual(code, 0, log)
        self.assertEqual(self.calls("planner"), 2)
        self.assertEqual(self.calls("claude"), 0)
        self.assertEqual(self.calls("codex"), 0)
        self.assertIn("INCOMPLETE", (self.docs / "0001.md").read_text())


class SharedMemory(Repo):
    def test_history_survives_worktree_removal_and_reaches_a_fresh_worktree(self):
        (self.docs / "0001.md").write_text("# Idea\nOld attempt\n## Verdict\nVERDICT: DISCARD\n")
        first = self.run_task(ITER["next_id"])
        self.assertEqual(first.returncode, 0, first.stderr)
        shared = self.main / ".atelier/implementations"
        self.assertEqual(self.docs.resolve(), shared.resolve())
        self.assertIn("Old attempt", (shared / "index.md").read_text())
        self.assertIn("DISCARD", (shared / "index.md").read_text())
        # An interrupted proposal has a durable reservation and remains visible.
        self.assertIn("INCOMPLETE", (shared / "index.md").read_text())
        git(self.main, "worktree", "remove", "--force", str(self.wt))
        git(self.main, "worktree", "add", "-q", str(self.wt), "-b", "fresh")
        second = self.run_task(ITER["next_id"])
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotEqual(first.stdout, second.stdout)
        self.assertIn("Old attempt", (self.docs / "index.md").read_text())
        self.assertEqual(git(self.main, "status", "--porcelain"), "")
        self.assertEqual(git(self.wt, "status", "--porcelain"), "")

    def test_proposal_guard_blocks_code_changes_and_incomplete_proposals(self):
        identifier = self.run_task(ITER["next_id"]).stdout
        variables = {"base.output": self.base, "branch.output": "work", "next_id.output": identifier}
        self.assertNotEqual(self.run_task(ITER["proposed"], **variables).returncode, 0)
        (self.docs / f"{identifier}.md").write_text("# Idea\nTest\n## Prior decisions\nNone\n## Hypothesis\nPass\n")
        self.assertEqual(self.run_task(ITER["proposed"], **variables).returncode, 0)
        (self.wt / "a.txt").write_text("unauthorized implementation")
        self.assertNotEqual(self.run_task(ITER["proposed"], **variables).returncode, 0)
        self.assertEqual((self.wt / "a.txt").read_text(), "unauthorized implementation")


if __name__ == "__main__":
    unittest.main()
