# ReproAgent P4 Contract Follow-up

> Status (2026-08-14): partially implemented in ReproAgent commit `088d9f3`.
> The module test suite passes (`158 passed`), but the three integration gaps
> below must be fixed before P4 cloud acceptance.

## Remaining review findings

### A. Absolute Conda prefixes are resolved but executed/audited as names

`ensure_environment()` accepts an absolute prefix, but
`build_backend_command()` always emits `conda run -n <env_ref>`. Absolute
prefixes require `conda run -p <prefix>`. Add one shared helper that selects
`-p` for absolute paths and `-n` for names, and test the generated command for
both forms. Audit and normal command execution must use the same helper.

The audit currently also checks `env_prefix.name == state.environment.env_name`.
That is valid for a name but always false for an absolute prefix. Normalize the
expected reference to its resolved prefix and compare paths for prefix mode.

### B. A previous successful audit can become stale after dependency changes

The new gate checks only `state.last_audit.success`. After a successful audit,
the agent can run package-install commands and retain the old successful audit,
then launch an experiment without re-auditing the changed environment. Any
environment-mutating setup command must invalidate `last_audit` (or advance an
environment revision checked by the audit). Add a test for:

1. successful audit;
2. dependency installation/change;
3. experiment command is blocked until a new audit succeeds.

### C. Key-artifact truncation may drop the primary experiment evidence

`_key_artifacts()` appends every command stdout/stderr in step order and then
returns the first 12 entries. A long setup/probe phase can consume the cap and
exclude later experiment logs. Classify and prioritize evidence before
truncation: final result, audit, experiment logs/metrics/checkpoints first;
setup/probe logs only as remaining capacity permits. Add a test with more than
12 earlier logs and one late experiment log.

### D. The certification gate can be bypassed by generic inline Python

`_is_setup_command()` treats every `python -c ...` as setup-safe. Before a
successful audit, an experiment can therefore be embedded in `python -c` and
run through the setup whitelist. Replace command-shape guessing with an
explicit pre-audit action policy: narrowly allow known import/version probes
and package repair operations, and reject arbitrary inline programs. Add a
test showing that `python -c` containing training/file mutation is blocked
before audit while the exact audit probe remains available through
`audit_env`.

## Scope

This document describes remaining ReproAgent-owned contract work found in
recent cloud runs. ResAgent must not implement these changes inside its synced
copy of ReproAgent.

## Confirmed gaps

### 1. Existing environment binding is not an explicit task contract

ResAgent can register an environment and inject `env_name`, but ReproTask still
derives its environment from `task_id`/`env_namespace`. This makes a specific
certified environment a hint rather than an authoritative binding.

Add an optional explicit environment reference to ReproTask. When present:

- resolve it by Conda name or absolute prefix according to one documented rule;
- do not silently create a differently named environment;
- run setup, audit, probe, and experiment commands in that exact environment;
- record the resolved name/prefix in `session.yaml` bindings;
- fail clearly if it cannot be resolved.

Keep `env_namespace` as the independent/default creation mechanism for
backward compatibility.

### 2. Environment certification must gate experiment execution

One cloud run trained successfully on GPU but produced no environment-audit
artifact and a session card with no certification. A successful command is not
equivalent to a certified experiment environment.

Before experiment commands, require a successful audit for the selected
environment. A missing or failed audit must trigger bounded environment repair,
`blocked`, or `needs_user_input`; it must not proceed as a normal certified run.
`setup_only` must finish with an audit artifact and a usable environment
binding.

### 3. Session artifacts need machine-readable experiment evidence

`result.md` is useful, but downstream fan-in analysis should not depend on a
single prose file or guessed subdirectories. The session card should register
actual existing paths for key logs, metric files, checkpoints, plots, audit
records, and the final report when produced.

The adapter can continue publishing one top-level ResAgent artifact, but its
metadata/session card must expose the authoritative evidence paths.

### 4. Structured upstream artifact input

ResAgent currently appends dependency artifact paths to `experiment_goal` as a
backward-compatible bridge. Add an optional structured `input_artifacts` field
so ReproAgent can consume prior measurements or files without parsing prose.
The module must remain usable without it.

## Acceptance criteria

1. An explicit existing environment is used unchanged by every command stage.
2. A nonexistent explicit environment fails without creating a substitute.
3. No experiment command runs before environment audit succeeds.
4. `setup_only` produces a session binding plus audit artifact.
5. A GPU task's audit and result both identify the same CUDA-capable
   environment/device.
6. Session `key_artifacts` paths exist and include the primary experiment log
   or metric evidence, not only `result.md`.
7. Structured `input_artifacts` can be consumed without guessed workspace
   paths.
8. Existing isolated, copy, shared, resume, cache, and CodingAgent-delegation
   tests remain green.

## Non-goals

- Do not move environment ownership into ResAgent.
- Do not let ResAgent mutate ReproAgent's internal state files.
- Do not couple ReproAgent to one cloud provider or one machine path.
