# ExpAgent P4 Contract Follow-up

> Status (2026-08-14): implemented and locally accepted in ExpAgent commit
> `0a2afe3`. ExpAgent tests: `75 passed, 22 deselected`. Final acceptance is
> the ResAgent cloud `fan-in-analysis` case.

## Scope

This document describes remaining ExpAgent-owned contract work found while
validating the P3/P4 ResAgent experiment graph. It is an issue handoff, not an
instruction for ResAgent to modify ExpAgent source.

## Confirmed gaps

### 1. Result analysis has no first-class action type

`RecommendedAction.type` and `ActionPlan.kind` accept experiment, coding,
literature, and user-input actions, but not `result_analysis`. In a two-run
comparison the advisor can therefore emit `run_task`, which routes the final
scientific interpretation back to ReproAgent. That violates the role split:
ReproAgent measures; ExpAgent interprets.

Add `result_analysis` to both model literals and the structured-output schema.
Its logical contract should contain:

- `action_id`
- `depends_on` listing every experiment whose evidence must be analyzed
- `project_ref` when relevant
- a goal/question for the analysis
- no physical workspace or output path guesses

ResAgent already infers `result_analysis` as `ExpAgent/analyze_result` and now
materializes all dependency artifacts before dispatch.

### 2. Required and priority are still conflated at the producer boundary

The recommended-action model has priority but no explicit `required` field.
Priority is scheduling order; required controls whether ResAgent may finish
without the task. They are independent.

Add `required: bool = True` to the model and JSON schema. Prompt rules should
say that scientific conclusions, requested experiments, and final result
analysis are normally required. Only genuinely optional follow-ups should set
`required=false`.

## Acceptance criteria

1. ExpAgent can emit an action graph containing two experiment actions and one
   `result_analysis` action depending on both.
2. The output validates through ExpAgent's model and structured-output schema.
3. The analysis action contains logical references only, never guessed
   `tasks/.../result.md` paths.
4. A medium- or low-priority action remains `required=true` unless explicitly
   marked optional.
5. Existing standalone advisory and experiment-planning tests remain green.
6. ResAgent's `fan-in-analysis` cloud case receives both actual artifact paths
   and completes with an ExpAgent scientific decision.

## Non-goals

- Do not let ExpAgent read or mutate ResAgent state directly.
- Do not make ExpAgent execute commands or edit code.
- Do not add ResAgent-specific filesystem layout knowledge to ExpAgent.
