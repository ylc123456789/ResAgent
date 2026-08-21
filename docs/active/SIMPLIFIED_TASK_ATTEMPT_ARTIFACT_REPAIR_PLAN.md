# 简化任务编排与实验产物管理修复方案

**日期**：2026-08-21  
**状态**：本地实施完成，待云端真实验收  
**原则**：保留现有功能；复用现有模型和目录；删除特殊路径；不增加第二套事实来源

## 1. 问题

L3 已完成完整闭环，但暴露了三个同源问题：

1. 两个实验在共享 repo 中写同名结果，后一次覆盖前一次；
2. CodingAgent 修复共享代码后，兄弟实验实际使用了补丁，但依赖和输入 Artifact 没有记录；
3. 动态 repair Task 走特殊构造路径，出现 `capability=""`。

根因不是 agentic loop，而是五个概念边界不清：

```text
科学建议 / 已调度 Task / 一次 Attempt / 可变 workspace / 不可变 Artifact
```

## 2. 借鉴但不引入外部框架

- [MLflow Tracking](https://mlflow.org/docs/latest/tracking/)：一次执行独立保存参数、指标和 Artifact；
- [Argo Artifacts](https://argoproj.github.io/argo-workflows/walk-through/artifacts/)：步骤间显式传递输出；
- [Temporal Retry Policies](https://github.com/temporalio/documentation/blob/main/docs/encyclopedia/retry-policies.mdx)：局部执行失败只重试该 Activity；
- [DVC Experiments](https://dvc.org/doc/user-guide/experiment-management/)：机器通过 JSON/CSV/YAML 等结构化指标比较实验。

本项目只采用这些原则，不增加 MLflow、DVC、Temporal、Argo、数据库或对象存储依赖。

## 3. 保留的现有模型

不新增模型：

| 现有模型 | 唯一职责 |
|---|---|
| `AgentTask` | 当前 run 已承诺执行的工作 |
| `Attempt` | Task 的一次真实执行 |
| `Artifact` | 已冻结、可供下游读取的执行证据 |
| `ResourceRef` | repo、Conda env、cache 等可变资源 |

不新增 Proposal、ProjectRevision、ArtifactManager 或 AttemptManifest。

## 4. 唯一主线

```text
ScientificDecision
  required=true  -> AgentTask -> Attempt -> Artifact -> analyze_results
  required=false -> 只留在 ScientificDecision，summary 展示
```

四条不变量：

```text
optional 建议不是 Task
workspace 不是 Artifact
result.json 是机器接口，result.md 是阅读接口
retry 创建新 Attempt，不创建同义 Task
```

## 5. ResAgent 修改

### 5.1 一个任务创建入口

新增小函数 `create_task()`，统一以下生产路径：

- 初始 ExpAgent 咨询；
- ExpAgent required action 转换；
- 自动补充 analyze_results；
- 用户 plan revision；
- ReproAgent blocked 后的 CodingAgent repair。

它只负责：

- 分配 task id；
- 填写 agent/kind/capability/source/action_id；
- 接收 dependencies、project_ref、input、fingerprint；
- 可选追加到 state。

禁止上述路径继续直接调用 `AgentTask(...)`。

### 5.2 Optional recommendation 不入队

`actions_to_tasks()` 只转换 `required=true` 的 action。

`required=false` action：

- 继续保存在 `scientific_decision.json`；
- 不进入 `state.tasks`；
- finish 时从最新 ScientificDecision Artifact 中写入 summary。

因此 completed run 不再包含 optional pending Task。

### 5.3 Repair 传播

repair Task 必须：

```text
agent=CodingAgent
kind=coding_task
capability=modify_code
source=<blocked repro task>
project_ref=<same project>
```

修复成功后：

1. 原 blocked ReproTask 回到 pending；
2. 同一 `project_ref` 下尚未执行的 ReproTask 增加 repair Task dependency；
3. 执行前现有 `materialize_dependency_artifacts()` 自动绑定 code_patch；
4. 已完成任务不改动。

不增加 ProjectRevision 模型，不自动重写已完成任务。

### 5.4 复用现有 task_manifest

不增加新 manifest。扩展现有 `task_manifest.json`，记录：

```json
{
  "task_id": "task_003",
  "module": "ReproAgent",
  "attempt": 2,
  "capability": "execute_experiment",
  "project_ref": "lrsched-cifar100-resnet18",
  "depends_on": ["task_002", "task_006"],
  "input_artifacts": ["code_patch_002", "code_patch_006"]
}
```

不修改 Attempt 模型。

## 6. ReproAgent 修改

### 6.1 Finish 返回结构化字段

finish action 增加：

```json
{
  "finish_metrics": {
    "best_test_acc": 61.70,
    "final_test_acc": 61.69
  },
  "finish_parameters": {
    "schedule": "cosine",
    "seed": 20170922,
    "epochs": 200
  },
  "evidence_files": [
    "final_metrics.json",
    "train_curves.json"
  ],
  "finish_deviations": []
}
```

这些字段进入 AgentState 的单一 `structured_result` 字典，不拆成多个状态字段。

### 6.2 冻结明确列出的 evidence

ReproAgent 不扫描整个 repo。

结束时仅处理 LLM 明确列出的 `evidence_files`：

1. 路径必须位于当前 repo 或当前 Attempt workspace 内；
2. 文件必须存在且是普通文件；
3. 复制到当前 ReproAgent workspace 的 `evidence/repo/` 或 `evidence/workspace/`；
4. 保留来源相对路径，避免同名冲突；
5. 记录 sha256 和 size；
6. 越界或不存在写入 warnings，不猜测替代文件。

### 6.3 两种结果文件

每次 ReproAgent Attempt 生成：

```text
repo_workspace/
  result.json
  result.md
  evidence/
```

`result.json` 是机器接口：

```json
{
  "schema": "repro_result_v1",
  "status": "completed",
  "summary": "...",
  "metrics": {},
  "parameters": {},
  "deviations": [],
  "evidence": [
    {"path": "evidence/repo/final_metrics.json", "sha256": "...", "size_bytes": 123}
  ],
  "warnings": []
}
```

`result.md` 保留现有报告，并追加 Frozen Evidence 段落。

### 6.4 ResAgent 登记机器结果

ReproAgent adapter：

- `result.json` 存在时，`repro_result` Artifact path 指向它；
- metadata 记录 `human_report_path=result.md`；
- 旧/失败路径没有 result.json 时回退 result.md。

这样 ExpAgent 现有 ArtifactRef/read_file 流程会自然优先读取 JSON，无需新接口。

## 7. ExpAgent 修改

不修改数据模型和 controller。

只调整 system prompt：

- `repro_result` 指向 JSON 时优先读取结构化 metrics；
- 比较必须 best-to-best、final-to-final；
- Markdown 只用于解释和补充；
- 缺失结构化指标时明确降低 confidence。

## 8. 删除的旧逻辑

实施时同步删除：

1. 五条生产路径中直接 `AgentTask(...)` 的重复构造；
2. optional action 转换成 pending Task；
3. finish 从 optional pending Task 拼 follow-up；
4. ReproAgent 只写 result.md 的机器接口；
5. ReproAgent result Artifact 默认指向 Markdown；
6. repair Task 缺 capability 的特殊路径。

不保留新旧两套主线。

## 9. 测试

### 9.1 本地确定性 fixture

使用一个极小共享 repo，模拟两个 Attempt 依次写同名 `final_metrics.json`：

```text
arm=cosine -> metric=0.8
arm=linear -> metric=0.7
```

必测：

1. 两个 Attempt 的 evidence 各自存在，前一份不被后一份覆盖；
2. result.json 的 metrics、parameters、evidence 正确；
3. 越界 evidence 被拒绝；
4. optional recommendation 不生成 Task；
5. repair Task capability=`modify_code`；
6. repair dependency 传播到同项目 pending ReproTask；
7. ExpAgent 读取 JSON 后保持指标口径一致；
8. 确定性四模块闭环通过。

### 9.2 全量测试

- ResAgent 全量 pytest；
- reproagent 全量 pytest；
- ExpAgent 全量 pytest；
- ResAgent deterministic system test。

### 9.3 云端

只跑一次 2-3 epoch 双臂轻量 GPU 测试，不重跑 L3 200 epoch。

## 10. 完成定义

1. 四个现有核心模型保留，无平行模型；
2. 所有生产 Task 经过 `create_task()`；
3. completed run 无 optional pending Task；
4. repair Task 字段完整并传播到 pending sibling；
5. 每个 Repro Attempt 独立拥有 result.json 和 evidence；
6. 共享 repo 文件变化不影响已冻结 evidence；
7. ExpAgent 优先消费结构化结果；
8. 旧重复路径已删除；
9. 三仓全量测试和确定性闭环通过；
10. 代码结构比修改前更集中，没有新增管理框架。

## 11. 本地验收结果

2026-08-21 已完成：

- ResAgent：218 passed；
- reproagent：224 passed；
- ExpAgent：79 passed，22 个真实 E2E 用例按惯例排除；
- CodingAgent：167 passed（未修改，仅回归验证）；
- ResAgent deterministic system test：passed。

尚未执行：云端 2-3 epoch 双臂轻量 GPU 验收。
