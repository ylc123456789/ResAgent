# Cloud E2E Findings and Orchestration Repair Plan

## Purpose

This document records findings from the AutoDL end-to-end test of ResAgent,
ExpAgent, ReproAgent, and CodingAgent. It turns the observed failures into a
development plan for ResAgent.

This document does not authorize ResAgent to modify ExpAgent, ReproAgent, or
CodingAgent. When a repair requires a downstream-module change, create a
module-specific integration request and handle that change in the downstream
module's own repository and development session.

## Test Snapshot

The tested research run was:

```text
/tmp/cloud_repro_majnpsgg/res-20260809-87c48f/
```

Observed outputs included:

```text
state.json
tasks/
  expagent/decision_001/
    scientific_decision.json
    task_manifest.json
    run/state.json
  reproagent/task_004/
    resagent_adapter_result.json
    task_manifest.json
    repo_workspace/result.md
  reproagent/task_005/
    repo_workspace/
      result.md
      state.json
      logs/
      repo/
      experiment_odenet/logs
  codingagent/task_006/
    diff.patch
    patch_report.md
```

The experiment itself completed and produced an MNIST ODE-Net result of about
99.04% test accuracy. This validates several important parts of the system:

- Per-task workspace layout is substantially cleaner than before.
- ExpAgent can produce a scientifically useful bounded-reproduction plan.
- ReproAgent can clone, configure, run, and preserve experiment artifacts.
- CodingAgent can be reached later in the workflow.
- A transient clone failure did not prevent a later successful run.

However, the workflow did not follow the intended ownership contract. The
system succeeded despite important orchestration defects, not because those
defects are harmless.

## Intended Responsibility Model

```text
ExpAgent      Scientific judgment and proposed tasks
ResAgent      Scheduling, lifecycle, retries, user gates, artifact registry
ReproAgent    Repository reproduction and environment/experiment execution
CodingAgent   Repository code inspection and modification
```

The central rule is:

> ResAgent may decide which existing task to execute next. It must not silently
> replace ExpAgent's scientific task specification with a new LLM-invented
> execution task.

## Finding 1: Planner Bypassed Valid ExpAgent Tasks

### Observation

ExpAgent produced three pending tasks:

```text
task_001  ReproAgent/repro_task
task_002  ReproAgent/repro_task (derived from run_task in current mapping)
task_003  ResAgent/ask_user
```

The first task was valid. Its `SuggestedPlan` contained a valid paper URL,
repository URL, and experiment goal. The Planner nevertheless created and ran
new tasks `task_004` and `task_005` instead of dispatching `task_001`.

### Root Cause

This is not caused by an absent `repo_url` or malformed ExpAgent plan.

The current ResAgent contract is internally inconsistent:

1. `build_controller_context()` presents pending tasks only as ID, module,
   kind, and priority. It does not present their actionable input.
2. The Controller prompt tells the LLM to call `call_repro_agent` with
   `paper_url`, `repo_url`, and `experiment_goal` directly.
3. `Controller._handle_repro_agent()` and `_handle_coding_agent()` create a
   new task when the Planner does not supply a resolvable `task_id`.

The LLM is therefore encouraged to synthesize an operational task rather than
selecting an existing task. It can bypass ExpAgent even if the ExpAgent task is
complete and higher priority.

### Required ResAgent Repair

Adopt a strict dispatch model:

```text
Planner selects:       action + existing task_id
Controller resolves:   task_id -> canonical AgentTask.input
Adapter executes:      normalized task input only
```

Required changes:

1. Require `task_id` for `call_repro_agent` and `call_coding_agent`.
2. Validate that the selected task exists, is pending or explicitly retryable,
   and belongs to the requested module.
3. Remove the fallback that creates a new execution task from arbitrary Planner
   parameters.
4. Make creation of a new task an explicit, separately audited action. In the
   first implementation, only ExpAgent task conversion and explicit user
   directives should create execution tasks.
5. Include a concise immutable task summary in Planner context, but let the
   Controller remain the sole authority for executable parameters.

### Acceptance Tests

- When ExpAgent emits one valid pending `repro_task`, the next ReproAgent call
  must use that exact task ID and exact `paper_url`/`repo_url`.
- A Planner response without `task_id` for a module execution action is rejected
  as a planning error; it must not create a task.
- A Planner response that selects a CodingAgent task ID for ReproAgent is
  rejected before any external process starts.
