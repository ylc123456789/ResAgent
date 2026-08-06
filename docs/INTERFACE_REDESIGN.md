# ResAgent ↔ ExpAgent 接口重设计方案

**日期**: 2026-08-06
**涉及模块**: ResAgent、ExpAgent（CodingAgent / ReproAgent 不变）
**状态**: 方案阶段，待两边确认后实施

---

## 1. 设计原则

```
ExpAgent = 科学顾问     → 决定"做什么、为什么"
ResAgent = 项目经理     → 决定"在哪做、怎么执行、用什么参数"
CodingAgent = 程序员    → 执行代码修改
ReproAgent = 复现工程师  → 执行论文复现
```

操作性的参数（路径、命令、超时、镜像策略）**天然属于 ResAgent，不应由 ExpAgent 填充**。

---

## 2. 现状问题

### 2.1 责任边界模糊

`SuggestedPlan` 模型混合了两种职责的字段：

```
┌─────────────────────────────────────────────────┐
│ kind, task_goal           ← 科学决策 (ExpAgent)   │
│ paper_url, repo_url       ← 科学决策 (ExpAgent)   │
│ experiment_goal           ← 科学决策 (ExpAgent)   │
│ expected_metrics          ← 科学决策 (ExpAgent)   │
│ rationale                 ← 科学决策 (ExpAgent)   │
│ ───────────────────────── ──────────────────── │
│ workspace_path            ← 操作细节 (ResAgent)   │
│ constraints               ← 操作细节 (ResAgent)   │
│ verify_commands           ← 操作细节 (ResAgent)   │
│ expected_artifacts        ← 操作细节 (ResAgent)   │
│ compute_budget            ← 操作细节 (ResAgent)   │
│ expected_runtime          ← 操作细节 (ResAgent)   │
│ requires_gpu              ← 操作细节 (ResAgent)   │
│ search_query              ← 操作细节 (ResAgent)   │
└─────────────────────────────────────────────────┘
```

后果：ExpAgent 只填上面 2-3 个字段，下面全是空的。ResAgent 收到残缺任务，要么让 LLM Planner 补救（浪费调用），要么 CodingAgent 直接报错。

### 2.2 字段名不一致

CodingAgent 已把 `repo_path` 重命名为 `workspace_path`（commit `5047a65`），但 ExpAgent 的 `SuggestedPlan` 和 `CodingTask` 仍用旧名。每个 adapter 都要做字段名翻译。

### 2.3 测试数据佐证

| 测试 | ExpAgent 填了多少字段 | 能否直接执行 |
|------|---------------------|-------------|
| CodingAgent 任务 | 2/17：`kind` + `task_goal` | ❌ `workspace_path` 为空 |
| ReproAgent 任务 | 6/17：`kind` + `paper_url` + `repo_url` + `experiment_goal` + `compute_budget` + `expected_metrics` | ✅ 关键字段都有 |

ReproAgent 路线之所以能跑通，是因为 ExpAgent 擅长填公开标识符（URL）。本地路径它从不填—这本就不该是它的活。

测试产物路径见 [EXPAGENT_INTEGRATION_REQUEST.md](./EXPAGENT_INTEGRATION_REQUEST.md)。

---

## 3. 新设计

### 3.1 分层模型

```
ExpAgent 产出                            ResAgent 补全
┌─────────────────────┐               ┌──────────────────────┐
│ kind                │               │ workspace_path       │
│ task_goal           │   ───────→    │ constraints          │
│ paper_url (repro)   │   ExpAgent    │ verify_commands      │
│ repo_url  (repro)   │   adapter     │ expected_artifacts   │
│ experiment_goal     │   翻译+补全    │ compute_budget       │
│ expected_metrics    │               │ mirror_profile       │
│ rationale           │               │ timeout / max_steps  │
└─────────────────────┘               └──────────────────────┘
   科学上做什么、为什么                    系统上怎么做、什么参数
```

### 3.2 各模块责任

**ExpAgent 负责填充**（必填）：

