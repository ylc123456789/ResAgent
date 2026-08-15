# ExpAgent 集成请求：编排模式下的产物路径规范

**来自**: ResAgent Phase 2 产物管理修复计划
**日期**: 2026-08-08
**优先级**: 低（ResAgent Phase 1 已通过独立 run_dir 规避了核心问题）

---

## 背景

ResAgent 在每次调用 `advise()` 时传入 `run_dir`。ExpAgent 在 `run_dir` 下写入 `state.json`、`logs/`、`papers/` 等文件。

Phase 1 之前，多次调用的 `run_dir` 相同导致文件互相覆盖。Phase 1 之后，ResAgent 为每次 decision 分配独立目录（`decision_NNN/run/`），解决了隔离问题。

但仍有两个改进项：

## 请求 1: 文档化 run_dir 契约

ExpAgent 的 `advise()` 文档/注释中明确说明：

```
run_dir:  ExpAgent 独占的目录。调用方保证不同调用传入不同路径。
          ExpAgent 可在此目录下自由创建任何文件和子目录。
          调用方不应依赖 ExpAgent 内部文件命名——除 state.json 外
          都是实现细节。
```

## 请求 2（可选）: 暴露产物文件列表

如果方便，`advise()` 返回的 `extra` 中包含产物文件清单：

```python
extra = [
    ...,
    {"produced_files": ["state.json", "logs/llm_step1.prompt.txt", ...]}
]
```

或提供一个工具函数 `list_run_files(run_dir) -> list[str]`。

## 边界修复提醒

审计发现了两个低风险问题（文档 `EXPAGENT_BOUNDARY_FIXES.md`）：

1. `llm.py:219,227` mock 数据中有 `/home/cyl/my_project` 硬编码路径——建议改为相对路径
2. `tools.py:110` `save_paper` 的 `output_dir` 默认值 `"papers"` 可能泄露到 CWD——建议改为必传参数

## 优先级说明

Phase 1 已通过独立 `run_dir` 解决了核心问题。本请求中的两项都是锦上添花。
