# 产物与工作区管理 — 审计报告与修复方案

**来源**: 外部审计 (原文: ARTIFACT_AND_WORKSPACE_MANAGEMENT.md)
**翻译整理**: ResAgent 开发会话
**日期**: 2026-08-08
**状态**: run/task 目录、manifest、artifact 登记和 session bindings 已实施。内容寻址环境、跨 run 资源复用、漂移检测和安全清理纳入 [`../active/RESOURCE_MANAGEMENT_MILESTONE_2.md`](../active/RESOURCE_MANAGEMENT_MILESTONE_2.md)。本文的修复步骤保留为审计记录。

---

## 1. 系统中有四类文件

| 类别 | 说明 | 归属 |
|------|------|------|
| **Run 产物** | 决策、计划、报告、补丁、指标、结果文件 | 归属于一个 research run + 一个模块 task |
| **执行证据** | 结构化状态、命令 stdout/stderr、工具 trace、环境审计、仓库 commit | 同上 |
| **可变工作区** | clone 的仓库、task 中被修改的代码 | 同上 |
| **共享资源** | Conda 环境、包缓存、数据集缓存、仓库镜像 | 可跨 run 共享，但路径和标识必须被 run 记录 |

前两类必须可追溯到具体 run 和 task。共享资源可以放在 run 外部，但必须被记录。

---

## 2. 当前行为

### 2.1 ResAgent

```
<workspace_root>/<run_id>/
  state.json
  execution_plan.md
  summary.md
  artifacts/index.json

<workspace_root>/conversations/<conversation_id>/
  conversation.json
  events.jsonl
  experts/
  briefs/
```

对话层和编排层的分离在概念上正确。但 adapter 之间缺乏一致的 per-task 目录契约。

### 2.2 CodingAgent

接收显式的 `workspace_path` 和必填的 `output_dir`：

```
<output_dir>/
  state.json
  initial_diff.patch
  diff.patch
  patch_report.md
  logs/
    verify_*.stdout / verify_*.stderr
```

### 2.3 ExpAgent

接收显式的 `run_dir`：

```
<run_dir>/
  state.json
  logs/
  papers/
```

CLI 还会额外产出 `experiment_plan.yaml`、`scientific_decision.json`、`validation_report.md`。

### 2.4 ReproAgent

```
<workspace>/
  repo/
  context/
  logs/
  .cache/pip/
  state.json
  result.md
```

Conda 环境存储在宿主 Conda 的 `envs_dirs` 下，不在 workspace 内。

---

## 3. 发现问题

### P0-1: ResAgent 覆盖 CodingAgent 的 state.json

`CodingAgentAdapter.execute()` 将 CodingAgent 的详细 `state.json`（含每步 action、verification 记录）覆盖为一个简化的 raw 摘要。

**文件**: `src/resagent/adapters/codingagent.py:55-56`

```python
with open(out_dir / "state.json", "w") as f:
    json.dump(raw, f, ...)
```

**影响**:
- 无法从 state.json 重建 CodingAgent 的执行过程
- 完整的 step 历史和 verification 记录丢失
- ResAgent 只保留了摘要，底层证据不可恢复

**修复**:
- 不覆盖模块自有文件。CodingAgent 的 `state.json` 保持原样
- ResAgent 的 adapter 结果写入 `resagent_adapter_result.json`
- 或将 adapter 结果仅保留在顶层的 ResAgent `state.json` 和 `artifacts/index.json` 中

### P0-2: ReproAgent 产物路径不匹配

adapter 创建 `<run>/reproagent/repro_NNN/`，但传给 ReproAgent 的是 `repro_NNN/repo_workspace/`。ReproAgent 的所有产物（`result.md`、`state.json`、`logs/`、`repo/`）都在 `repo_workspace/` 下。而 artifact 注册的路径指向不存在的 `reproagent/repro_NNN/result.md`。

**文件**: `src/resagent/adapters/reproagent.py:39-40, 49-50, 76`

```python
out_dir = Path(workspace_dir) / f"reproagent/repro_{task_num}"
...
workspace_dir=out_dir / "repo_workspace",  # ReproAgent 在这里写
...
path=f"reproagent/repro_{task_num}/result.md",  # artifact 指向错误位置
```

**影响**:
- Artifact index 中的路径指向不存在的文件
- 下游模块无法可靠地打开报告的结果

**修复**:
- 将 ReproAgent 的 workspace 直接设为 per-task 目录，或
- artifact path 使用 ReproAgent 实际返回的路径，不从命名惯例重建
- 同样不覆盖 ReproAgent 的 `state.json`

### P0-3: ExpAgent 多次调用共享同一个输出目录

每次调用 ExpAgent 都使用 `Path(workspace_dir) / "expagent"` 作为 `run_dir`。多次调用时 `state.json`、`logs/`、`papers/` 被后续调用覆盖。

**文件**: `src/resagent/adapters/expagent.py:204`

```python
run_dir = Path(workspace_dir) / "expagent"
```

**影响**:
- 科学推理历史不可按 decision 复现
- trace 文件可能冲突
- 文献证据缺乏 task 归属

**修复**:
- 每个 ExpAgent decision 分配独立的 `run_dir`：`expagent/decision_NNN/run/`
- adapter 的决策拷贝放在 `expagent/decision_NNN/` 根下
- ExpAgent 内部文件完全归属该 decision

### P1-1: 缺少统一的 task 目录契约

adapter 使用不同命名（`code_<n>`, `repro_<n>`, `decision_<n>`）和不同的嵌套层级。没有共享的清单说明哪个模块拥有哪个目录、哪些文件是不可变证据、task 是否可以在原地重试。

### P1-2: 资源位置仅部分受控

