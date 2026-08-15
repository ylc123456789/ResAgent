# Phase A Repair Completion Plan

## Purpose

This document guides the completion of ResAgent's Phase A orchestration
repairs after review of commit `caff9d4`.

It applies only to the ResAgent repository. Do not modify ExpAgent,
ReproAgent, or CodingAgent while implementing this plan. When a downstream API
improvement is needed, record it as an integration request and implement only
the ResAgent-side compatibility layer here.

## Current State

The following improvements are already present and should be preserved:

- ReproAgent no longer receives a double-nested `repo_workspace/repo_workspace`
  path.
- Planner context includes key task input fields.
- CodingAgent and ReproAgent dispatch now require an existing `task_id`.
- Controller no longer creates side-effecting CodingAgent/ReproAgent tasks from
  arbitrary Planner parameters.
- Task attempt metadata has been introduced.
- ReproAgent's `completed_with_failures` status has an intended mapping to
  `completed_with_warnings`.

The intended ReproAgent warning mapping is currently broken because the mapped
outcome is not returned at the adapter boundary. The remaining work is grouped
below by priority.

## Design Rules

1. **Controller owns lifecycle.** It validates task selection, records
   attempts, updates task/run status, and decides whether to pause or retry.
2. **Adapters own translation, not policy.** An adapter converts a downstream
   response into a normalized ResAgent result. It must not hide status in an
   unrelated field or infer retry policy.
3. **One task, many attempts.** A scientific task has one stable task ID. Every
   execution has a distinct immutable attempt directory.
4. **A user question is a state transition.** It is not a boolean callback.
5. **Planner proposes; Controller validates.** A malformed Planner action must
   become a safe planning error, never an untracked side effect.

## 1. Repair ReproAgent Outcome Propagation

### Problem

`ReproAgentAdapter._call_execute()` computes a string outcome, but `execute()`
returns it under `returncode`. `Controller._handle_repro_agent()` looks for a
top-level `outcome`, does not find it, and falls back to `returncode == 0`.

A real `completed_with_warnings` result is therefore treated as `failed`.

### Required Data Contract

Introduce a ResAgent-owned normalized execution result. A small Pydantic model
is preferred over an untyped dictionary.

```python
class ExecutionOutcome(str, Enum):
    completed = "completed"
    completed_with_warnings = "completed_with_warnings"
    failed = "failed"
    blocked = "blocked"
    needs_user_input = "needs_user_input"

class AdapterResult(BaseModel):
    outcome: ExecutionOutcome
    summary: str = ""
    artifact: Artifact
    raw: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
```

The first implementation may retain dictionaries for compatibility, but every
adapter result must expose a top-level `outcome` string. Do not overload a
field named `returncode` with a string status.

### Implementation Steps

1. In `ReproAgentAdapter.execute()`, return `outcome=...` at the top level.
2. Keep downstream status in `raw["status"]` and normalized status in both
   `raw["outcome"]` and the adapter result's `outcome` field.
3. Remove or deprecate `returncode` from the ResAgent adapter interface. If it
   must remain temporarily, keep it numeric only.
4. In `Controller._handle_repro_agent()`, consume `result["outcome"]` as the
   single state decision input.
5. For `completed_with_warnings`, mark the task completed, record warnings in a
   dedicated warning field or artifact metadata, and return observation `ok`.
   Do not store a warning in `task.error`.
6. For `failed`, `blocked`, and `needs_user_input`, set both task status and
   observation result consistently.

### Unit Tests

- `completed` produces task `completed` and observation `ok`.
- `completed_with_warnings` produces task `completed`, observation `ok`, and
  warning metadata.
- `failed` produces task `failed` and observation `error`.
- `blocked` produces task `blocked` and does not mark it completed.
- `needs_user_input` produces task `needs_user_input`, pauses the run, and
  exits the loop.
- No test should need a live ReproAgent process; use a fake adapter result.

### Acceptance Criteria

A simulated ReproAgent response with:

```json
{"status": "completed_with_failures", "outcome": "completed_with_warnings"}
```

must finish the Controller step with a completed task and `ok` observation. It
must not trigger a retry or failure classifier.

## 2. Implement Real ask_user Pause and Resume

### Problem

The Controller currently uses a boolean confirmation callback that defaults to
automatic approval. It does not preserve a question, requested information, or
answer. This permits an ask-user loop without any actual user input.

### Required State Contract

Add a persisted model to `ResearchState`:

```python
class PendingQuestion(BaseModel):
    question_id: str
    text: str
    task_id: str | None = None
    requested_fields: list[str] = Field(default_factory=list)
    created_at: datetime
    response: str | None = None
    answered_at: datetime | None = None
```

Use a single optional field:

```python
pending_question: PendingQuestion | None = None
```

### Implementation Steps

