# automatic_goal

Move a codebase toward a demonstrated user outcome within a time and usage budget.
Codex observes, prioritizes, supervises and reviews; Claude Code implements and repairs.

**Observe → compare opportunities → choose an outcome → build → review and repair → demonstrate.**

An outcome persists across milestones and multiple focused commits. Acceptance of a milestone
means it contributes useful, reliable behavior; only the final user-path demonstration can
establish that the complete outcome was achieved. Finishing early is valid.

## Setup and run

Requirements: Git, Bash, Python 3.11+ as `python3`, Atelier, and the `codex` and `claude-code`
harnesses with their existing logins. Python helpers use the standard library; reports/tests
also need PyYAML. Use a clean, dedicated linked worktree and a normal main checkout with a
`.git` directory. Main checkouts, detached HEAD, submodules and tracked `.atelier/` files are
refused. Never run overlapping flows in one worktree.

```sh
cd /path/to/project
git worktree add ../project-goal -b goal/first-use
cd ../project-goal
atelier add Andesprit/automatic-goal --project
atelier check goal_loop
atelier check goal_iteration
CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1 atelier run goal_loop \
  --input hours=4 \
  --input goal="A new developer can run a useful workflow and recover from a failure unaided"
```

Quote the goal as a single argument. It is only interpolated into agent prompts, never shell.
`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` is required: agent commands and checks must complete
in the foreground before the harness closes the session. Nothing is pushed or deployed.

| Input | Default | Meaning |
|---|---|---|
| `goal` | required | User outcome sought |
| `hours` | required | Wall-clock hours, decimals allowed, positive and at most 720 |
| `finish_reserve_percent` | 20 | Last 5–50% of time reserved for integration, repairs and demonstration |
| `max_revisions` | 2 | 0–5 review repair rounds per milestone; also bounds integration repair milestones |
| `usage_reserve_percent` | 5 | 0–25 extra percentage points above each usage floor required to start features |
| `min_claude_fable_remaining` | 50 | Claude model-specific Fable weekly remaining floor |
| `min_claude_5h_remaining` | 50 | Claude five-hour remaining floor |
| `min_codex_remaining` | 30 | Codex weekly remaining floor |
| `usage_poll_seconds` | 300 | Seconds between usage checks while paused, 1–3600 |

Choose the finishing reserve for the project's risk and run length. The supervisor can finish
sooner but cannot silently lower your reserve or usage floors. Planning time counts against
the same deadline. A milestone estimate includes implementation and review and must fit before
the finishing reserve. A repair must leave a final handoff allowance (5% of the run, capped at
five minutes). These estimates are planning constraints, not guarantees of agent speed.

Usage is checked before every stage. New feature work requires the configured extra headroom;
reviews, repairs and demonstrations may use that buffer, while still respecting the original
floors. Unknown telemetry pauses. The wall clock continues during pauses. At the deadline the
runner writes an honest partial handoff without launching another agent. Already-started agent
turns have a **soft deadline**: they may complete their bounded turn before the next check.
Codex stages have an 1800-second cap, implementation 3600; a transport failure has one retry,
and a stage pass has a 7200-second outer cap. Finishing reserves do not turn these into hard
process deadlines. At most 500 passes, including pauses, run before the loop fails safely.

For unattended use, launch through a persistent job mechanism and record the worktree, branch,
flow ID and output log. Installation does not start a run. The conduits do not schedule themselves.

## How a run makes decisions

1. **Observe and compare.** Codex inspects the actual user experience, records reproducible
   baseline steps, failures or measurements, states assumptions, and defines success and
   preserved behavior. It normally compares three to five opportunities (at least two credible
   alternatives), by user benefit, evidence, effort and uncertainty. It selects a primary and
   fallback and explains why the primary deserves the time. Competitor research resolves a
   named uncertainty when useful; it is not a mandatory ritual or support for a preselected idea.
2. **Maintain the outcome.** The brief, priority and milestones persist across stage sessions.
   Codex consults shared accepted, abandoned and unfinished history, checking which changes
   actually exist on the branch. A changed brief requires an explicit evidence-backed replan;
   the earlier brief remains in the event log. The opportunity list is not regenerated on every
   pass. At checkpoints Codex chooses to build, simplify, switch, finish, or report a blocker.
