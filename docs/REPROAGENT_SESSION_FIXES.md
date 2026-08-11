# ReproAgent Session 修复任务

**来源**: ResAgent 全流程测试分析 (run: res-20260811-80f8b6)
**优先级**: P1（影响执行可靠性和报告可读性）

---

## 问题 1: audit_env 被当 bash 命令执行

日志显示：
```
conda run ... bash -c audit_env → exit 127: audit_env: command not found
```
LLM 把内部 agent action 名写进了 `commands` 列表，而非使用 `action: "audit_env"`。

**建议修复** (`src/reproagent/runner.py`):
命令执行前拦截内部 action 名：
```python
INTERNAL_ACTIONS = {"audit_env", "call_coding_agent", "finish"}
for cmd in commands:
    if cmd.strip() in INTERNAL_ACTIONS:
        return CommandResult(
            command=cmd, returncode=-1,
            stderr=f"'{cmd}' is an agent action, not a shell command. Use action: '{cmd}' instead."
        )
```

---

## 问题 2: result.md 声称"无偏差"但实际有失败

`result.md` 写 "No deviations"，但 Step 3 实际有一个 exit=127 的失败命令。虽然后续 audit_env 作为正确 action 重试成功了，但报告应体现这次恢复。

**建议修复** (`src/reproagent/report.py`):
在写报告时，扫描 steps 中的异常并追加 "Recovered Warnings" 段：
```python
warnings = []
for step in state.steps:
    for cr in step.command_results:
        if cr.returncode != 0 and not _is_expected_failure(cr):
            warnings.append(f"Step {step.step}: {cr.command[:80]} (exit={cr.returncode})")
if warnings:
    lines += ["## Recovered Warnings", ""] + [f"- {w}" for w in warnings]
```

---

## 问题 3: 重试无 attempt 计数

同一 task 多次尝试时，result.md 和 session card 无法区分是哪次 attempt。

**建议修复** (`src/reproagent/report.py`):
在报告头部显式标注 `Attempt: 1` 或 `Attempt: 2 (retry after clone failure)`。