| 字段 | 含义 | 举例 |
|------|------|------|
| `kind` | 任务类型 | `coding_task` / `repro_task` |
| `task_goal` | 科学目标 | "添加 per-epoch loss 日志" |
| `paper_url` | 选哪个论文 | `https://arxiv.org/abs/...` |
| `repo_url` | 选哪个仓库 | `https://github.com/pytorch/examples` |
| `experiment_goal` | 实验目的 | "验证 baseline accuracy > 95%" |
| `expected_metrics` | 关注什么指标 | `["test_accuracy", "ECE"]` |
| `rationale` | 为什么做 | "建立 baseline 后才能对比" |

**ExpAgent 可以留空**（ResAgent 补全）：

| 字段 | ResAgent 怎么补 |
|------|----------------|
| `workspace_path` | 从 research goal 提取，或从已有 task 继承，或用当前目录 |
| `constraints` | 默认策略："不改变训练语义"、"只修改指定文件" |
| `verify_commands` | 默认策略：`py_compile` → `pytest` → `python script.py --help` |
| `expected_artifacts` | 不补（让 CodingAgent 自己决定产出什么文件） |
| `compute_budget` | 从 ResAgent config 取默认值 |
| `expected_runtime` | 从 task 类型估计 |
| `requires_gpu` | 从 task_goal 关键词推断 |
| `mirror_profile` | 根据环境：WSL 本地=`none`，云端=`autodl` |

**LLM Planner 最后兜底**：如果 ResAgent 规则推断也不出来（比如不知道 repo 在哪），留空让 LLM Planner 在 dispatch 时补全或 ask_user。

---

## 4. ResAgent 改动清单

### 4.1 `adapters/expagent.py` — 参数推断逻辑

`_actions_to_tasks` 方法从"透传"变为"翻译 + 补全"。

新增三个推断函数：

```python
def _infer_workspace_path(state, plan, action) -> str:
    """推断 workspace_path，优先级：
    1. plan 里 ExpAgent 已经填的（尊重科学判断）
    2. research goal 文本中提取路径
    3. 已有 task 的 workspace_path 继承
    4. 空（让 LLM Planner 或用户补）
    """
    if plan.get("workspace_path") or plan.get("repo_path"):
        return plan.get("workspace_path") or plan.get("repo_path")
    # 从 research goal 文本中匹配路径
    import re
    goal = state.run.research_goal
    match = re.search(r'(/[^\s,;]+)', goal)
    if match:
        return match.group(1)
    # 从已有 task 继承
    for t in state.tasks:
        p = t.input.get("repo_path") or t.input.get("workspace_path", "")
        if p:
            return p
    return ""


def _infer_constraints(plan, action) -> list[str]:
    """为不同类型生成默认约束。"""
    kind = plan.get("kind", "")
    if plan.get("constraints"):
        return plan["constraints"]  # ExpAgent 已填的直接用
    defaults = {
        "coding_task": [
            "Do not change training semantics or model architecture",
            "Only modify files necessary for the stated goal",
        ],
        "repro_task": [],
    }
    return defaults.get(kind, [])


def _infer_verify_commands(plan, action) -> list[str]:
    """为 coding_task 生成默认验证命令。"""
    if plan.get("verify_commands"):
        return plan["verify_commands"]  # ExpAgent 已填的直接用
    kind = plan.get("kind", "")
    if kind == "coding_task":
        return ["python -m py_compile <modified_files>"]
    return []
```

`_actions_to_tasks` 中的字段映射改为：

```python
# 改前
input={
    "repo_path": plan.get("repo_path", ""),
    "constraints": plan.get("constraints", []),
    "verify_commands": plan.get("verify_commands", []),
    ...
}

# 改后
input={
    "workspace_path": _infer_workspace_path(state, plan, action),
    "constraints": _infer_constraints(plan, action),
    "verify_commands": _infer_verify_commands(plan, action),
    "repo_path": plan.get("repo_path", ""),  # 保留兼容旧 ExpAgent
    ...
}
```

### 4.2 `context.py` — 字段名翻译

```python
# build_codingagent_context: repo_path → workspace_path
def build_codingagent_context(task):
    return {
        "workspace_path": task.input.get("workspace_path")
                       or task.input.get("repo_path", ""),  # 兼容旧 task
        "task_goal": task.input.get("task_goal", ""),
        "constraints": _as_list(task.input.get("constraints", [])),
        "verify_commands": _as_list(task.input.get("verify_commands", [])),
        "allowed_paths": _as_list(task.input.get("allowed_paths", [])),
        "output_dir": task.input.get("output_dir", ""),
    }
```