3. **Build a coherent milestone.** Claude implements across as many files and focused commits
   as the milestone needs. The scope is the smallest independently useful experience that
   advances the outcome, with specific acceptance checks. Small fixes are appropriate when
   they unblock or complete that experience. Multiple commits must extend the recorded base
   without merges or rewritten history. Journal files must never enter Git history.
4. **Review value and correctness separately.** Codex inspects the complete milestone diff and
   independently checks its user benefit and reliability. Checks match risk and scope; broader
   validation happens at integration points. Commands, results and elapsed time are recorded.
   Assertion counts and commit counts are not success metrics.
5. **Repair worthwhile work.** ACCEPT retains the milestone; REVISE sends concrete findings
   and completion criteria back to Claude on the same milestone; ABANDON records weak value,
   a disproven hypothesis or unjustified repair cost. Repair has both a count and time budget.
   Exhaustion becomes DEFERRED, separately from a judgment that the idea was bad. Before rolling
   back an abandoned/deferred implementation, its commits are preserved under
   `refs/automatic-goal/<run-id>/<milestone-id>`. Only that milestone is reset; earlier accepted
   work stays. Dirty or unexpected Git state is preserved for inspection, never cleaned blindly.
6. **Demonstrate the whole experience.** Codex revisits the original baseline on the accepted
   branch, runs integrated checks, records before/after evidence, and reports limitations.
   UI work calls for screenshots or recordings; agent workflows call for reproducible commands.
   Narrow integration repairs can use the finishing reserve, with their own bounded count.
   ACHIEVED requires evidence for every success criterion. PARTIAL and BLOCKED state what remains
   unproven. Neither a passing test suite nor exhausted time implies achievement or adoption.

## Durable state and recovery

`goal_loop` performs setup, creates a run checkpoint and repeats `goal_iteration`. Each iteration
executes **one stage or one usage pause**, not necessarily one idea or commit. Stage selection and
Git guards are handled by `goal_iteration/scripts/run.py`; the agents supply JSON decisions.
The exact contract and examples live in
[stage-results.md](.atelier/conduits/goal_iteration/stage-results.md).

The main checkout holds shared, local-only records:

```text
.atelier/implementations/
  index.md                   # numbered milestone history and links to run handoffs
  0001.md                    # hypothesis, contribution, implementation and reviews
  runs/<run-id>/
    state.json               # canonical brief, milestones, decisions and timestamps
    request.json             # current stage ID, time remaining and recovery instructions
    result.json              # agent-written result; must match this request ID and Git state
    usage.json               # safe telemetry summary, never credentials
    handoff.md               # regenerated at every checkpoint, including incomplete runs
    evidence/                # screenshots, recordings or other demonstration artifacts
```

The worktree's `.atelier/implementations` links to the shared history, and `.atelier/goal`
links to its current run. A second unfinished run in that worktree is refused. Run IDs are
stored in the parent flow's outputs, so a report for an older run does not accidentally show
the newest run's brief. State writes use atomic replacement. Records and abandoned commit refs
survive worktree deletion but are local: Git pushes do not transfer the journals or these refs.
Back them up separately. Raw flow logs remain under their originating worktree's `.atelier/flows`.

Final chat formatting is not a control protocol. A valid stage JSON result is accepted regardless
of the final message. Missing, stale or malformed JSON gets **two bounded recovery attempts**
with the same request ID and actual Git state, so a committed implementation is not repeated.
Exhaustion is OPERATIONAL_FAILURE, not ABANDON. A genuine implementation blocker becomes BLOCKED
and preserves unfinished code. Unexpected branch, HEAD or tracked-file changes also preserve work.
The human-readable handoff exists even when transport failure prevents a final agent response.

Inspect before resuming an interrupted flow:

```sh
git status
atelier status <flow_id>
atelier outputs <flow_id>
atelier logs <flow_id>
cat .atelier/goal/handoff.md
CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1 atelier run --resume <flow_id>
```

Resume retains the original deadline and run checkpoint. Do not start a duplicate loop, reset
unfinished work, or blindly edit canonical state. A terminal BLOCKED/OPERATIONAL_FAILURE run
needs inspection and an explicit decision about its preserved changes before a fresh run.
Flow logs may contain private data; share only relevant redacted excerpts.

