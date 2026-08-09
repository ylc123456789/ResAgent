# Integration Request: 统一产物路径纪律（ExpAgent / CodingAgent）

**Date**: 2026-08-10
**From**: ResAgent 代码审查（四仓库产物路径专项）
**To**: ExpAgent owner、CodingAgent owner
**Priority**: Medium（功能不受影响，但产物散落会持续累积混乱）

---

## 背景

四仓库联合审查结论：pytest 单测普遍干净（tmp_path 隔离），reproagent 纪律最好（CLI `--workspace` 必填）。问题集中在**独立使用时的默认路径**和**个别测试 helper**。

ResAgent 侧已修复：`RESAGENT_WORKSPACE` 环境变量，解析链 `--workspace` CLI > env > config.yaml > `./runs`，chat 启动时打印实际落盘路径。

建议两个仓库采纳同一约定：**所有默认产物根目录支持 `RESAGENT_WORKSPACE` 环境变量**，使整个系统在任何机器上都能用一行 `export RESAGENT_WORKSPACE=/root/autodl-tmp/resagent-workspace/runs` 收敛全部产物。

## 请求 1 — ExpAgent（两处）

### 1a. `advise()` 的默认 run_dir 依赖 cwd

`src/experiment_designer/advisor.py:50-53`：

```python
if run_dir is None:
    run_dir = Path.cwd() / "runs" / stamp   # ← 在哪调用就丢哪
```

建议改为：

```python
if run_dir is None:
    root = os.environ.get("RESAGENT_WORKSPACE")
    run_dir = (Path(root) if root else Path.cwd() / "runs") / stamp
```

`main.py::_default_output_dir`（L575-586，写死 project_root/runs，fallback cwd/runs）同理加 env 分支。

注：被 ResAgent 编排时 run_dir 总是显式传入，此问题只影响 ExpAgent 独立使用（REPL、`expagent --idea`）。

### 1b. e2e/planner 测试把产物写进仓库

`tests/test_e2e.py:536-541` 与 `tests/test_planner.py:276-281` 的 `_runs_dir()`：

```python
project_root = Path(__file__).resolve().parent.parent
return project_root / "runs" / "tests" / ...   # ← 写进 ExpAgent 仓库内
```

磁盘实证：`ExpAgent/runs/tests/{advisor,plan,test_traces}` 已存在。

理解其动机是保留 trace 供人工检查。建议：默认改为 pytest `tmp_path`，仅当显式设置环境变量（如 `EXPAGENT_KEEP_TEST_TRACES=1`）时才落仓库——既保可追溯性，又不让 `pytest` 的默认行为污染仓库。

## 请求 2 — CodingAgent（一处）

`tests/batch_real_tasks.py:135`：

```python
tmp = Path.cwd() / "runs" / datetime...   # ← 从哪运行就丢哪，CodingAgent/runs/ 由此产生
```

建议：

```python
root = os.environ.get("RESAGENT_WORKSPACE")
tmp = (Path(root) if root else Path.cwd() / "runs") / datetime...
```

CodingAgent 的核心 API 纪律很好（`output_dir` 必填），仅此一个批测脚本需要收口。

## 不属于本请求的范围

- reproagent：无需改动。
- 各仓库 `.gitignore` 已覆盖 `runs/`（git status 干净），本请求解决的是**磁盘上的物理散落**，不是版本库污染。

## 验收标准

| # | 检查 |
|---|------|
| 1 | `RESAGENT_WORKSPACE=/tmp/ws expagent --idea ... --no-interactive` 的产物落在 `/tmp/ws` 下 |
| 2 | `cd /home/cyl/ExpAgent && pytest` 后 `git status` 与 `ls runs/` 均无新增（默认 tmp_path） |
| 3 | `cd /tmp && python CodingAgent/tests/batch_real_tasks.py` 不在 `/tmp/runs` 留产物（设了 env 时） |
| 4 | 现有测试全绿 |