1. Replace Controller's default automatic approval with a no-response behavior.
2. On `ask_user`, create `pending_question`, append an observation with
   `user_response_required`, set run status to `paused`, and return control.
3. Add a ResAgent service function such as:

```python
submit_user_response(state, question_id, response) -> ResearchState
```

It must validate the question ID, persist the response, append a `UserDirective`
or a dedicated answer record, clear `pending_question`, and return the run to
`running`.
4. The chat layer must display a pending question instead of calling
   `advance_run` blindly. It should submit the user's reply before resuming.
5. Direct CLI mode must print the pending question and exit/return a paused
   status. It must never silently answer yes.
6. Do not treat a response as approval only. A response is free text and may
   supply a metric, a path, a choice, or a refusal.
7. If a Planner repeats the same unanswered question, preserve one pending
   question and do not create duplicates.

### Unit Tests

- `ask_user` creates a `pending_question` and pauses the run.
- `Controller.run()` stops immediately after that observation.
- No default controller configuration auto-approves.
- A valid answer clears the question, is visible in Planner context, and allows
  a later step to run.
- Invalid or stale question IDs are rejected without altering state.
- Repeated ask-user calls while a question is pending do not create a loop.

### Acceptance Criteria

In a non-interactive run, when the Planner asks "Use 5 or 160 epochs?", the
process ends with `paused`, `state.json` contains the exact question, and no
further module task starts. After supplying "Use 5 epochs", the resumed Planner
context contains that answer.

## 3. Correct run_task Routing

### Problem

`ExpAgentAdapter._actions_to_tasks()` still maps `run_task` to
`ReproAgent/repro_task`. A run task usually means post-experiment validation or
result inspection. It may not have a paper URL or repository URL and is not a
new reproduction request.

### Required Interim Semantics

```text
repro_task       -> ReproAgent
coding_task      -> CodingAgent
run_task         -> ExpAgent result-analysis task
result_analysis  -> ExpAgent result-analysis task
ask_user         -> ResAgent user gate
```

### Implementation Steps

1. Map `run_task` to `Producer.ExpAgent` and `AgentKind.advise`.
2. Preserve `command_goal`, expected runtime, and declared expected artifacts
   in the task input.
3. When the Controller dispatches this ExpAgent task, pass the source
   reproduction artifact(s) to ExpAgent as input context.
4. Do not call a new ExpAgent global-planning pass if the task is specifically
   result analysis; invoke an explicit analysis entrypoint or add a focused
   adapter method that accepts task input and referenced artifacts.
5. If that focused ExpAgent API does not exist, produce an ExpAgent integration
   request rather than forcing the task through ReproAgent.

### Unit Tests

- ExpAgent `run_task` conversion produces an ExpAgent-owned task.
- Converted task has no requirement for `paper_url` or `repo_url`.
- Dispatching it cannot reach `ReproAgentAdapter.execute()`.

### Acceptance Criteria

A successful reproduction followed by an ExpAgent `run_task` results in a
scientific result-analysis artifact that references the reproduction result and
logs. It must not clone a repository or create a Conda environment.

## 4. Make Attempts Immutable on Disk

### Problem

`Attempt` metadata now exists in state, but CodingAgent and ReproAgent adapters
still use one directory per task. Retrying overwrites `result.md`, logs,
adapter result, and task manifest from the prior attempt.

### Required Layout

```text
tasks/
  reproagent/
    task_001/
      task_manifest.json
      attempt_001/
        resagent_adapter_result.json
        repo_workspace/
      attempt_002/
        resagent_adapter_result.json
        repo_workspace/
  codingagent/
    task_002/
      task_manifest.json
      attempt_001/
        state.json
        logs/
        patch_report.md
```

The task-level manifest describes immutable scientific intent. The attempt-level
manifest describes one execution and its timestamps/status.

### Implementation Steps

1. Add `attempt_dir(module, task_number, attempt_number)` to `WorkspaceLayout`.
2. Pass the computed attempt number from Controller to each adapter.
3. Write a separate `attempt_manifest.json` before invoking a module.
4. Make every artifact record include `task_id` and `attempt_number` metadata.
5. Register only paths below the attempt directory for module-generated files.
6. Preserve a future explicit `resume` mechanism separately; do not treat a
   retry as resume by default.

### Unit Tests

- Two attempts of one task use different absolute directories.
- First attempt logs remain unchanged after attempt two.
- Artifact paths for both attempts exist and remain in the artifact index.
- Attempt number in `state.json`, directory name, and manifest agree.

### Acceptance Criteria

Force one clone failure followed by success. The resulting run must contain
both `attempt_001` and `attempt_002`, each with its own logs and adapter result.

## 5. Normalize CodingAgent Outcomes

### Problem