- The run state links each execution attempt to its ExpAgent source artifact.

## Finding 2: Retry Worked Operationally but Not Semantically

### Observation

The first reproduction attempt failed because `git clone` failed after three
network attempts. A later reproduction succeeded.

### What Worked

The system retained the first task's logs and result and eventually performed a
successful second attempt. This is useful recovery behavior.

### What Is Still Wrong

The successful retry was represented as a newly synthesized `task_005`, not as
a second attempt of the same logical task. The failure classifier returned
`unknown/investigate` for a recognizably transient GitHub connection failure.

The current system therefore lacks a reliable task-attempt lifecycle:

```text
logical task -> attempt_001 failed -> classification -> attempt_002 retry
```

Instead it has disconnected task records, which makes provenance and retry
budgets unreliable.

### Required ResAgent Repair

1. Keep one logical `AgentTask` for a scientific action.
2. Add attempt records with attempt number, timestamps, failure category,
   artifact IDs, and retry reason.
3. Allocate immutable directories such as:

```text
tasks/reproagent/task_001/
  attempt_001/
  attempt_002/
```

4. Treat known transport errors (`git clone`, DNS, connection reset, TLS EOF,
   timeout, API 5xx) as deterministic `transient` failures before asking an
   LLM classifier.
5. Apply bounded backoff and retry policy; when exhausted, pause for user input
   or escalate to ExpAgent only if a scientific decision is needed.

### Acceptance Tests

- A simulated clone timeout creates `attempt_001` with failed status.
- The retry uses `attempt_002` beneath the same logical task.
- The artifact index retains result/log links for both attempts.
- Retry budget exhaustion yields `blocked` or `needs_user_input`, not an
  unrelated new task.

## Finding 3: Successful Result Was Reported as an Error

### Observation

ReproAgent finished the substantive experiment and produced a valid result
with approximately 99.04% test accuracy. Its overall status was
`completed_with_failures` because a later agent-loop JSON parse issue occurred.

ResAgent converted this to an error because the adapter used:

```text
returncode = 0 only when ReproAgent status == "completed"
```

The Controller then reported an error when `returncode != 0`.

There is a second inconsistency: the Controller currently marks the task as
`completed` before it evaluates the return code. This can leave a completed task
with an error observation.

### Required ResAgent Repair

Do not model downstream outcomes as a binary return code. Introduce a typed
adapter outcome, for example:

```text
completed
completed_with_warnings
failed
blocked
needs_user_input
```

ResAgent should update the task status, observation result, retry policy, and
artifact metadata from this one normalized outcome.

Short-term compatibility mapping:

```text
ReproAgent completed                 -> completed
ReproAgent completed_with_failures   -> completed_with_warnings
ReproAgent failed                    -> failed
```

The long-term correct contract belongs in ReproAgent: it should return a
structured distinction between:

- whether the primary experiment goal was achieved;
- warnings/non-fatal agent-loop failures;
- final report and result paths;
- whether retry is appropriate.

ResAgent should consume that declared contract rather than inspect arbitrary
logs to infer scientific success.

### Acceptance Tests

- A `completed_with_failures` response with `primary_goal_achieved=true` becomes
  `completed_with_warnings`, retains artifacts, and triggers result analysis
  rather than automatic retry.
- A failed ReproAgent response marks the task failed.
- No task can be `completed` while its latest execution observation is `error`.

## Finding 4: GPU Requirement Was Lost in Environment Execution

### Observation

The AutoDL machine had an RTX 4090D and ReproAgent collected hardware context.
ExpAgent also requested a GPU run. ReproAgent nevertheless installed PyTorch
from the CPU wheel index and executed the experiment on CPU, taking about
10.5 minutes instead of roughly one minute on GPU.

### Responsibility Boundary

Selecting a compatible PyTorch/CUDA build and validating actual GPU usage is a
ReproAgent responsibility. ResAgent must not hard-code wheel URLs or CUDA
versions.

ResAgent does, however, need to preserve the upstream resource constraint as a
structured, non-optional field when dispatching a task.

### Required Cross-Module Contract

Add an execution-resource policy to a reproduction task:

```json
{
  "gpu_policy": "required",
  "allow_cpu_fallback": false,
  "max_runtime_seconds": 3600
}
```

Suggested meanings:

- `required`: do not start the experiment unless a CUDA-capable framework and
  visible GPU have been verified.
