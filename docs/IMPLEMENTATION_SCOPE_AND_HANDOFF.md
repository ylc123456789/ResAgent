# ResAgent closure work: ownership and handoff

## Goal

Close the four-module orchestration loop without coupling the submodules or
turning cloud runs into the only source of truth.

## Implemented directly in ResAgent

These are orchestration concerns and must remain owned by ResAgent:

- deterministic task executor/capability normalization;
- correct routing of executable `run_task` work to ReproAgent;
- task fingerprints and duplicate suppression;
- generated allowed-action candidates for the planner;
- required-task and error-aware finish validation;
- terminal-state guard so completed runs cannot execute again;
- task-bound `ask_user` persistence and answer recovery;
- CodingAgent, ReproAgent, and ExpAgent parent session linkage;
- real mock artifacts so registered paths are testable;
- unit, deterministic system, and real cloud acceptance tests.

## No CodingAgent change required

CodingAgent already exposes `CodeTaskSpec.parent_run`. ResAgent now passes this
public field. CodingAgent remains independently callable and no private API was
used.

## Delegated independent changes

- ReproAgent: shell pipeline status propagation. See
  `docs/handoffs/REPROAGENT_PIPEFAIL_HANDOFF.md`.
- ExpAgent: optional same-decision dependency metadata. See
  `docs/handoffs/EXPAGENT_TASK_CONTRACT_HANDOFF.md`.

The ExpAgent enhancement is not required for the current sequential closed
loop. Until it exists, an ambiguous same-decision `run_task` fails explicitly
instead of being silently assigned to the wrong module.

## Test ownership

ResAgent owns the cross-module acceptance tests and their usage guide:

- `tests/test_task_contracts.py`
- `tests/test_orchestration_closure.py`
- `scripts/deterministic_system_test.py`
- `scripts/cloud_acceptance.py`
- `docs/TESTING_GUIDE.md`

Each submodule still owns its internal unit tests. The handoff documents state
the exact submodule tests and acceptance criteria to add.

## Merge order

1. Review and merge the ResAgent closure changes.
2. Run local tests and deterministic system acceptance.
3. Let the ReproAgent maintainer apply and test `pipefail` independently.
4. Optionally let the ExpAgent maintainer add dependency metadata.
5. Sync submodule revisions into the cloud checkout.
6. Run the cloud acceptance cases individually, then `--case all`.

