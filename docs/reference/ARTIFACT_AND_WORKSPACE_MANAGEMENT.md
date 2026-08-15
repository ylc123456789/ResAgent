# Artifact and Workspace Management

## Purpose

This document records the current artifact, log, cache, and workspace behavior
across ResAgent, ExpAgent, CodingAgent, and ReproAgent. It defines the target
contract for a future implementation.

This is a design and integration document. ResAgent must not modify the source
of ExpAgent, CodingAgent, or ReproAgent. Changes required in those projects
must be requested through a module-specific integration request document.

## Scope

The system has four different kinds of files:

1. **Run artifacts**: decisions, plans, reports, patches, metrics, and result
   files that explain a research outcome.
2. **Execution evidence**: structured state, command stdout/stderr, tool traces,
   environment audits, and repository commit metadata.
3. **Mutable workspaces**: cloned repositories and code modified during a task.
4. **Shared resources**: reusable Conda environments, package caches, dataset
   caches, and repository mirrors.

The first three categories must be attributable to one research run and one
module task. Shared resources may live outside an individual run, but their
paths and identity must be recorded by that run.

## Current Behavior

### ResAgent

ResAgent has a reasonable top-level research state model. A run uses:

```text
<workspace_root>/<run_id>/
  state.json
  execution_plan.md
  summary.md
  artifacts/index.json
```

Conversation state is intentionally separate:

```text
<workspace_root>/conversations/<conversation_id>/
  conversation.json
  events.jsonl
  experts/
  briefs/
```

This split is conceptually correct: conversations are user interaction history,
while a research run is an executable project record.

However, ResAgent does not yet enforce one consistent per-task directory
contract for its adapters.

### CodingAgent

CodingAgent accepts an explicit `workspace_path` and mandatory `output_dir`.
Given a correct `output_dir`, its expected outputs are well scoped:

```text
<output_dir>/
  state.json
  initial_diff.patch
  diff.patch
  patch_report.md
  logs/
    verify_*.stdout
    verify_*.stderr
```

Its source repository currently also contains a project-local `runs/` directory
for standalone development/testing. That is acceptable for direct use, but it
must not be the destination of a ResAgent-managed task.

### ExpAgent

ExpAgent can accept an explicit `run_dir`. With one, its agent loop writes:

```text
<run_dir>/
  state.json
  logs/
  papers/
```

The CLI may also create `experiment_plan.yaml`, `scientific_decision.json`, and
`validation_report.md` in its output directory. Standalone defaults are not
fully consistent: the CLI derives a project-local `runs/<timestamp>` directory,
whereas the Python API defaults to `Path.cwd() / "runs" / <timestamp>` when no
`run_dir` is supplied.

### ReproAgent

ReproAgent is largely workspace-oriented. One explicit task workspace contains:

```text
<workspace>/
  repo/
  context/
  logs/
  .cache/pip/
  state.json
  result.md
```

The cloned repository is intentionally inside that workspace, so code changes
and files created by a reproduced project are contained there. ReproAgent also
supports optional external locations for a repository cache and a dataset cache.

Conda environments are different: they are created by environment name and are
stored in the Conda `envs_dirs` location configured by the host. They are not
currently created inside the task workspace.

## Findings

### P0: ResAgent overwrites CodingAgent state

`CodingAgentAdapter.execute()` calls CodingAgent with:

```text
<run>/codingagent/code_<task>/
```

CodingAgent writes its detailed agent `state.json` there. The adapter then writes
a simplified raw result to the same `state.json` path. This discards the coding
agent's step history, actions, observations, and verification record.

Impact:

- Later diagnosis cannot reconstruct what CodingAgent did.
- ResAgent keeps only a summary, while the underlying evidence is lost.
- Artifact persistence is not append-safe.

Required repair in ResAgent:

- Do not write adapter-owned data to a child module's reserved file name.
- Preserve CodingAgent's `state.json` unchanged.
- If ResAgent needs a normalized adapter result, write it as
  `resagent_adapter_result.json` in the task directory, or retain it only in
  top-level ResAgent `state.json` and `artifacts/index.json`.

### P0: ReproAgent artifact path does not match the real result

`ReproAgentAdapter` creates:

```text
<run>/reproagent/repro_<task>/
```

but passes this nested directory to ReproAgent:

```text
<run>/reproagent/repro_<task>/repo_workspace/
```