Controller currently marks a CodingAgent task completed whenever the adapter
returns normally. CodingAgent can return a `PatchReport` with `failed`,
`blocked`, or `needs_user_input` without raising an exception.

### Implementation Steps

1. Add the same top-level normalized `outcome` contract used for ReproAgent.
2. Convert CodingAgent report status into ResAgent outcome without assuming an
   exception means the only failure form.
3. Reuse a shared Controller helper to apply an adapter outcome to a task and
   observation. Do not duplicate status mapping in two handlers.
4. Preserve CodingAgent's own `state.json` and report; ResAgent only writes its
   namespaced adapter file.

### Unit Tests

- CodingAgent `completed`, `failed`, `blocked`, and `needs_user_input` reports
  map to the corresponding ResAgent task/observation state.
- A normal-return failed report does not increment completed-task count.

### Acceptance Criteria

A fake CodingAgent report with `status="failed"` leaves the task failed and
causes the Planner to see a failed CodingAgent task, not a completed one.

## 6. Add Deterministic Transient Retry Policy

### Problem

Failure classification currently depends mainly on an LLM. Obvious transport
errors may be labeled `unknown`, which causes unpredictable recovery behavior.

### Implementation Steps

1. Add a pure ResAgent helper, for example `classify_transport_failure(text)`.
2. Recognize at least: DNS failure, TLS EOF, connection reset/refused, GitHub
   timeout, HTTP 429, HTTP 502/503/504, and generic command timeout.
3. Return a deterministic result containing category, retryability, and a
   backoff recommendation.
4. Invoke the LLM classifier only when deterministic classification returns no
   result.
5. Enforce retry budget from `Budget.max_task_retries` per logical task.
6. Retry the same task in a new immutable attempt directory.

### Unit Tests

- Representative GitHub TLS EOF and timeout strings classify as transient.
- Python import error classifies as non-transient and is not automatically
  retried.
- Retry count cannot exceed the configured budget.

### Acceptance Criteria

An injected clone TLS failure triggers a bounded retry for the same task without
asking the LLM to identify a well-known network error.

## 7. Preserve GPU Policy as a Cross-Module Contract

### Scope

This item is only partially implementable in ResAgent. ReproAgent must own
dependency selection and CUDA verification. ResAgent must preserve the research
intent and send it downstream without loss.

### ResAgent-Side Steps

1. Extend ReproAgent task input with optional fields:

```json
{
  "gpu_policy": "required|preferred|forbidden",
  "allow_cpu_fallback": false,
  "max_runtime_seconds": 3600
}
```

2. Preserve these fields in task conversion, planner context, and
`build_reproagent_context()`.
3. Record them in task and attempt manifests.
4. Add a ReproAgent integration request specifying GPU environment audit and
   replan/block behavior.

### Acceptance Criteria

ResAgent can create and persist a GPU-required ReproAgent task without changing
or dropping the policy. Actual enforcement is accepted only after the ReproAgent
integration request has been implemented downstream.

## 8. Regression Suite Requirements

The existing Controller tests are smoke tests and do not cover the repaired
contracts. Add focused tests before running another cloud experiment.

Minimum tests:

1. Missing/unknown/wrong-agent `task_id` cannot start a module.
2. Planner-selected valid task ID dispatches exactly that canonical task input.
3. ReproAgent warning outcome is completed with warnings, not failed.
4. CodingAgent normal-return failed report is failed, not completed.
5. `ask_user` pauses and resumes with a saved free-text answer.
6. Two retries create two distinct directories and preserve logs.
7. `run_task` routes to ExpAgent analysis, never ReproAgent.
8. Deterministic clone/network classification retries within budget.

Run the complete local test suite after the changes. Then run a small cloud
test that deliberately injects one transient clone failure before a successful
bounded reproduction.

## Recommended Implementation Order

1. Outcome result model and ReproAgent outcome propagation.
2. ask_user pause/resume state model and chat/CLI handoff.
3. Correct run_task conversion and focused ExpAgent routing.
4. Attempt directory layout and artifact metadata.
5. CodingAgent outcome normalization.
6. Deterministic retry policy.
7. ResAgent-side GPU policy preservation plus downstream integration request.
8. Local regression suite and cloud end-to-end acceptance test.

## Definition of Done

Phase A is complete only when all of the following are true:

- No free-form Planner response can create side-effecting work.
- Every external execution references one existing task ID and one attempt ID.
- Every task attempt has isolated on-disk evidence.
- Adapter outcomes are typed or equivalently normalized and are never inferred
  from an overloaded return-code field.
- User questions genuinely pause the run and resume only with a stored answer.
- `run_task` does not masquerade as reproduction.
- Known network failures retry deterministically within budget.
- All new contracts have unit tests, and a cloud E2E run passes without task
  bypass, path overwrite, silent CPU fallback, or ask-user looping.