- `preferred`: use GPU if available; CPU fallback requires a prominent warning.
- `forbidden`: CPU-only execution is intentional.

Required ReproAgent behavior should be requested separately:

1. Read available driver/GPU hardware before dependency planning.
2. Prefer a compatible GPU framework build when policy is `required` or
   `preferred` and hardware is available.
3. Verify `torch.cuda.is_available()` and device identity after installation.
4. When policy is `required`, replan or pause rather than running a CPU build.
5. Record actual execution device and dependency versions in the final report.

### Acceptance Tests

- GPU-required task + GPU available + CPU-only torch results in a blocked or
  replan state before training starts.
- GPU-preferred task can fall back to CPU only with an explicit warning artifact.
- Result report contains device, CUDA runtime, PyTorch version, and fallback
  rationale.

## Finding 5: ask_user Is Not a Real User Interaction Protocol

### Observation

The test used a mock confirmation callback that always returned `True`. The LLM
asked for information, the callback approved the question without supplying an
answer, and the Planner could ask again.

### Root Cause

The issue is deeper than the test callback. `Controller` currently defaults to
a confirmation callback equivalent to automatic approval. Its `ask_user` action
does not persist a question identifier, expected response, or answer. It only
returns an observation like `User approved: ...`.

Therefore neither CLI mode nor chat mode has a complete pause-answer-resume
protocol for internal research-run questions.

### Required ResAgent Repair

Replace boolean confirmation with a persisted question lifecycle:

```text
ask_user
  -> create pending_question(question_id, text, task_id, requested_fields)
  -> mark run paused and task needs_user_input
  -> return control to caller/chat

user response
  -> store response linked to question_id
  -> append user directive/evidence
  -> resume run
```

Rules:

1. A default non-interactive callback must never silently approve a question.
2. The chat layer must render the pending question and resume only after a
   concrete user response.
3. Tests may inject a scripted response provider keyed by `question_id`; a
   boolean provider is insufficient for information requests.
4. Add duplicate-question detection or a maximum unanswered-question policy.

### Acceptance Tests

- A run that asks for a metric pauses and persists a question; it does not keep
  calling the planner.
- Resuming with a response makes that response visible in Planner context.
- A repeated unanswered question does not create an infinite loop.

## Additional Contract Gap: run_task Is Not a Repro Task

The ExpAgent decision included a `run_task` asking to inspect training logs and
verify loss/accuracy behavior. Current ResAgent maps `run_task` to
`ReproAgent/repro_task`. This is semantically wrong: a run-task has a
`command_goal` and prior artifacts, but often has no paper URL, repository URL,
or reproduction goal.

Recommended interim mapping:

```text
repro_task   -> ReproAgent
coding_task  -> CodingAgent
run_task     -> ExpAgent result analysis over declared artifacts
ask_user     -> ResAgent pause-and-resume protocol
```

A future `ResultInspector` module may execute deterministic log/metric checks,
but it should not be simulated by creating an invalid ReproAgent task.

## Repair Order

### Phase A: ResAgent control-plane correctness

1. Enforce task-id-only dispatch; remove implicit execution-task creation.
2. Implement pause-answer-resume for `ask_user`.
3. Normalize downstream outcome handling and fix task/observation consistency.
4. Add first-class attempts and deterministic transient retry policy.
5. Correct `run_task` routing.

### Phase B: Integration contracts

1. Create a ReproAgent integration request for structured outcomes.
2. Create a ReproAgent integration request for `gpu_policy` and GPU audit gate.
3. Create an ExpAgent integration request only if it needs a stricter task
   schema or explicit task resource policy output.

### Phase C: End-to-end regression suite

Run a deterministic test covering:

```text
ExpAgent creates repro_task -> ResAgent dispatches same task_id
-> first clone fails transiently -> attempt_002 succeeds
-> ReproAgent returns completed_with_warnings + metric artifact
-> ResAgent registers warning outcome
-> ExpAgent analyzes result
-> ResAgent pauses for a concrete user decision
```

The test must assert artifact paths, task source links, attempt lineage, final
run status, and absence of unexpected task creation.

## Non-goals

- Do not move PyTorch/CUDA decision logic into ResAgent.
- Do not let ResAgent parse arbitrary ReproAgent logs to decide science success.
- Do not allow a free-form Planner response to create side-effecting work.
- Do not treat mock callback approval as a substitute for user input.

