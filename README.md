# automatic_goal

Two Flow Atelier conduits that move a codebase toward a goal one judged commit at a time
until a fixed number of hours (decimals allowed) has passed.

- `goal_loop` guards the worktree, computes the deadline and repeats `goal_iteration`.
- `goal_iteration` is one pass: `codex` proposes one small documented idea, then `claude-code` implements it and commits
  it, a mechanical check verifies the commit, `codex` judges it, and the commit is kept or the
  worktree is reset to where the pass started. Each agent is re-prompted with its own previous
  output when a turn ends without its final marker line (`claude-code` up to three turns on the
  same idea, `codex` up to two), because a non-interactive harness turn ends the agent session:
  anything the agent left running in the background is killed, and "I will wait for the
  notification" never happens.
- Before the agents run, `goal_iteration` reads the remaining Claude (Fable weekly, 5-hour) and
  Codex weekly allowances. A meter below its floor, or a meter that cannot be read, makes the
  pass wait instead, and the loop rechecks on its next pass. The wall deadline keeps running
  while paused.

Every attempt leaves a numbered Markdown document in the main checkout's
`.atelier/implementations/`. A worktree's folder is a symlink to that shared directory.
Codex reads its generated `index.md` and relevant detailed records before proposing anything.
The index covers kept, discarded, and incomplete/unreviewed attempts. Kept experiments must
still be checked against the current branch: their commits may not have been merged.

On first attachment, existing worktree records are copied into `imported/<worktree>-<id>/`,
with originals retained as `.atelier/implementations-local-backup/`. Existing main-checkout
archives are indexed recursively. Numbered new documents are reserved with exclusive file
creation so worktrees cannot overwrite each other's idea IDs. Records are written directly
to shared storage; worktree removal does not remove them. Git's local shared `info/exclude`
ignores `/.atelier/`; no ignore change or journal is committed or pushed.

To attach/import an existing worktree without starting agents, from its root run:
```sh
python3 /path/to/automatic_goal/.atelier/conduits/goal_iteration/scripts/memory.py attach
```
A normal main checkout with a `.git` directory must remain available. This is local memory:
it is not replicated to another machine by git. Back it up separately if needed. Agent flow
logs remain in their originating worktree; this shared store contains the decision documents.

## Setup

Run only inside a dedicated linked git worktree on its own branch. The main checkout is refused.

```sh
cd /path/to/your/repo
git worktree add ../myrepo-goal -b goal/cache-cleanup
cd ../myrepo-goal
atelier add Andesprit/automatic-goal --project   # installs goal_loop and goal_iteration into ./.atelier
atelier harness list                            # claude-code and codex must be installed and logged in
```

`python3` (Python 3.11 or newer, standard library only) must be on PATH for the usage check.

