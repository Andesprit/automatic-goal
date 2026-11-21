# Stage result contract (version 3)

Read `.atelier/goal/request.json` and `state.json`. Write **one JSON object**, without
Markdown fences, to `.atelier/goal/result.json`. Copy `request.json.id` to `request_id`.
The final chat message is informational; no magic completion line is required.
Never edit canonical state, request files, history, or the handoff. The controller
validates results, records each decision, and creates the human-readable handoff.

A protocol recovery retains the same request ID. Inspect the error in request.json,
existing result, actual HEAD, and worktree before repairing the artifact. Do not redo
completed work. A transport failure gets one retry; malformed results get two recovery
attempts. Unexpected Git state stops with OPERATIONAL_FAILURE and preserves all work.

Before you end your turn, run `python3 <conduit_dir>/scripts/run.py check` from the worktree
root. It validates result.json against this request and the actual Git state without changing
anything, and prints `OK` or `INVALID` with the reason. Repair the result until it prints OK.

All text fields below must be nonempty strings. Text lists must be nonempty except
`assumptions`, `findings`, `limitations`, `remaining`, `next_run` and `questions`, which may be empty.
Do not use placeholder evidence: name the command, observation or artifact that supports
the conclusion. Store evidence files in `.atelier/goal/evidence/` and reference their paths.

Write milestone titles as short outcome phrases (about eight words or fewer). The implementation
`summary` appears directly in **Done this session**: write one plain-language paragraph
(typically three to five sentences) explaining **what** changed for the user, **why** it was
worth doing, and **how** the change works. Describe the concrete approach and any meaningful
tradeoff; avoid a chronological work log. Keep commands and test counts in evidence and checks.
On repairs, summarize the complete milestone, including the original change and the repair,
so the report describes all the work rather than just the last fix.

For ABANDON and BLOCKED, the report combines that paragraph with the milestone's `risk` and the
reviewer's `reason` (or the implementer's blocked `reason`).
Use `reason` to explain why the approach was dropped, what observation or evidence led to
that decision, and the resulting limitation or lesson. Do not repeat the implementation
summary or imply that abandoned work was delivered. Each discarded idea should read as one
coherent paragraph covering the attempted change, its intended benefit, its approach, and
why it was not kept. Use recorded facts; never invent detail to reach a sentence count.

## Common check record

Every implementation, review, and final result has a nonempty `checks` array:

```json
{"command":"the exact command run","exit_code":0,"summary":"pass/fail counts and relevant observations","duration_seconds":3.4}
```

READY implementations, ACCEPT reviews, and ACHIEVED outcomes require passing checks.
A check is something you ran that passed. A command that is expected to fail, such as a
baseline reproduction of the original problem, belongs in `evidence`, not in `checks`.
For a manual/browser check, record the reproducible action sequence as `command` and
use exit code 0 for pass or 1 for fail. Include actual elapsed time; no assertion quotas.

## Notes for later agents

Any stage result may add `learned`, a list of at most five notes:

```json
"learned": [{"kind":"pitfall","note":"One sentence a later agent can act on","evidence":"The command, file or observation behind it"}]
```

`kind` is `fact` (how the code or its tools work), `pitfall` (something that cost time and how
to avoid it) or `decision` (a choice later work must respect). The controller adds new notes to
"What we know" at the top of `.atelier/implementations/index.md`, which every agent reads first.
Report only what that section does not already say; verdicts on ideas are recorded for you.
A note taken on a milestone that was later rolled back is kept and marked as unverified.

## SUPERVISE