ReproAgent therefore writes its `result.md`, `state.json`, logs, repository, and
context below `repo_workspace/`. The registered ResAgent artifact currently
points to `reproagent/repro_<task>/result.md`, which does not exist.

Impact:

- Artifact index paths are incorrect.
- A downstream module cannot reliably open the reported result.
- Users see a task-level directory but not the actual evidence location.

Required repair in ResAgent:

- Make the ReproAgent workspace equal to the per-task directory itself, or
  explicitly treat `repo_workspace/` as the official ReproAgent workspace.
- Register the exact path returned by ReproAgent rather than reconstructing it
  from a naming convention.
- Never overwrite ReproAgent's `state.json`; use a distinct adapter-owned file
  if needed.

### P0: ExpAgent calls share one mutable output directory

For a ResAgent research run, every ExpAgent call currently uses:

```text
<run>/expagent/
```

The adapter stores a copied decision under `expagent/decision_<n>/`, but
ExpAgent's own `state.json`, `logs/step*.json`, and `papers/` remain shared at
`expagent/`. A second call can overwrite the previous state and trace files, and
saved papers cannot be attributed to one decision.

Impact:

- Scientific reasoning history is not reproducible per decision.
- Log collisions are possible.
- Literature evidence lacks task provenance.

Required repair in ResAgent:

- Allocate one ExpAgent `run_dir` per decision:
  `expagent/decision_<n>/`.
- Pass that exact directory into `advise()`.
- Let ExpAgent own all files within that directory.
- Register `scientific_decision.json`, `state.json`, selected papers, and trace
  paths as artifacts or artifact metadata.

### P1: No formal task directory contract

The adapters use different names (`code_<n>`, `repro_<n>`, `decision_<n>`) and
different levels of nesting. There is no shared manifest that says which module
owns which directory, which files are immutable evidence, or whether a task may
be retried in place.

Impact:

- Directory structure is hard to browse.
- Retry behavior can overwrite earlier evidence.
- Cleanup and archival cannot be automated safely.

### P1: Resource locations are only partially controlled

ReproAgent redirects pip cache into `<workspace>/.cache/pip`, which is good.
However:

- Conda environments use the host Conda environment location.
- Conda package caches may use the host-level package cache.
- Dataset cache is optional and can point anywhere.
- `REPROAGENT_DATASET_CACHE` is set process-wide and not restored after a task.
- Third-party frameworks may write to `~/.cache`, `/tmp`, or their own defaults
  if their cache environment variables are not configured.

Impact:

- Disk usage is difficult to attribute and clean up.
- One task's configuration can leak into the next task in a long-lived process.
- "All generated files are under the workspace" is not presently true.

### P1: Standalone defaults differ across modules

All modules have a project-local `runs/` convention, but those defaults are not
identical. This is acceptable for local development, but a system orchestration
path must always pass explicit destinations and never depend on cwd.

### P2: No lifecycle management

The projects ignore `runs/` in Git, which prevents accidental commits. There is
no formal retention policy, run manifest version, archival command, cleanup
plan, size accounting, or distinction between disposable cache and durable
research evidence.

## Target Contract

### Research root

Each production workflow must receive an explicit absolute `research_root`.
ResAgent must resolve it once at startup and record it in the run state. It must
not silently default to a path based on the caller's current working directory.

```text
<research_root>/
  runs/
  conversations/
  _shared/
    datasets/
    repo-cache/
    conda-envs/
    conda-pkgs/
```

`_shared/` is owned by the deployment/operator, not by any single run. It is an
optimization layer; a run must remain understandable if its shared resources are
later removed.

### Per-run layout

```text
<research_root>/runs/<run_id>/
  run_manifest.json
  state.json
  execution_plan.md
  summary.md
  artifacts/index.json
  tasks/
    expagent/
      decision_001/
        scientific_decision.json
        state.json
        logs/
        papers/
    codingagent/
      task_001/
        state.json
        patch_report.md
        diff.patch
        logs/
        resagent_adapter_result.json
    reproagent/
      task_001/
        state.json
        result.md
        context/
        logs/
        repo/
        .cache/
        patches/
        resagent_adapter_result.json
```

Rules:

1. A module owns its task directory and its normal file names.
2. ResAgent may add only namespaced integration files such as
   `resagent_adapter_result.json`; it must not overwrite module-owned files.
