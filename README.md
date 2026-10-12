# automatic_goal

Move a codebase toward a demonstrated user outcome within a time and usage budget.
Codex observes, prioritizes, supervises and reviews; Claude Code implements and repairs.

**Observe → write a scorecard → place a bet → probe → build with check-ins → review and repair → demonstrate.**

The director turns the goal into a scorecard and the controller measures it, so progress is a
reading, not a count of accepted milestones. Work is done as a few large bets, each with a
hypothesis, a time budget and a check-in after every worker turn. An outcome persists across
milestones and multiple focused commits. Acceptance of a milestone means it contributes useful,
reliable behavior; only the final demonstration against the scorecard can establish that the
complete outcome was achieved. The report says what moved the scorecard, what did not and why,
and where the time went.

A run works like a person given the whole time slot: it keeps going until the time is used and
never finishes early. It prefers bold ideas; a failed attempt is saved and logged with why it
failed, and the run moves on. Two filters keep weak work out: the reviewer accepts only reliable
work, and you pick which accepted milestones to keep after the run.

## Setup and run

Requirements: Git, Bash, Python 3.11+ as `python3`, Atelier 0.12.0+, and the `codex` and `claude-code`
harnesses with their existing logins. Python helpers use the standard library; reports/tests
also need PyYAML. Use a clean, dedicated linked worktree and a normal main checkout with a
`.git` directory. Main checkouts, detached HEAD, submodules and tracked `.atelier/` files are
refused. Never run overlapping flows in one worktree. On Windows run from Git Bash; without
Developer Mode the links to shared history are created as directory junctions.

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
| `finish_reserve_percent` | 10 | Last 5–50% of time reserved for integration, repairs and demonstration |
| `max_revisions` | 2 | 0–5 review repair rounds per milestone; also bounds integration repair milestones |
| `usage_reserve_percent` | 5 | 0–25 extra percentage points above each usage floor required to start features |
| `min_claude_fable_remaining` | 50 | Claude model-specific Fable weekly remaining floor |
| `min_claude_5h_remaining` | 50 | Claude five-hour remaining floor |
| `min_codex_remaining` | 30 | Codex weekly remaining floor |
| `usage_poll_seconds` | 300 | Seconds between usage checks while paused, 1–3600 |
| `claude_weekly_model` | Fable | Model whose weekly meter the first floor applies to, or `all` |

Choose the finishing reserve for the project's risk and run length. The supervisor cannot
finish early or lower your reserve or usage floors: the final demonstration starts only when
the reserve arrives. A milestone too big for the time left is refused and the supervisor picks
a smaller one. Planning time counts against
the same deadline. A milestone's budget covers all of its worker turns and check-ins, is counted
in agent time, and must fit before the finishing reserve. A repair must leave a final handoff
allowance (5% of the run, capped at five minutes). These budgets are planning constraints, not
guarantees of agent speed.

Usage is checked before every stage. New feature work requires the configured extra headroom;
reviews, repairs and demonstrations may use that buffer, while still respecting the original
floors. Below a floor, or inside the buffer before new work, the run waits for the meters to
reset instead of ending. Unknown telemetry pauses. The wall clock continues during pauses. At the deadline the
runner writes an honest partial handoff without launching another agent. Already-started agent
turns have a **soft deadline**: they may complete their bounded turn before the next check.
Codex stages have an 1800-second cap, implementation 3600; a transport failure has one retry,
and a stage pass has a 7200-second outer cap. Finishing reserves do not turn these into hard
process deadlines. At most 500 passes, including pauses, run before the loop fails safely.

For unattended use, launch through a persistent job mechanism and record the worktree, branch,
flow ID and output log. Installation does not start a run. The conduits do not schedule themselves.

### Choose the director and the worker

By default Codex directs (plans, reviews and demonstrates) and Claude Code builds. Any agent
or model can take either role for one run, without editing the conduits:

```sh
DIRECTOR='claude-code:opus:xhigh'   # <agent>, <agent>:<model> or <agent>:<model>:<effort>
WORKER='codex:gpt-5.6-sol:high'
CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1 atelier run goal_loop \
  --input hours=4 --input goal="..." \
  --agent iterate.supervise="$DIRECTOR" --agent iterate.review="$DIRECTOR" \
  --agent iterate.finalize="$DIRECTOR" --agent iterate.implement="$WORKER"
```

Give the three director steps the same agent, so they share one conversation.
`atelier harness check <agent>` lists the models and efforts an agent offers; the names change
between agent versions. `atelier check goal_loop --recursive --probe` with the same `--agent`
flags confirms every choice is offered, without sending a prompt, and `atelier plan goal_loop`
previews it. The choice is saved with the run, so `--resume` keeps it. The usage floors still read
the Claude and Codex meters, whichever role each has.

