# ReproAgent: shell pipeline failure propagation

## Ownership and scope

This is a ReproAgent-only change. Do not modify ResAgent, CodingAgent, or ExpAgent.

## Problem

ReproAgent executes planned commands through a shell. When a command contains a
pipeline, the shell normally returns only the last process status. For example,
`python train.py | tee train.log` can be reported as successful when Python
failed but `tee` exited successfully. ResAgent cannot repair this after the
adapter call because the incorrect status already comes from ReproAgent.

## Required change

Locate the single command-construction boundary in ReproAgent's runner or Conda
environment helper. Execute task commands with Bash pipeline failure propagation
enabled, preferably by invoking Bash as:

```text
bash -o pipefail -c <command>
```

Keep the existing Conda environment selection, streaming output, timeout,
logging, mirror configuration, and command safety policy unchanged. Do not add a
ResAgent-specific branch.

## Tests to add in ReproAgent

1. `false | tee output.log` returns nonzero.
2. `printf ok | tee output.log` returns zero and preserves output.
3. A non-pipeline command retains its previous return code.
4. The command still runs inside the requested Conda environment.
5. Streaming stdout/stderr and timeout behavior remain covered.

Use temporary directories and local commands only; unit tests must not download
packages or require a GPU.

## Acceptance criteria

- All existing ReproAgent tests pass.
- New pipeline tests pass on Linux.
- No public API or session-card schema changes.
- No edits outside the ReproAgent repository.

## Verification

```bash
cd /home/cyl/reproagent
conda activate reproagent
pytest -q
```

