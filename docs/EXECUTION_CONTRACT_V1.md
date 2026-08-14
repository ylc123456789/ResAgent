# Execution Contract v1

Status: **SUPERSEDED** by `SCIENTIFIC_ORCHESTRATION_MAINLINE_REDESIGN.md`
(the V2 scientific action contract). This document is kept only as a
historical record of the milestone-one contract; the orchestration mainline
now consumes V2 `capability`-discriminated actions, not the `type`/`plan.kind`
shape described below.

This contract coordinates ExpAgent, CodingAgent, reproagent (the experiment
operator), and ResAgent. It defines data exchanged between modules; it does
not require the modules to share implementation code.

## Ownership

- ExpAgent emits scientific intent and a logical action graph. It never emits
  physical workspace paths or environment names.
- ResAgent resolves logical references, allocates physical paths, registers
  resources, and dispatches work. It does not create environments itself.
- CodingAgent modifies code and reports verification evidence. It does not
  certify experimental results.
- The experiment operator prepares or binds an environment, runs experiments,
  audits the environment, and reports metrics backed by logs.

## Logical action graph

Every recommended action has a non-empty `action_id` that is unique within one
decision. `depends_on` contains only `action_id` values from the same decision,
not ResAgent task IDs. Dependencies must refer to an earlier action, so the
graph is acyclic by construction.

The shared logical fields are:

| Field | Meaning |
|---|---|
| `action_id` | Stable identifier inside one ExpAgent decision |
| `depends_on` | Earlier action IDs that must complete first |
| `project_ref` | Logical identity shared by actions for one repository |
| `workspace_intent` | `shared`, `isolated`, or empty when undecided |

ExpAgent must not emit `workspace_path`, `external_repo_path`, `copy_from`,
`env_name`, or an absolute path. ResAgent converts action dependencies to task
dependencies and resolves the physical fields at dispatch time.

## Repository source modes

An experiment-operator task selects exactly one source:

| Source | Mode | Meaning |
|---|---|---|
| `repo_url` | `isolated` | Clone a remote repository into private task space |
| `copy_from` | `copy` | Copy a local worktree, preserving uncommitted changes |
| `external_repo_path` | `shared` | Operate on an existing repository in place |
| existing `workspace/repo` on resume | prior mode | Resume the existing task repository |

For a new task, zero or multiple explicit sources are invalid. Resume may use
the existing repository only when all three explicit source fields are empty.
The implementation must reject conflicts instead of choosing precedence.

CodingAgent uses `repo_url` only to materialize a repository into its supplied
`workspace_path`. The destination must be absent or empty; a non-empty
conflicting directory must never be overwritten.

## Environment policy

| Policy | Environment behavior |
|---|---|
| `auto` | CodingAgent may create/configure a verification environment |
| `reuse_only` | Use `env_name`; no environment create/delete and no heavy framework mutation |
| `frozen` | Use `env_name`; no package or environment mutation |

Safety validation runs on the original command before any `conda run` wrapper
is added. ResAgent passes references but never performs environment setup.
Only the experiment operator may mark an environment `experiment` certified.

Milestone one retains existing pip and dataset caches plus `env_namespace`.
It does not implement content-addressed environments or cross-run matching.

## Session bindings

Writers use the additive schema below. Paths are absolute at runtime; fixtures
use placeholders. Readers must tolerate a missing `bindings` object and missing
children for compatibility with old cards.

```yaml
bindings:
  repo:
    path: /absolute/path/to/repo
    origin: https://example.invalid/org/repo.git
    commit: abc1234
    mode: isolated
  environment:
    name: resenv_example
    policy: reuse_only
    certification: experiment
    certified_at: 2026-01-01T00:00:00Z
    audit_artifact: logs/environment_audit.stdout
  dataset_cache: /absolute/path/to/datasets
  pip_cache: /absolute/path/to/pip-cache
```

Required when a section is present:

- `repo`: `path`, `origin`, `mode`; `commit` may be empty.
- `environment`: `name`, `policy`, `certification`.
- `certification=experiment`: `certified_at` and `audit_artifact` are required.
- `certification=verification`: produced by CodingAgent; it is not experimental
  certification.

Allowed values:

- repo mode: `isolated`, `copy`, `shared`
- environment policy: `auto`, `reuse_only`, `frozen`
- certification: `none`, `verification`, `experiment`

`project_path` remains the module's resumable project/session pointer for
backward compatibility. Cross-module resource registration uses `bindings`,
not an overloaded interpretation of `project_path`.

## Milestone-one resource record

ResAgent records only resources used by the current run:

```text
kind, id, path/name, origin, repo association, certification,
created_by, created_task
```

This registry supports same-run dispatch. Fingerprints, manifests, locks,
cross-run matching, drift detection, and LRU cleanup belong to milestone two.

## Compatibility and failure rules

- Existing callers that pass `paper_url + repo_url` retain isolated behavior.
- Old session cards without structured bindings remain readable and register no
  resource.
- New fields are additive; a reader must ignore unknown fields.
- Ambiguous source selection, unresolved dependencies, missing shared resources,
  unsafe environment mutations, and conflicting clone destinations fail with a
  structured error. They must not silently fall back to the current directory.

Canonical fixtures live in `tests/fixtures/execution_contract_v1/`.
