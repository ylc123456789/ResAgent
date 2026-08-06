# ResAgent — Research Project Manager / Orchestrator

ResAgent is the top-level orchestrator in a multi-agent scientific research system.
It does **not** write code, perform experiments, or reproduce papers. It manages
the research lifecycle by coordinating three specialist agents:

| Agent | Role | ResAgent calls it to... |
|-------|------|------------------------|
| **ExpAgent** | Scientific advisor | Analyze problems, design experiments, evaluate results |
| **CodingAgent** | Programmer | Make scoped code changes in specific repositories |
| **ReproAgent** | Reproduction engineer | Reproduce paper results from published repositories |

## Architecture

```
CLI -> Orchestrator -> Controller (agentic loop)
                        |-- Planner (LLM action selection)
                        |-- Adapters -> ExpAgent / CodingAgent / ReproAgent
```

6 actions: call_exp_agent | call_coding_agent | call_repro_agent | classify_failure | ask_user | finish

Module path resolution: CLI arg > env var > config.yaml > importable package > vendor

## Quick Start

```bash
pip install -e ".[dev]"
export DEEPSEEK_API_KEY=sk-...

resagent init --goal "Compare CNN architectures on CIFAR-10" --workspace runs/
resagent run --workspace runs/ --run-id <id> \
  --expagent-path /path/to/ExpAgent \
  --codingagent-path /path/to/CodingAgent \
  --reproagent-path /path/to/ReproAgent
resagent status --workspace runs/ --run-id <id>
```

## Development

```bash
pytest -q          # 31 tests
```
