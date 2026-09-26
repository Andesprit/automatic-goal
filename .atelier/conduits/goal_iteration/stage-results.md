# Stage result contract (version 2)

Read `.atelier/goal/request.json` and `state.json`. Write **one JSON object**, without
Markdown fences, to `.atelier/goal/result.json`. Copy `request.json.id` to `request_id`.
The final chat message is informational; no magic completion line is required.
Never edit canonical state, request files, history, or the handoff. The controller
validates results, records each decision, and creates the human-readable handoff.

A protocol recovery retains the same request ID. Inspect the error in request.json,
existing result, actual HEAD, and worktree before repairing the artifact. Do not redo
completed work. A transport failure gets one retry; malformed results get two recovery
attempts. Unexpected Git state stops with OPERATIONAL_FAILURE and preserves all work.

All text fields below must be nonempty strings. Text lists must be nonempty except
`assumptions`, `findings`, `limitations`, and `remaining`, which may be empty.
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
For a manual/browser check, record the reproducible action sequence as `command` and
use exit code 0 for pass or 1 for fail. Include actual elapsed time; no assertion quotas.

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
    "success_criteria": ["Observable criterion to demonstrate at the end"],
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
    "acceptance": ["Observable milestone completion criterion"],
    "scope": ["Expected files/components and scope boundaries"],
    "checks": ["Relevant validation, proportional to risk"],
    "estimated_seconds": 900
  }
}
```

Actions: BUILD, SIMPLIFY, SWITCH, BLOCKED. The first three require `milestone`. There is no
FINISH: the run works until the finishing reserve, then the controller requests the final
demonstration. BLOCKED requests it early and is only for when no useful work of any kind is
possible without a human. The brief is required initially and otherwise retained. Normally
compare three to five opportunities, with at least two credible alternatives. Add ideas at any
checkpoint with `new_opportunities` (optional; unique new IDs); a milestone can serve one of them
in the same result. When the brief's criteria are met, raise the bar with bolder ideas. A changed
brief requires `replan_reason` explaining the new evidence; previous briefs remain in events.
`ambition` is `bold`, `normal` or `polish`; `upside` and `risk` are required text. `depends_on`
(optional) lists accepted milestone IDs this one builds on, so a human can pick milestones.
The estimate includes implementation **and review**. A milestone that cannot finish before the
finishing reserve is refused and the supervisor is asked again for a smaller one.

## IMPLEMENT

```json
{"request_id":"copy request.id","status":"READY","head":"full current commit hash","summary":"One paragraph: what changed, why it matters, and how it works","checks":[{"command":"test command","exit_code":0,"summary":"results","duration_seconds":1}],"evidence":["Before/after observation or artifact path"]}
```

Multiple focused commits may extend the active milestone's base. Leave the tree clean and
do not rewrite earlier commits. During a review repair, address `milestones[active].reviews`
findings on top of the current work. If the idea proves unworkable or a genuine blocker stops
it, return instead:

```json
{"request_id":"copy request.id","status":"BLOCKED","reason":"What was tried, what was observed, and why it failed or what is missing"}
```

The controller commits any uncommitted attempt, saves it under
`refs/automatic-goal/<run-id>/<milestone-id>`, rolls back only this milestone, logs the reason
and returns to the supervisor. The run continues.

## REVIEW

```json
{"request_id":"copy request.id","head":"full reviewed commit hash","verdict":"REVISE","ready_percent":55,"correctness":"Independent correctness conclusion","value":"Demonstrated contribution to the outcome","evidence":["Evidence supporting both conclusions"],"checks":[{"command":"independent test","exit_code":1,"summary":"failure reproduced","duration_seconds":1}],"findings":["Concrete required repair and how to verify completion"],"notes":[],"reason":"Expected benefit justifies remaining repair cost","repair_estimate_seconds":300}
```

Verdicts: ACCEPT, REVISE, ABANDON. `ready_percent` (integer 0-100, required) is the share of
the change the reviewer would keep exactly as it is. ACCEPT requires `ready_percent` of at least
70, passing checks and no unresolved required `findings`. Small non-blocking issues go in
`notes` (optional list); the report shows them to the human who picks milestones after the run.
REVISE requires findings and a positive `repair_estimate_seconds` including re-review.
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
  "criteria":[{"criterion":"Exact success_criteria entry from the brief","met":true,"evidence":"Direct evidence for this criterion"}],
  "limitations":[],
  "remaining":[],
  "reason":"Why these observations demonstrate the outcome"
}
```

Statuses: ACHIEVED, PARTIAL, BLOCKED, REVISE. ACHIEVED requires every brief success criterion
in the same order, `met: true`, and evidence. Never infer achievement from passing unit tests
or accepted commit counts alone. PARTIAL/BLOCKED explain unproven or unfinished work.
REVISE includes a `milestone` object with the SUPERVISE shape (including `ambition`, `upside`
and `risk`) for a narrow integration repair.
No new feature work is allowed. The controller bounds integration repairs and preserves
handoff time. If time/usage prevents an agent demonstration, the automatic handoff explicitly
records PARTIAL and retained evidence instead of fabricating a successful final assessment.