`goal_iteration` can also advance a standalone checkpoint with `goal` and `deadline` (epoch
seconds) inputs; its default `run_id=auto` creates a checkpoint if absent. Subsequent direct
calls continue that checkpoint with the same deadline. Normally use `goal_loop` to advance
all stages automatically. Existing legacy numbered history is imported by `memory.py attach`,
with originals retained in `.atelier/implementations-local-backup/`. KEEP/DISCARD records remain
readable and are considered during planning.

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

## Reports and local dashboard

The shared read-only reader covers saved runs across a project's worktrees:

```sh
python .atelier/conduits/goal_loop/scripts/observe.py report --root /path/to/project
python .atelier/conduits/goal_loop/scripts/observe.py report --root /path/to/project --details
python .atelier/conduits/goal_loop/scripts/observe.py json --root /path/to/project
python .atelier/conduits/goal_loop/scripts/observe.py serve --root /path/to/project --port 8765
```

Open `http://127.0.0.1:8765`. The dashboard opens with **Done this session**: every accepted
improvement, the overall result and elapsed time. Each improvement has a plain-language
paragraph explaining what changed, why it matters, and how it works. Discarded ideas retain
the attempted approach and intended benefit, followed by the evidence and reason for dropping them. **Still open**
shows unfinished work and limitations. Evidence, abandoned attempts, usage and stage history are
expandable, so the main view answers what the whole session accomplished without reading logs.
The Markdown export uses the same summary; add `--details` for full review records and technical
data. Newly written `handoff.md` files also lead with this summary and fold the audit trail below.
Accepted milestones are not labeled achieved outcomes. Legacy sessions list their kept changes
without claiming that the overall goal was demonstrated.
Stage duration includes recovery and any waits inside that stage; elapsed wall time includes
all pauses. Usage is the last saved check, and a recorded runner status is not proof of process
liveness. Missing records are identified rather than guessed.

The server stays local, refreshes every three seconds and stops with Ctrl-C. It does not start
agents or change runs. Keep exported reports private when their evidence contains private data.

## Upgrade from one-commit iterations

This changes execution semantics and stage names. Finish or inspect existing runs before updating
an installation; do not resume an old KEEP/DISCARD flow with these new conduits. Install both
`goal_loop` and `goal_iteration` together, preserving any project-specific customizations. The
shared legacy decision history is read without rewriting its verdicts. Old dashboards remain
readable, but accepted-count comparisons across versions do not measure outcome quality.

## Validation

Tests use throwaway Git repositories, local HTTP fixtures, and scripted ACP agents; they do not
call real models or account endpoints. Native tests exercise the actual Atelier runner through
multiple commits, review/repair, protocol recovery without markers, and an early final handoff.
Other checks cover Git guards, time/usage reserves, bounded repair, incomplete handoffs, shared
history, legacy reports, and telemetry parsing. No test asserts that a model's value judgment
is correct; real-run evaluation still needs the demonstrated outcome and human judgment.

```sh
uv run --with pyyaml --with agent-client-protocol python -m unittest discover -s tests -v
atelier check goal_loop
atelier check goal_iteration
atelier plan goal_loop
atelier plan goal_iteration
```

Alternatively use Atelier's Python environment, which already includes PyYAML and ACP.
Native tests require the Atelier CLI and ACP package. Local HTTP tests require permission to
bind localhost sockets.

To generate a saved demonstration through the actual runner with scripted agents:

```sh
python tests/demo.py
```

It prints the disposable project, worktree, handoff and monitor command. This demonstrates
orchestration and recovery mechanics without spending model quota; it does not evaluate an
agent's product judgment. Remove the printed disposable directory when finished inspecting it.

## Input and execution boundaries

Numeric inputs and the generated run ID are interpolated into shell before validation: supply
only trusted numeric values and generated IDs, never untrusted strings. Goal text and agent
results are not interpolated into shell. Stage JSON is parsed and validated against Git state.
Abandon/defer resets are limited to the guarded dedicated worktree, after saving the commit ref.
No reset, cleanup, push, deployment, usage reset, credit purchase or model switch is performed
merely to force an interrupted or usage-paused run to continue.
