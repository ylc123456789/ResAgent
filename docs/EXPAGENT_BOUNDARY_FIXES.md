# ExpAgent 边界修复任务

**来源**: ResAgent 全系统边界审计 (docs/BOUNDARY_AUDIT_REPORT.md)
**审计日期**: 2026-08-08
**修复优先级**: 低（不影响生产，仅 mock 和死代码路径）

---

## 问题 1: mock 模式中有硬编码绝对路径

**文件**: `src/experiment_designer/llm.py`
**行号**: 219, 227

```python
# line 219 (mock coding task example)
"workspace_path": "/home/cyl/my_project",

# line 227 (mock recommended action)
"workspace_path": "/home/cyl/my_project",
```

**影响**: `mock=True` 时，这个路径会原样写入 `experiment_plan.yaml`、`scientific_decision.yaml`、`state.json`，并通过 ResAgent 进入下游 `AgentTask.input.workspace_path`。不影响真实 API 调用。

**建议修复**: 改为 `"./"` 或 `"<your_project_path>"` 占位符。

---

## 问题 2: save_paper 默认输出目录可能泄露到 CWD

**文件**: `src/experiment_designer/tools.py`
**行号**: 110

```python
def save_paper(..., output_dir: str | Path = "papers"):
```

**影响**: 当前所有调用路径（advisor.py:158）都显式传入 `papers_dir`，所以死代码路径不会触发。但如果有人直接调 `save_paper()` 不传 `output_dir`，会在 CWD 下创建 `papers/` 目录。

**建议修复**: 移除默认值，改为必传参数：
```python
def save_paper(..., output_dir: str | Path):
```
