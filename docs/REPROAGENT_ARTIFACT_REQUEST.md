# ReproAgent 集成请求：产物路径返回与环境变量作用域

**来自**: ResAgent Phase 2 产物管理修复计划
**日期**: 2026-08-08
**优先级**: 低（ResAgent Phase 1 已修正 artifact 路径）

---

## 背景

ResAgent Phase 1 修正了 ReproAgent adapter 的 artifact 路径——现在指向 `repo_workspace/result.md` 而非错误的上级目录。

但仍有一个结构性问题：ResAgent 需要猜测 `result.md` 的文件名。如果 ReproAgent 返回了确切路径，就不需要猜。

## 请求 1: API 返回产物文件路径

`run_controller()` 返回的 `AgentState`（或 `write_agent_result` 的返回值）中包含：

```python
class AgentState:
    # ... existing fields ...
    produced_files: dict[str, Path] = Field(default_factory=dict)
    # 例: {"result": "result.md", "state": "state.json", ...}
```

或者提供一个独立函数：

```python
def list_task_files(workspace_dir: Path) -> dict[str, Path]
```

## 请求 2（可选）: 数据集/缓存环境变量作用域限定

当前 `REPROAGENT_DATASET_CACHE` 等环境变量在进程级设置，task 结束后不恢复。建议：

- `run_controller()` 接受一个 `env_overrides: dict[str, str]` 参数
- 这些覆盖仅对本次 task 的 subprocess 生效
- Task 结束后环境变量恢复到调用前状态

## 优先级说明

Phase 1 已修正 artifact 路径。本请求为可观测性优化，非阻塞。