## How a run makes decisions

The director (Codex by default) keeps one conversation for the whole run: every plan, review
and the final demonstration is its next turn. The worker (Claude Code by default) keeps one
conversation per milestone, from the first build to the verdict. The saved files remain the source of truth, so a stage whose conversation cannot be
reloaded starts a new one and continues from them.

1. **Observe and compare.** Codex inspects the actual user experience, records reproducible
   baseline steps, failures or measurements, states assumptions, and defines success and
   preserved behavior. It writes the **scorecard**: one to six outcomes that say what progress
   means for this goal. A measured outcome is a read-only command that prints one number; the
   controller runs it at the start, after every worker turn and at the end, so no agent reports
   its own numbers. A judged outcome is read by the director using the result as its user would;
   at least one is required, so a number alone cannot stand for the goal. It normally compares
   three to five opportunities (at least two credible alternatives), ranked by gain: how far the
   scorecard moves if the idea works. Uncertainty is a reason to try, not to skip. It selects a
   primary and fallback and explains why the primary deserves the time. Research into competitors
   and the best products in the space is used when it sharpens an idea or reveals a bolder one.
   Your verdicts on earlier runs, under "What the owner said" in the shared index, weigh on the
   choice.
2. **Maintain the outcome.** The brief, priority and milestones persist across stage sessions.
   Codex consults shared accepted, abandoned and unfinished history, checking which changes
   actually exist on the branch. Every stage can also report up to five notes (a fact about the
   code or its tools, a pitfall, a decision). They are listed under "What we know" at the top of
   the shared index, which every agent reads before exploring, so later agents do not rediscover
   them. A note taken on work that was rolled back stays, marked as unverified. A changed brief requires an explicit evidence-backed replan;
   the earlier brief remains in the event log. New ideas can be added at every checkpoint; when
   the scorecard's targets are reached or only polish is left, Codex raises the bar with bolder
   ones. Parts of the goal nobody has tried come before polish. At checkpoints Codex chooses to
   build, simplify or switch. Each milestone is a **bet**: it states its ambition (bold, normal
   or polish), its upside, why it might fail, which accepted milestones it builds on, and a
   hypothesis: the action, the scorecard outcome it should move, from what to what, and why.
   Its time budget is sized to the expected gain and may span several worker turns, so large
   moves are possible.
3. **Probe, then build with check-ins.** Anything larger than polish starts with a probe: a few
   minutes of work that tests the way the idea is most likely to fail. Claude's first turn does
   only that. Then Claude implements across as many files, commits and turns as the budget
   allows, ending a turn with a checkpoint at a natural stopping point or when a decision is
   not its own. After every turn the controller measures the scorecard, and Codex checks in:
   it looks at the actual work and the readings, answers the question, and either hands the
   conversation back with a direction, accepts what is worth keeping, or closes the bet. A
   failed probe closes it before the budget is spent. Multiple commits must extend the recorded
   base without merges or rewritten history. Journal files must never enter Git history.
4. **Review value and correctness separately.** Codex inspects the complete milestone diff and
   independently checks its user benefit and reliability. Checks match risk and scope; broader
   validation happens at integration points. Commands, results and elapsed time are recorded.
   Assertion counts and commit counts are not success metrics.
5. **Repair worthwhile work.** The reviewer scores how much of the change is ready as is.
   ACCEPT retains the milestone and needs at least 70% ready; small issues become notes for you
   to review after the run. REVISE sends concrete blocking findings and completion criteria back
   to Claude on the same milestone, in the conversation that built it.
   The reviewer rates every issue critical, high, medium or
   low, and only critical and high ones are findings that cost a repair round: data loss, a
   security hole, a main path that does not work, a failing acceptance criterion, a regression
   or a failing required check. Medium and low issues are approved as they are and listed as
   notes, because a repair round costs far more than such a fix; expect accepted milestones to
   carry more notes. The final demonstration uses the same bar for integration repairs.
   A repair and its re-review start from the milestone diff and the findings, not a new
   exploration of the project; ABANDON records weak value, a disproven hypothesis or
   unjustified repair cost, with what was learned. If Claude finds the idea unworkable it returns
   BLOCKED with what it tried and saw. Work that is not kept names its cause: the idea was
   wrong, the execution failed, or the time ran out. Repair has both a count and time budget.
   Exhaustion becomes DEFERRED, separately from a judgment that the idea was bad. None of these end the
   run: the supervisor chooses the next idea. Before rolling back an abandoned, deferred or
   blocked implementation, its commits and any uncommitted attempt are preserved under
   `refs/automatic-goal/<run-id>/<milestone-id>`. Only that milestone is reset; earlier accepted
   work stays. Dirty or unexpected Git state is preserved for inspection, never cleaned blindly.
