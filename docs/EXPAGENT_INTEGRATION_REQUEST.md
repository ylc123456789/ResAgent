# ExpAgent Integration Request: Incomplete SuggestedPlan Fields

**Date**: 2026-08-06
**From**: ResAgent development session
**Priority**: Medium (LLM Planner workaround exists but wastes calls)

---

## Problem

When ResAgent calls ExpAgent via `advise()`, the returned `ScientificDecision.recommended_actions`
contain `SuggestedPlan` objects whose operational fields are mostly empty.

The `SuggestedPlan` Pydantic model has 17 fields. ExpAgent's LLM typically fills only 2–3 of them:

```
Filled (2-3):   kind, task_goal, (sometimes constraints)
Empty (14-15):  repo_path, verify_commands, expected_artifacts,
                paper_url, repo_url, experiment_goal, ...
```

**For coding_task actions**, the three most critical fields for downstream execution
are all empty: `repo_path`, `constraints`, `verify_commands`.

**For repro_task actions**, the critical fields (`paper_url`, `repo_url`, `experiment_goal`)
are correctly filled — this path works well.

---

## Evidence: Test Artifacts

### Test 1: CodingAgent task (ExpAgent → ResAgent → CodingAgent)

**Research goal**: Add per-epoch loss logging to /tmp/ca2_fvz36l2e/proj/train.py

**ExpAgent output**:
```
/tmp/ca2_fvz36l2e/res-20260806-380419/expagent/decision_001/scientific_decision.json
```
- Filled fields: `kind`, `task_goal` (2 of 17)
- `repo_path`: "" (EMPTY — even though path was in the research goal)
- `constraints`: [] (EMPTY)
- `verify_commands`: [] (EMPTY)

**Resulting ResAgent task** (task_001 — NEVER EXECUTED because repo_path is empty):
```
/tmp/ca2_fvz36l2e/res-20260806-380419/state.json  → tasks[0].input
```

**LLM Planner had to synthesize task_002** with repo_path extracted from the
research goal text. This task succeeded:
```
/tmp/ca2_fvz36l2e/res-20260806-380419/state.json  → tasks[1].input
/tmp/ca2_fvz36l2e/res-20260806-380419/codingagent/code_002/logs/action_*.json
(4 actions: read_file → replace_text → run_command → finish)
```

### Test 2: ReproAgent task (ExpAgent → ResAgent → ReproAgent)

**Research goal**: Reproduce pytorch/examples MNIST baseline

**ExpAgent output**:
```
/tmp/resagent_repro_0q3t6vqj/res-20260806-31c782/expagent/decision_001/scientific_decision.json
```
- Filled fields: `kind`, `paper_url`, `repo_url`, `experiment_goal`, `compute_budget`, `expected_metrics` (6 of 17)
- All three critical fields present and correct

**Resulting ResAgent task** (directly executable):
```
/tmp/resagent_repro_0q3t6vqj/res-20260806-31c782/state.json  → tasks[0].input
```

### Test 3: Another ExpAgent call showing more constraints filled but still no repo_path

```
/tmp/resagent_fulltest_6sruxan0/res-20260806-1d60a2/expagent/decision_001/scientific_decision.json
```
- 3 fields filled: `kind`, `task_goal`, `constraints` (constraints has content!)
- `repo_path`: STILL EMPTY

### Standalone CodingAgent reference (3 steps, successful)

For comparison, when CodingAgent receives properly-filled fields directly
(not through ExpAgent):
```
/home/cyl/CodingAgent/runs/20260803-200556/results/01_argparse/state.json  → "task" key
```
- All fields present: repo_path, task_goal, constraints[2], verify_commands[1], allowed_paths[1]
- 3 steps to complete: insert_after → run_command → finish

---

## Root Cause Analysis

1. **ExpAgent's LLM is not prompted to fill operational fields.** The `SuggestedPlan`
   model has all the right fields, but the ExpAgent system prompt emphasizes
   scientific analysis over operational details.

2. **Asymmetry between task types**: `repro_task` fields (`paper_url`, `repo_url`)
   are well-known public identifiers that LLMs generate naturally. `coding_task`
   fields (`repo_path`, `verify_commands`) are local-environment-specific and
   the LLM doesn't fill them without explicit prompting.

3. **AdvisorContext lacks concrete repo paths.** ResAgent's `_build_situation()`
   now includes available repository paths, but ExpAgent doesn't extract them
   into `plan.repo_path`.

---

## Recommended Fix Direction

In ExpAgent, consider:

1. **Prompt enhancement**: Add explicit instruction to fill `SuggestedPlan` fields:
   - For `coding_task`: `repo_path` (extract from situation if mentioned),
     `task_goal` (specific and actionable), `constraints` (what NOT to change),
     `verify_commands` (how to confirm correctness)
   - For `repro_task`: already working well

2. **Post-processing in advisor.py**: After the LLM generates `ScientificDecision`,
   validate that `repo_path` is non-empty for coding tasks. If the `situation`
   string contains a path, extract and fill it.

3. **AdvisorContext enhancement**: Consider adding a `repo_paths: list[str]` field
   to `AdvisorContext` so ResAgent can explicitly pass available repos.

---

## Temporary Workaround (in ResAgent)

ResAgent's LLM Planner currently compensates by creating new tasks with
properly-filled fields when it detects empty ExpAgent tasks. This works but:
- Costs an extra LLM call (~20s) to "fix" the task
- Quality depends on the Planner LLM rather than ExpAgent's scientific expertise
- ReproAgent path doesn't need this workaround (ExpAgent output is good)

---

## Verification

Run ResAgent with real ExpAgent and check `state.tasks[0].input`:
```python
# Expected (coding_task): non-empty repo_path, constraints, verify_commands
# Current: repo_path="", constraints=[], verify_commands=[]
```

Test artifacts are preserved at the paths listed above.
