# Four-Module Refactor Phase 0 Baseline

Date: 2026-08-13

## Purpose

Phase 0 freezes externally observable behavior before readability refactoring.
It does not move production files, rename modules, change prompts, change model
schemas, or alter runtime behavior.

The compatibility tests added to each repository are intentionally small. They
lock the boundaries most likely to be damaged by file moves:

- public package exports and versions;
- CLI commands and options;
- controller/system prompts and representative rendered prompts;
- persisted Pydantic field names and JSON round trips;
- ResAgent workspace paths;
- cross-module task models and action schemas.

Prompt hashes are behavior locks, not style tests. A later intentional prompt
change must be reviewed as a behavior change and must not be hidden inside a
readability-only refactor.

## Source Baseline

| Module | Branch | Source commit before Phase 0 tests |
| --- | --- | --- |
| ResAgent | `master` | `87e76f4` |
| ExpAgent | `main` | `090e132` |
| CodingAgent | `main` | `d11570c` |
| ReproAgent | `main` | `384312b` |

These commits describe the production behavior being frozen. Each repository's
Phase 0 test commit will be newer while leaving production source unchanged.

## Verification Results

Run from each repository with its named conda environment:

```bash
conda run -n ResAgent python -m pytest -q
conda run -n ResAgent python -m pytest -q  # from ExpAgent
conda run -n CodingAgent python -m pytest -q
conda run -n reproagent python -m pytest -q
```

Results on 2026-08-13:

| Module | Result |
| --- | --- |
| ResAgent | `127 passed` |
| ExpAgent | `61 passed, 22 deselected` |
| CodingAgent | `35 passed` |
| ReproAgent | `112 passed` |

The ExpAgent deselections are its existing real-API E2E tests, excluded by the
repository's default pytest configuration.

The deterministic four-module system test also passed:

```bash
cd /home/cyl/ResAgent
PYTHONPATH=/home/cyl/ResAgent/src:/home/cyl/ExpAgent/src:/home/cyl/CodingAgent/src:/home/cyl/reproagent/src \
  conda run -n ResAgent python scripts/deterministic_system_test.py
```

It verified typed ExpAgent-to-ReproAgent tasks, artifact registration,
follow-up ReproAgent routing, CodingAgent parent links, multiple ReproTasks,
ask-user save/resume behavior, finish gating, and artifact path existence.

## What Was Not Re-Run

Phase 0 did not run a real LLM, download dependencies, train a model, or execute
the cloud GPU acceptance suite. The latest successful cloud acceptance remains
the runtime baseline, while the deterministic test protects orchestration during
the refactor. A real cloud E2E should be run after all four module refactors are
merged, not after every file move.

## Refactor Rule

During the readability refactor:

1. Run the affected repository's full test suite after each coherent move.
2. Run `tests/test_phase0_contract.py` before committing.
3. Do not update a failing contract hash or expected field list merely to make a
   refactor pass. First determine whether observable behavior changed.
4. Any intentional CLI, prompt, schema, path, or public API change leaves the
   behavior-preserving refactor track and requires separate review.
5. Keep one owner per module. Cross-module changes must be documented and handed
   to that module's owner instead of edited incidentally.

## Phase 0 Acceptance

Phase 0 is complete when:

- each repository contains `tests/test_phase0_contract.py`;
- all four full unit suites pass;
- the deterministic four-module system test passes;
- production source files remain unchanged;
- each repository has one focused Phase 0 commit and a clean worktree.