```json
{
  "request_id": "copy request.id",
  "action": "BUILD",
  "reason": "Why this is the best use of the remaining time",
  "learning": "What inspection or the previous milestone taught us",
  "priority": "The current user-facing priority",
  "brief": {
    "goal_interpretation": "Interpretation of the user's original request",
    "outcome": "The complete observable user experience to improve",
    "baseline": ["Reproducible starting experience, failures and measurements"],
    "scorecard": [
      {"id":"first-run","outcome":"A new user finishes the first run unaided","kind":"judged","how":"Follow the first run as a new user would","baseline":"Stops at the second step"},
      {"id":"scripts","outcome":"Scripts that run from the new layout","kind":"measured","command":"python3 tools/count_working.py","unit":"%","direction":"up","target":95}
    ],
    "preserve": ["Existing behavior or constraint that must remain intact"],
    "assumptions": [],
    "opportunities": [
      {"id":"primary","title":"Opportunity A","benefit":"User benefit","evidence":"Observed evidence","effort":"Estimate with rationale","uncertainty":"Unverified assumptions"},
      {"id":"fallback","title":"Opportunity B","benefit":"User benefit","evidence":"Observed evidence","effort":"Estimate with rationale","uncertainty":"Unverified assumptions"}
    ],
    "primary": "primary",
    "fallback": "fallback",
    "selection_reason": "Why A deserves time more than the alternatives",
    "research": "What competitor or best-in-class research showed, or why it was unnecessary"
  },
  "new_opportunities": [
    {"id":"bolder-idea","title":"Opportunity C","benefit":"User benefit","evidence":"Observed evidence","effort":"Estimate with rationale","uncertainty":"Unverified assumptions"}
  ],
  "milestone": {
    "title": "An independently useful piece of the experience",
    "opportunity": "primary",
    "ambition": "bold",
    "upside": "What the experience gains if this works",
    "risk": "Why it might fail",
    "depends_on": [],
    "benefit": "How this materially contributes to the chosen outcome",
    "hypothesis": {
      "action": "What will be done",
      "outcome": "scripts",
      "expected": "from 12% to about 90%",
      "because": "Why the action should move that outcome",
      "failure_mode": "The most likely way this fails"
    },
    "probe": "A few minutes of work that tests that failure before the budget is spent",
    "acceptance": ["Observable milestone completion criterion"],
    "scope": ["Expected files/components and scope boundaries"],
    "checks": ["Relevant validation, proportional to risk"],
    "budget_seconds": 5400
  }
}
```

The `scorecard` (one to six outcomes, unique ids) is what the run takes the goal to mean; it
leads the owner's report. A `measured` outcome needs `command`, `direction` (`up` or `down`) and
optionally `unit` and a numeric `target`. The controller runs the command with bash from the
worktree root at the start, after every worker turn and at the end; no agent reports these
numbers. The command must exit 0, print one number on its last line, finish within two minutes
and leave the worktree untouched; otherwise the result is refused and anything it changed is
restored. A `judged` outcome needs `how` (how the coordinator will read it by using the result)
and `baseline` (where it starts). At least one outcome must be judged.

A milestone is a bet. `hypothesis` names the action, the scorecard `outcome` id it should move,
what is `expected` (from what to what), `because` of what, and its `failure_mode`. `probe` is
required unless `ambition` is `polish`: the worker's first turn does only the probe and returns
a CHECKPOINT, and the coordinator decides whether the bet goes on. `budget_seconds` covers every
worker turn and check-in of the milestone, counted in agent time.

Actions: BUILD, SIMPLIFY, SWITCH, BLOCKED. The first three require `milestone`. There is no
FINISH: the run works until the finishing reserve, then the controller requests the final
demonstration. BLOCKED requests it early and is only for when no useful work of any kind is
possible without a human. The brief is required initially and otherwise retained. Normally
compare three to five opportunities, with at least two credible alternatives. Add ideas at any
checkpoint with `new_opportunities` (optional; unique new IDs); a milestone can serve one of them
in the same result. When the scorecard's targets are reached, raise the bar with bolder ideas.
A changed brief requires `replan_reason` explaining the new evidence; previous briefs remain in
events, and an outcome that stays keeps its first reading.
`ambition` is `bold`, `normal` or `polish`; `upside` and `risk` are required text. `depends_on`
(optional) lists accepted milestone IDs this one builds on, so a human can pick milestones.
A milestone whose budget does not fit before the finishing reserve is refused and the
supervisor is asked again for a smaller one.

## IMPLEMENT

```json
{"request_id":"copy request.id","status":"READY","head":"full current commit hash","summary":"One paragraph: what changed, why it matters, and how it works","checks":[{"command":"test command","exit_code":0,"summary":"results","duration_seconds":1}],"evidence":["Before/after observation or artifact path"]}
```

Multiple focused commits may extend the active milestone's base. Leave the tree clean and
do not rewrite earlier commits. During a review repair, address `milestones[active].reviews`
findings on top of the current work.

`request.json` carries `turn`: its `number`, its `kind` (`probe`, `build` or `repair`), the
coordinator's last `direction` and `budget_left_seconds`. READY means the whole milestone is
done. To hand the turn back earlier (after the probe, at a natural stopping point, or when a
decision is not yours) return a checkpoint:

```json
{"request_id":"copy request.id","status":"CHECKPOINT","head":"full current commit hash","summary":"What was done or found in this turn","evidence":["Observation or artifact path"],"question":"Optional: the decision the coordinator must make"}
```

A checkpoint needs a clean tree: commit what works, or commit nothing if the turn only
investigated. A probe turn must end with CHECKPOINT or BLOCKED, never READY. The coordinator
answers with a direction and this conversation continues. If the idea proves unworkable or a
genuine blocker stops it, return instead:

```json
{"request_id":"copy request.id","status":"BLOCKED","reason":"What was tried, what was observed, and why it failed or what is missing","cause":"idea"}
```

`cause` is `idea` (the hypothesis was wrong), `execution` (this approach failed) or `time`.

The controller commits any uncommitted attempt, saves it under
`refs/automatic-goal/<run-id>/<milestone-id>`, rolls back only this milestone, logs the reason
and returns to the supervisor. The run continues.

## REVIEW

```json
{"request_id":"copy request.id","head":"full reviewed commit hash","verdict":"REVISE","ready_percent":55,"correctness":"Independent correctness conclusion","value":"Demonstrated contribution to the outcome","evidence":["Evidence supporting both conclusions"],"checks":[{"command":"independent test","exit_code":1,"summary":"failure reproduced","duration_seconds":1}],"findings":["high: Concrete required repair and how to verify completion"],"notes":[],"reason":"Expected benefit justifies remaining repair cost","repair_estimate_seconds":300}
```

Between two worker turns the same stage is a check-in, answered with:

```json
{"request_id":"copy request.id","head":"full reviewed commit hash","verdict":"CONTINUE","reason":"Why the milestone goes on","direction":"What to do next, what to change, and the answer to the worker's question","turn_seconds":1200,"evidence":["What you looked at"]}
```

Verdicts: ACCEPT, REVISE, ABANDON, CONTINUE. CONTINUE hands the worker's conversation back with
`direction`; `turn_seconds` is the time for the next turn and its check-in, and it must fit in
the milestone's remaining budget and before the finishing reserve. When it does not, ACCEPT what
is worth keeping or ABANDON. The other verdicts need the full review described here. Any verdict
may include `judged`, the reviewer's own reading of judged scorecard outcomes:

```json
"judged": [{"id":"first-run","state":"partial","evidence":"What you observed when using the result"}]
```

`state` is `unmet`, `partial` or `met`. ACCEPT requires an entry for every judged outcome, and
something committed. ABANDON requires `cause`: `idea`, `execution` or `time`.

`ready_percent` (integer 0-100, required) is the share of
the change the reviewer would keep exactly as it is. ACCEPT requires `ready_percent` of at least
70, passing checks and no unresolved required `findings`. Medium and low issues go in
`notes` (optional list); the report shows them to the human who picks milestones after the run.
REVISE requires findings and a positive `repair_estimate_seconds` including re-review.
Rate every issue. `critical`: data loss, a security hole, or the milestone's main path does not
work at all. `high`: an acceptance criterion fails, behavior the brief preserves broke, a
required check fails, or someone cannot complete the main path. `medium`: a real defect that
leaves the acceptance criteria working. `low`: polish. Only critical and high issues are
`findings`, and each must start with `critical: ` or `high: `. Medium and low issues are approved
as they are: they go in `notes` (label them `medium: ` or `low: `) and never justify REVISE.
ABANDON explains weak value, a disproven hypothesis, or unjustified repair cost. The runner
records exhausted repair budgets as DEFERRED, not a judgment that the idea lacked value.
Milestone acceptance does not establish overall outcome achievement.

## FINALIZE

```json
{
  "request_id":"copy request.id",
  "head":"full accepted branch HEAD",
  "status":"ACHIEVED",
  "before":"Original user path and measured baseline",
  "after":"Current user path and measured results",
  "evidence":["Reproducible demo commands or UI screenshot/recording paths"],
  "checks":[{"command":"integrated validation","exit_code":0,"summary":"results","duration_seconds":1}],
  "judged":[{"id":"first-run","state":"met","evidence":"What you observed when using the result"}],
  "limitations":[],
  "remaining":[],
  "next_run":["What you would do with another run, most valuable first"],
  "questions":["A question whose answer would change the next run"],
  "reason":"Why these observations demonstrate the outcome"
}
```

Statuses: ACHIEVED, PARTIAL, BLOCKED, REVISE. The controller takes the final measurements.
ACHIEVED requires a `judged` entry with state `met` for every judged outcome and every measured
target reached. Never infer achievement from passing unit tests or accepted commit counts alone.
PARTIAL/BLOCKED explain unproven or unfinished work. `next_run` and `questions` (at most three)
are optional and go into the owner's report.
REVISE includes a `milestone` object with the SUPERVISE shape (including `ambition`, `upside`,
`risk` and `hypothesis`) for a narrow integration repair.
No new feature work is allowed. The controller bounds integration repairs and preserves
handoff time. If time/usage prevents an agent demonstration, the automatic handoff explicitly
records PARTIAL and retained evidence instead of fabricating a successful final assessment.