6. **Demonstrate the whole experience.** Codex revisits the original baseline on the accepted
   branch, runs integrated checks, records before/after evidence, and reports limitations.
   UI work calls for screenshots or recordings; agent workflows call for reproducible commands.
   Narrow integration repairs can use the finishing reserve, with their own bounded count.
   The controller takes the final measurements and Codex reads every judged outcome itself.
   ACHIEVED requires every judged outcome met and every measured target reached. PARTIAL and
   BLOCKED state what remains unproven. Neither a passing test suite nor exhausted time implies
   achievement or adoption.
7. **You pick, and you steer the next run.** Accepted milestones stack on the goal branch. The
   report opens with the scorecard, before and after, so you can see what the run took the goal
   to mean. It lists each accepted milestone with its bet, what its outcome read before and
   after, the reviewer's notes, what it builds on, and a `git cherry-pick <base>..<head>`
   command to take only that milestone. Ideas that were not kept list their cause, their saved
   ref and why. It ends with where the time went, what the director would do with another run,
   and at most three questions for you. Record your verdict so the next run learns your taste:

   ```sh
   python3 .atelier/conduits/goal_iteration/scripts/memory.py feedback 0016 discard "I wanted labels before a model"
   python3 .atelier/conduits/goal_iteration/scripts/memory.py feedback - note "Finish the layout before any new experiment"
   ```

## Durable state and recovery

`goal_loop` performs setup, creates a run checkpoint and repeats `goal_iteration`. Each iteration
executes **one stage or one usage pause**, not necessarily one idea or commit. Stage selection and
Git guards are handled by `goal_iteration/scripts/run.py`; the agents supply JSON decisions.
The exact contract and examples live in
[stage-results.md](.atelier/conduits/goal_iteration/stage-results.md).

The main checkout holds shared, local-only records:

```text
.atelier/implementations/
  index.md                   # what agents learned, milestone history and links to run handoffs
  notes.jsonl                # the notes behind "What we know"; delete a line to retire a note
  feedback.jsonl             # your verdicts behind "What the owner said"; delete a line to retire one
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
On Windows, Git deletes through directory links, so a plain `git worktree remove` would erase the
shared history. The run therefore locks its worktree; before removing it, run
`python3 .atelier/conduits/goal_iteration/scripts/memory.py detach` in it, which removes only
the links and unlocks it. Never force past the lock with `--force --force`.
Back them up separately. Raw flow logs remain under their originating worktree's `.atelier/flows`.

Final chat formatting is not a control protocol. A valid stage JSON result is accepted regardless
of the final message. Each agent is told to run `run.py check` before it ends its turn: it validates
the result against the request and the actual Git state without changing anything, so a malformed
result or a dirty tree is repaired in the same session instead of costing another stage. Results
are read in any Unicode encoding, including the byte-order mark PowerShell writes. Missing, stale or malformed JSON gets **two bounded recovery attempts**
with the same request ID and actual Git state, so a committed implementation is not repeated.
Exhaustion is OPERATIONAL_FAILURE, not ABANDON. An implementation blocker marks only that
milestone BLOCKED, saves its code under a ref and returns to the supervisor. Unexpected branch,
HEAD or tracked-file changes stop the run and preserve work.
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

To close a run before its deadline, run this in its worktree:

```sh
python3 .atelier/conduits/goal_iteration/scripts/run.py stop "why you are stopping"
```

The run is recorded as PARTIAL with your reason, the handoff is written, and the loop ends at its
next pass. Nothing is reset: an unfinished milestone and any commit you made stay on the branch.

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
| `claude_weekly_model` | Fable | Display name of the model whose weekly meter `min_claude_fable_remaining` applies to (for example `Opus`), or `all` for the all-model weekly total |

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
  script is updated. An expired saved login (http 401) is renewed once by asking Claude Code
  for a one-word reply on haiku, then read again; `CLAUDE_CODE_OAUTH_TOKEN` is never refreshed.
  The local `rate-limit-cache.json` is not used (stale, no Fable meter).
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

Review findings must now start with `critical: ` or `high: `; finish or stop a run in progress
before updating.

This changes execution semantics and stage names. Finish or inspect existing runs before updating
an installation; do not resume an old KEEP/DISCARD flow with these new conduits. Install both
`goal_loop` and `goal_iteration` together, preserving any project-specific customizations. The
shared legacy decision history is read without rewriting its verdicts. Old dashboards remain
readable, but accepted-count comparisons across versions do not measure outcome quality.

## Validation

Tests use throwaway Git repositories, local HTTP fixtures, and scripted ACP agents; they do not
call real models or account endpoints. Native tests exercise the actual Atelier runner through
multiple commits, review/repair, protocol recovery without markers, and the final handoff.
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