- Conda 环境使用宿主位置
- Conda 包缓存可能使用宿主级别缓存
- 数据集缓存可选且可指向任意位置
- 第三方框架可能写入 `~/.cache`、`/tmp` 或其他默认路径
- 缓存环境变量是进程级设置，task 结束后不恢复

### P2: 缺少生命周期管理

- 没有正式的保留策略
- 没有 run manifest 版本
- 没有归档命令
- 没有清理计划
- 没有大小统计
- 没有区分可丢弃缓存和持久化研究证据

---

## 4. 目标契约

### 4.1 研究根目录

```
<research_root>/
  runs/
  conversations/
  _shared/
    datasets/
    repo-cache/
    conda-envs/
    conda-pkgs/
```

`_shared/` 由部署/运维管理，不属于任何单个 run。它是优化层；即使共享资源被移除，run 仍应可理解。

### 4.2 Per-run 布局

```
<research_root>/runs/<run_id>/
  run_manifest.json
  state.json
  execution_plan.md
  summary.md
  artifacts/index.json
  tasks/
    expagent/
      decision_001/
        scientific_decision.json
        run/                         ← ExpAgent 的独立 run_dir
          state.json
          logs/
          papers/
    codingagent/
      task_001/
        state.json                   ← CodingAgent 原生，不覆盖
        patch_report.md
        diff.patch
        logs/
        resagent_adapter_result.json  ← ResAgent 的 adapter 摘要
    reproagent/
      task_001/
        state.json                   ← ReproAgent 原生，不覆盖
        result.md
        context/
        logs/
        repo/
        .cache/
        patches/
        resagent_adapter_result.json
```

**规则**:
1. 模块拥有自己的 task 目录和标准文件名
2. ResAgent 只能添加带命名空间的集成文件（如 `resagent_adapter_result.json`），不覆盖模块文件
3. artifact path 相对于 `<run_id>/`，注册时必须指向存在的文件
4. 每个 task 目录有一个 `task_manifest.json`：记录 task id、模块、attempt、输入摘要、时间戳、状态
5. 重试创建 `attempt_001`、`attempt_002` 等，除非模块显式支持安全恢复。失败证据绝不覆盖

### 4.3 Artifact 记录规范

```json
{
  "id": "repro_result_001",
  "producer": "ReproAgent",
  "type": "repro_result",
  "path": "tasks/reproagent/task_001/result.md",
  "sha256": "...",
  "task_id": "task_001",
  "attempt": 1,
  "created_at": "...",
  "summary": "..."
}
```

### 4.4 缓存和环境策略

- 持久数据集 → `<research_root>/_shared/datasets/`
- 可复用仓库镜像 → `<research_root>/_shared/repo-cache/`
- ReproAgent pip 缓存 → task 目录内
- Conda 环境和包缓存 → 通过 `_shared/conda-envs/` 和 `_shared/conda-pkgs/` 可配置
- 临时文件 → task 本地临时目录
- 缓存环境变量 → 以复制的环境映射传给子进程，不永久修改 ResAgent 进程环境

---

## 5. 修复计划

### Phase 1: ResAgent 自身修复（本会话可实施）

| # | 任务 | 涉及文件 |
|---|------|---------|
| 1 | 引入 `WorkspaceLayout` 工具类，集中管理所有 run/task 路径 | 新文件 `src/resagent/workspace_layout.py` |
| 2 | 每个 ExpAgent decision 分配独立 `run_dir` → `expagent/decision_NNN/run/` | `adapters/expagent.py` |
| 3 | CodingAgent/ReproAgent 的 `state.json` 不改写；adapter 摘要写 `resagent_adapter_result.json` | `adapters/codingagent.py`, `adapters/reproagent.py` |
| 4 | 修正 ReproAgent artifact path：使用实际产物路径 | `adapters/reproagent.py` |
| 5 | artifact 注册前验证文件存在 | `adapters/expagent.py`, `codingagent.py`, `reproagent.py` |
| 6 | 每次模块调用写入 `task_manifest.json` | 各 adapter |
| 7 | Task attempt 不可变且显式编号 | `adapters/` + `models.py` |

**验收标准**:
- 一次 mock run 可调用 ExpAgent 两次、CodingAgent 一次、ReproAgent 一次，无文件覆盖
- `artifacts/index.json` 中每个路径都存在
- adapter 写入的文件与模块自有文件可区分

### Phase 2: 跨模块集成请求（生成文档，不直接改下游）

| 文档 | 接收方 | 内容 |
|------|--------|------|
| `CODINGAGENT_ARTIFACT_REQUEST.md` | CodingAgent | 要求返回结构化 artifact 列表；`output_dir` 保持为唯一输出权威 |
| `EXPAGENT_ARTIFACT_REQUEST.md` | ExpAgent | 确认为编排模式提供 `run_dir`；可选暴露产物路径列表 |
| `REPROAGENT_ARTIFACT_REQUEST.md` | ReproAgent | 返回确切产物路径；可选 conda 环境根目录配置；数据集/缓存环境变量作用域限定 |

### Phase 3: 共享资源管理

1. ResAgent 增加 `research_root` 配置
2. 资源清单：Conda env 路径/名、仓库缓存键、数据集缓存路径、包缓存路径、磁盘大小
3. `resagent storage status` 和 `resagent run inspect` 命令
4. 保守清理方案：先报告候选、再确认删除
5. 归档格式：保留 run 证据（去掉 clone 仓库和可再生缓存）

---

## 6. 不做什么

- 不强制把大型可复用数据塞进每个 run
- 不自动删除历史 attempt
- 不让 ResAgent 解析每个模块的内部日志格式
- 不在生产环境依赖硬编码路径
- 不从 ResAgent 仓库直接修改下游模块源码