3. An artifact path is always relative to `<run_id>/` and must resolve to an
   existing file at registration time.
4. Every task directory has one `task_manifest.json` written by ResAgent. It
   records task id, module, attempt, input digest, timestamps, status, and the
   exact module workspace path.
5. Retries always create `attempt_001`, `attempt_002`, and so on, unless a
   module explicitly supports safe resume. Failed evidence must never be
   overwritten.

### Artifact record requirements

The ResAgent artifact index should record at least:

```json
{
  "id": "repro_result_001",
  "producer": "ReproAgent",
  "type": "repro_result",
  "path": "tasks/reproagent/task_001/result.md",
  "sha256": "...",
  "task_id": "task_001",
  "attempt": 1,
  "created_at": "...",
  "summary": "..."
}
```

The `path` must be validated before the record is persisted. Checksums are not
required for the first MVP, but the schema should reserve the field.

### Cache and environment policy

- Persistent datasets belong in `<research_root>/_shared/datasets/`.
- Reusable repository mirrors belong in `<research_root>/_shared/repo-cache/`.
- ReproAgent task-specific pip caches belong inside its task directory.
- Conda environments and Conda package cache should be configurable through
  explicit deployment settings, ideally under `_shared/conda-envs/` and
  `_shared/conda-pkgs/`.
- Temporary files should use a task-local temporary directory where practical.
- Cache environment variables must be passed to child processes as a copied
  environment mapping, never left as permanent mutations of the ResAgent
  process environment.

## Repair Plan

### Phase 1: ResAgent-only correctness repairs

These changes are wholly within ResAgent and should be implemented first.

1. Introduce a `RunLayout` or `WorkspaceLayout` helper that is the sole place
   allowed to create or return run/task paths.
2. Give each ExpAgent decision its own `run_dir`.
3. Preserve CodingAgent and ReproAgent `state.json` files; move adapter payloads
   to `resagent_adapter_result.json`.
4. Correct ReproAgent artifact paths and capture the actual result path from its
   returned state.
5. Add artifact-path validation before every index update.
6. Add `task_manifest.json` for every module invocation.
7. Make task attempts immutable and explicitly numbered.

Acceptance criteria:

- One mock ResAgent run can call ExpAgent twice, CodingAgent once, and
  ReproAgent once without any file overwrite.
- Every path in `artifacts/index.json` exists.
- All files written by adapters are distinguishable from module-owned files.

### Phase 2: Cross-module integration requests

ResAgent must create request documents rather than editing downstream projects.

Request to ExpAgent:

- Document that callers must pass `run_dir` in orchestration mode.
- Ensure every trace file is named safely inside that directory.
- Optionally expose a structured list of produced artifact paths.

Request to CodingAgent:

- Return or expose a structured artifact list that includes state, report, diff,
  and verification logs.
- Keep `output_dir` as the sole output authority.

Request to ReproAgent:

- Return exact produced paths in its final state/API result.
- Add an optional explicit environment-prefix or environment-root setting so a
  deployment can place task environments under its configured shared root.
- Scope dataset/cache environment variables to one child-process execution.

### Phase 3: Shared-resource management

1. Add `research_root` configuration to ResAgent.
2. Add a resource manifest recording Conda env path/name, repository cache key,
   dataset cache paths, package cache paths, and disk size.
3. Add read-only `resagent storage status` and `resagent run inspect` commands.
4. Add conservative cleanup planning that reports candidates first; deletion
   must always require explicit user confirmation.
5. Define an archive format for durable run evidence without cloned repository
   build products or reproducible caches.

## Non-goals

- Do not force all large reusable data into each individual run.
- Do not delete previous attempts automatically.
- Do not make ResAgent parse every module's internal log format.
- Do not rely on a hard-coded `/home/cyl/...` layout in production.
- Do not modify downstream module source from the ResAgent repository.

## Development Checklist

- [ ] Add `WorkspaceLayout` and tests for all generated paths.
- [ ] Add per-task/per-attempt directories.
- [ ] Fix adapter state-file collisions.
- [ ] Fix ReproAgent artifact registration.
- [ ] Add artifact existence validation.
- [ ] Add run and task manifests.
- [ ] Write downstream integration request documents.
- [ ] Add resource-root configuration and documentation.
- [ ] Add storage inspection before considering cleanup automation.

