# ExpAgent: optional dependency metadata for executable recommendations

## Ownership and scope

This is an ExpAgent-only, backward-compatible contract enhancement. Do not
modify ResAgent, CodingAgent, or ReproAgent.

## Current status

ResAgent now maps ExpAgent action types to operational capabilities itself:
`coding_task` to code modification, `repro_task` and executable `run_task` to
ReproAgent, and scientific analysis/search to ExpAgent. No ExpAgent change is
required for the current closed loop.

One ambiguity remains when a single ExpAgent decision emits a code-changing
action followed by a `run_task` that depends on the newly changed workspace.
The second action has no stable, explicit reference to the first action's output.
ResAgent intentionally fails closed instead of guessing.

## Recommended enhancement

Add optional, generic dependency metadata to recommended actions. Suggested
fields:

```json
{
  "action_id": "patch_training_loop",
  "depends_on": ["patch_training_loop"],
  "project_ref": "current_project"
}
```

Exact names may follow ExpAgent's existing Pydantic style, but the semantics must
be clear:

- `action_id` is unique within one decision.
- `depends_on` references action IDs in the same decision.
- `project_ref` identifies the logical project/workspace without embedding a
  ResAgent directory layout.
- Fields are optional so old callers and stored decisions remain valid.
- ExpAgent remains a scientific advisor; it must not import ResAgent classes or
  choose machine-specific paths.

Update the advisor prompt so a dependent `run_task` uses these fields when its
workspace is produced or modified by another recommendation.

## Tests to add in ExpAgent

1. Existing decisions without the new fields still validate.
2. A code task plus dependent run task serializes and round-trips.
3. Duplicate `action_id` and unknown dependency references are rejected by a
   decision-level validator.
4. Independent actions need no dependency metadata.
5. Mock output follows the same schema.

## Acceptance criteria

- All existing ExpAgent tests pass.
- Stored/legacy decision JSON remains loadable.
- No dependency on ResAgent, CodingAgent, or ReproAgent packages.
- The schema documentation states that dependency IDs are decision-local.

## Verification

```bash
cd /home/cyl/ExpAgent
conda activate ExpAgent
pytest -q
```