`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` must be exported for the `atelier` process. It is an
official Claude Code setting (<https://code.claude.com/docs/en/env-vars>) that disables
background shells, automatic backgrounding of long commands and subagents for that process
only; nothing in your global Claude settings is touched. Without it `claude-code` can start the
project's tests in the background and end its turn to "wait for the notification", and the
harness closes the session at the end of the turn, so the tests are killed and no commit is
made. `setup` and `base` refuse to run any agent unless the variable is exactly `1`.

## Run

```sh
CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1 atelier run goal_loop --input hours=4 --input goal="Reduce cold start time of the CLI without changing its behaviour"
CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1 atelier run goal_loop --input hours=1.5 --input goal="..." --input min_codex_remaining=30 --input usage_poll_seconds=600
```

The worktree must be clean before the run. Untracked run artifacts under `.atelier/` are the
only exception, and nothing under `.atelier/` may be tracked (a discard runs `git reset --hard`
and must not lose the documents). Submodules are not supported. One run per worktree at a time.

## Behaviour

1. `setup` requires `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`, validates `hours` (a number up to
   720 with at most four decimals, e.g. `4` or `1.5`; the deadline is now plus exactly
   `hours * 3600` seconds, truncated to whole seconds, so `1.5` is 5400 seconds), refuses main
   checkouts, submodules, dirty trees, detached heads and tracked `.atelier/` files, creates
   `.atelier/implementations/` and prints the deadline (epoch seconds).
2. `iterate` runs `goal_iteration` up to 500 times, stopping as soon as a pass prints
   `ATELIER_DEADLINE`. Paused passes count too. If 500 passes finish before the deadline the
   flow fails; kept commits stay.
3. In each pass: `deadline` is checked first, then `usage` reads the remaining allowances once
   (see below) and prints `USAGE_READY` or `USAGE_PAUSE`; if the reads outlived the deadline it
   prints `ATELIER_DEADLINE` instead and no agent runs. On `USAGE_PAUSE` the `pause` step sleeps
   `usage_poll_seconds` (capped at the deadline), prints `USAGE_PAUSED` or `ATELIER_DEADLINE`,
   and the pass ends there. On `USAGE_READY`, `base` repeats the environment and worktree
   guards from `setup` (so running `goal_iteration` directly is refused just the same),
   requires a clean tree and records HEAD; `branch` records the branch name, which must match
   `[A-Za-z0-9._/-]+`; `next_id` attaches shared history, reserves a document and refreshes
   the index. `propose` uses Codex to read prior decisions and write the idea, prior-decision
   links, hypothesis and intended scope. It retries up to twice until `IDEA: READY`.
   `proposed` refuses code or Git changes and missing proposal sections.
   Claude Code then implements that specific proposal, preserves its rationale, runs the
   existing tests in the foreground and makes exactly one commit only if they pass. If a human
   decision is needed it writes that in the document and makes no commit. The turn must end
   with `COMMIT: <full hash>` or `COMMIT: none` as its last line. A turn that ends without that
   line (the agent returned early) is not treated as done: the same prompt is sent again, at
   most three times per pass, with the previous turn's output attached and the same document
   id and base commit, and the agent is told to continue the same idea from the real git state:
   a staged draft is finished and committed once; an existing single commit on top of base is
   reported without a second commit; anything else stops with `COMMIT: none`. An explicit
   `COMMIT: none` ends the turns immediately (a human decision or a persistent test failure is
   not retried). Three turns without the line fail the flow with all work left in place.
4. `verify` checks mechanically: same branch, exactly one non-empty commit on top of base, no
   leftover changes, no tracked `.atelier/`, document present. Anything else fails the flow and
   leaves the worktree untouched for manual review.
5. `codex` reviews the commit, runs the tests itself in the foreground, appends a `## Verdict`
   section to the document and ends its message with `VERDICT: KEEP` or `VERDICT: DISCARD` as
   the final line. A turn that ends without a verdict line is sent once more with its previous
   output attached; an explicit `DISCARD` is never retried. A `judged` guard then confirms the
   judge changed nothing in git.
6. `keep` leaves the commit. `discard` runs `git reset --hard <base>` and `git clean -fd`
   excluding `.atelier/`, so documents survive. No valid final verdict line fails the flow.

The deadline is soft: it is checked before usage reads, after they finish, and after each pause.
An implementation that has already started runs through judging to completion. A pass is limited
to 7200 seconds. Inside it, each claude-code turn is capped at 3600 seconds (at most three turns)
and each Codex proposal/review turn at 1800 (at most two per stage). Those are per-turn caps that share the pass budget, so
the turn counts are maxima, not a promise that every retry gets its full cap; a pass that runs out
of budget fails as a whole. A run can overshoot `hours` by up to one pass, plus runtime cleanup
overhead.

### Usage limits

| Input | Default | Meter |
|---|---|---|
| `min_claude_fable_remaining` | 50 | Remaining Claude weekly allowance specific to the Fable model (100 minus its used percent), not the all-model weekly total |
| `min_claude_5h_remaining` | 50 | Remaining Claude 5-hour allowance (100 minus its utilization) |
| `min_codex_remaining` | 30 | Remaining Codex weekly allowance of the `codex` bucket (100 minus its `usedPercent`), not Spark or base-model buckets |
| `usage_poll_seconds` | 300 | Wait between rechecks while paused (1-3600) |

Limits are whole percentages from 1 to 100. Every limit is a floor on what is left: less than
that much remaining pauses, exactly that much remaining still runs. Every meter must be readable
and at or above its floor to run a pass: missing, malformed, stale or ambiguous telemetry pauses
and is retried, never treated as 0. Nothing is bought, reset or switched; the loop waits and
checks again until all three meters allow a pass. A pass that already started always finishes.

`scripts/usage.py` (stdlib only, one read of each provider bounded to about 30 seconds) prints
the percentages and the verdict; tokens, bodies and error messages are never printed.

- Claude: `GET https://api.anthropic.com/api/oauth/usage` with the subscription OAuth token
  Claude Code uses: `CLAUDE_CODE_OAUTH_TOKEN` if set; else, on macOS with the default config
  dir, the Keychain item `Claude Code-credentials` and, only if that item is absent,
  `~/.claude/.credentials.json`; else `<CLAUDE_CONFIG_DIR or ~/.claude>/.credentials.json`. With
  a custom `CLAUDE_CONFIG_DIR` only that directory's file is read, so a setup it cannot resolve
  pauses rather than reading another account. Nothing else is searched. Redirects are refused.
  The clock is read after each response, so a window that reset during a slow read is treated as
  stale and pauses. This endpoint is what the
  installed CLI uses, not a stable public API: a schema or auth change pauses the loop until the
  script is updated. The local `rate-limit-cache.json` is not used (stale, no Fable meter).
- Codex: a private `codex app-server --listen stdio://` process answers
  `account/rateLimits/read` through the existing login (no thread or turn is started, the process
  is stopped afterwards). See <https://learn.chatgpt.com/docs/app-server#6-rate-limits-chatgpt>.

### When the flow fails

Nothing is cleaned up on failure. Inspect the worktree and the last document, fix or discard the
leftovers by hand (`git status`, `git reset --hard`, `git clean -fd -e /.atelier/`), then start a
new `goal_loop` run. The 500-pass and 720-hour ceilings, the per-turn timeouts and the turn
counts (`repeat: 3` on `implement`, `repeat: 2` on `propose` and `judge`) are literals in the YAML; edit them
there if your project needs different ones.

### Trust boundaries

- `hours`, the three usage limits and `usage_poll_seconds` are interpolated into shell before they
  are validated. Pass only numbers you typed yourself.
- The branch name is interpolated into later shell steps, so `branch` refuses names with characters
  outside `[A-Za-z0-9._/-]` before anything else runs.
- `goal` and the agents' outputs are used only inside agent prompts and regex gates, never in shell.
- `discard` runs destructive git commands. That is by design and only sensible in the dedicated
  worktree; do not point the flow at a checkout you care about.

## Tests

The tests run the real shell blocks from the YAML in throwaway git repositories, exercise
`scripts/usage.py` against synthetic fixtures, a local HTTP server and a fake app-server
process (no real account is queried), and run the real `goal_iteration` conduit with `atelier`
against a scripted fake ACP agent (`tests/fake_agent.py`) standing in for both `claude-code`
and `codex`: early returns, staged drafts finished on a later turn, an existing commit reported
without a second one, explicit `COMMIT: none`, and judge retries. They need git and PyYAML;
flow-atelier's own interpreter has PyYAML. The native-loop tests also need the `atelier` CLI on
PATH and its interpreter's `acp` package (found through the CLI's shebang); they skip otherwise.

```sh
uv run --with pyyaml python -m unittest discover -s tests -v
atelier check goal_loop
atelier check goal_iteration
atelier plan goal_loop
atelier plan goal_iteration
```