### 4.3 `adapters/codingagent.py` — 适配新 API

```python
# _call_execute: workspace_path 和 output_dir 用 Path 类型
task_spec = CodeTaskSpec(
    workspace_path=Path(spec.get("workspace_path", "") or "."),
    task_goal=spec.get("task_goal", ""),
    constraints=spec.get("constraints", []),
    verify_commands=spec.get("verify_commands", []),
    allowed_paths=spec.get("allowed_paths", []),
    max_steps=self.max_steps,
    model=self.model,
    api_base=self.api_base,
    api_key_env=self.api_key_env,
    output_dir=out_dir,  # Path 类型
)
```

### 4.4 `adapters/reproagent.py` — 参数补全

```python
# _call_execute: 补全操作参数
task = ReproTask(
    paper_url=spec.get("paper_url", ""),
    repo_url=spec.get("repo_url", ""),
    workspace_dir=out_dir / "repo_workspace",
    experiment_goal=spec.get("experiment_goal", ""),
    model=self.model,
    api_base=self.api_base,
    api_key_env=self.api_key_env,
    timeout_seconds=spec.get("timeout") or 1800,
    mirror_profile=spec.get("mirror_profile") or _default_mirror(),  # 环境感知
    mock_llm=False,
)
```

---

## 5. ExpAgent 改动方案（建议）

> 以下改动在 ExpAgent 项目中执行。ResAgent 开发会话不直接修改 ExpAgent。

### 5.1 `models.py` — 字段重命名

```
SuggestedPlan.repo_path → workspace_path
CodingTask.repo_path    → workspace_path
```

### 5.2 `prompts.py` — 调整 System Prompt

明确告诉 ExpAgent 的 LLM：

```
Your job is scientific decision-making. You decide WHAT to do and WHY.

For each recommended action, fill these fields:
  - kind: what type of task
  - task_goal: what scientific goal this task achieves
  - rationale: why this task is the right next step
  - paper_url / repo_url: which paper/repo to use (for repro tasks)
  - experiment_goal: what experiment to run (for repro tasks)
  - expected_metrics: what metrics to evaluate

You do NOT need to fill operational fields:
  - workspace_path, constraints, verify_commands, expected_artifacts,
    compute_budget, expected_runtime, mirror_profile
  These are filled by the orchestrator (ResAgent) based on the
  execution environment.
```

### 5.3 `advisor.py` — 移除操作字段填充

如果 `advisor.py` 中有尝试填充操作字段的逻辑，移除或简化。

---

## 6. ReproAgent 影响评估

ReproAgent **不需要改**。它通过 `ReproTask` 接收所有参数（包括操作细节），ExpAgent 填科学部分、ResAgent 补操作部分，最终透传给 ReproAgent 的是一个完整的 `ReproTask`。

`mirror_profile` 现在由 ResAgent 根据环境自动选择——这是之前没有的能力。

---

## 7. CodingAgent 影响评估

CodingAgent **不需要改**。它的 `CodeTaskSpec` API 已在本次更新中稳定。ResAgent 适配后传入正确的 `workspace_path`（Path 类型）即可。

---

## 8. 实施顺序

```
1. ExpAgent 改 models.py (重命名字段) + prompts.py (调整 prompt)
2. ResAgent 改 adapters/expagent.py (参数推断) + context.py + codingagent.py
3. 联调测试
4. ReproAgent 无需改动
```

两个模块可以并行改——ResAgent 的 adapter 兼容新旧字段名（`workspace_path` 和 `repo_path` 都接受），所以不依赖 ExpAgent 先完成。

---

## 9. 决策记录

| 决策 | 理由 |
|------|------|
| ExpAgent 不填操作字段 | 操作字段依赖执行环境（本地/云端），不属于科学判断 |
| ResAgent 用规则推断而非 LLM | 推断逻辑简单确定（路径匹配、默认约束），不需要 LLM |
| 保留 ExpAgent 填操作字段的能力 | 如果 ExpAgent 填了，ResAgent 直接使用（不覆盖） |
| 兼容新旧字段名 | `repo_path` 和 `workspace_path` 都接受，过渡期平滑 |
| LLM Planner 兜底 | 规则推断不出的，由 LLM Planner 在 dispatch 时补或 ask_user |
