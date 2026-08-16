# 里程碑二：环境复用与资源生命周期管理

**日期**：2026-08-16

**状态**：ACTIVE，待 P0 契约评审后开始实现

**前置基线**：里程碑一 P0-P4 和 V2 科学编排主线已完成，tag `v2-validated-2026-08-15`

**主要模块**：ResAgent、reproagent、CodingAgent；ExpAgent 无代码改动

**关联契约**：
[`EXPERIMENT_OPERATOR_REDESIGN.md`](../completed/EXPERIMENT_OPERATOR_REDESIGN.md)、
[`SESSION_AND_PROJECT_MODEL.md`](../reference/SESSION_AND_PROJECT_MODEL.md)、
[`ARTIFACT_AND_WORKSPACE_MANAGEMENT_CN.md`](../reference/ARTIFACT_AND_WORKSPACE_MANAGEMENT_CN.md)

---

## 1. 目标

里程碑一解决了执行链断裂：同一 repo 和同一 env 可以在 CodingAgent、reproagent 和 ResAgent 之间传递。里程碑二不再改造科学编排主线，只解决资源复用的准确性、并发安全和可管理性。

目标是让系统能回答并证明以下问题：

1. 这个环境是否真的与当前 repo、Python、平台和 GPU 栈匹配？
2. 环境是否可以安全复用，而不是只因为名字相同？
3. 两个并发任务是否会重复创建或相互破坏环境？
4. 环境被增量安装后是否已经漂移？
5. 跨 run 复用时，谁使用过它、它在哪里、谁可以删除？
6. 磁盘不足时，能否在不删除活跃资源的前提下安全清理？

一句话定位：

```text
里程碑一让资源“能传递”；
里程碑二让资源“能识别、能复用、能审计、能安全清理”。
```

## 2. 现有能力基线

以下能力已存在，本里程碑必须复用，不得重写为第二套主线：

- reproagent 的 `env_namespace` 可在同一 ResAgent run 内复用同名 conda env；
- `env_name` 可绑定现有 conda 环境名或绝对 prefix；
- `isolate_env=True` 可强制任务级隔离；
- pip wheel cache 和数据集 cache 可跨任务复用；
- `mirror_profile` 可控制下载源策略；
- CodingAgent 已有 `auto / reuse_only / frozen` 环境权限边界；
- 三个执行模块已能在 `session.yaml` 写入 bindings；
- ResAgent 已有 run 级 `ResearchState.resources` 和 bindings 回收逻辑；
- 云端 `env-reuse` 验收已证明同一 run 内两个任务可共享一个 env。

必须区分两个指标：

```text
缓存命中：包或数据不再从公网下载，但仍可能创建新 env。
环境命中：已有 env 通过身份校验与审计，跳过创建和安装。
```

里程碑二的主要缺口是：内容身份、manifest、并发锁、复用前审计、漂移检测、跨 run 发现和安全清理。

## 3. 边界与不变量

### 3.1 模块所有权

| 模块 | 负责 | 明确不做 |
|---|---|---|
| reproagent | 创建/复用实验环境，写 manifest，实验级 audit 和认证 | 不决定科学任务，不直接修改实验语义 |
| CodingAgent | 独立调用时创建/复用验证级 env；按 env_policy 使用已绑定 env | 不将验证级 env 宣布为实验级认证 |
| ResAgent | 记录资源、选择候选、注入指针、管理 run 引用与清理决策 | 不运行 pip/conda 安装，不伪造 audit 结论 |
| ExpAgent | 根据 artifact 做科学判断 | 不感知物理 env 名、prefix、cache 路径 |

任何模块的开发会话只能修改自己的仓库。跨模块问题必须生成问题单，不得在调用方仓库内特化下游模块。

### 3.2 设计不变量

1. 环境身份由确定性代码计算，不由 LLM 命名或猜测。
2. 只有 manifest 状态为 `ready` 且复用前 audit 通过的 env 才能命中。
3. 同一 `spec_fingerprint` 在同一 resource root 下最多有一个创建者。
4. 每个 run 只记自己使用的资源；物理 env 可跨 run 复用，但账目不合并。
5. session workspace 始终私有；repo/env/cache 的复用不得把日志混到其他任务。
6. 复用失败要结构化降级为新建或 blocked，不得静默使用不匹配的 env。
7. 清理默认 dry-run，运行中的 run、有效 lease 和人工 pin 永不自动删除。
8. 所有物理路径删除前必须 resolve 并校验仍位于配置的 resource root 内。

## 4. 环境身份模型

### 4.1 两级指纹

不将“想安装什么”和“最后安装了什么”混成一个 hash。

#### `spec_fingerprint`

表示请求的环境规格，用于创建前查找候选 env。规范化输入至少包含：

- Python major/minor 和必要 ABI 约束；
- OS、CPU 架构；
- accelerator 类型（cpu/cuda/rocm/mps）及框架二进制变体；
- `environment.yml`、`requirements*.txt`、`pyproject.toml`、`setup.py`、lock file 等依赖声明的内容 hash；
- 用户或上游任务明确要求的 framework/CUDA 约束；
- 会影响二进制变体的 channel/index 选择。

`repo_commit` 记录在 provenance 中，但不默认进入 env 身份。否则每次只改模型代码都会创建新 env。依赖声明变化已由文件内容 hash 捕获；如果依赖来自特定 Git revision，该 revision 作为依赖条目进入指纹。

#### `resolved_fingerprint`

表示创建后的实际库存，用于漂移检测。至少由以下规范化结果计算：

- conda explicit package inventory；
- pip package inventory；
- Python 版本；
- framework 版本、编译 CUDA/ROCm 版本；
- 必要的系统 ABI 摘要。

复用前重新计算 resolved fingerprint。与 manifest 不一致时标记 `drifted`，不静默继续使用。

### 4.2 规范化要求

- JSON 键排序、UTF-8、稳定换行；
- 依赖文件按 repo 相对路径排序；
- 不包含 workspace 绝对路径、run id、task id、时间戳、mirror 的临时域名；
- 包含会改变依赖语义的 index/channel 或 binary variant；
- 缺少字段与空字段的语义必须在 schema 中唯一；
- 四仓通过同一组 golden fixtures 证明指纹一致。

### 4.3 环境名与放置

```text
env_id = resenv_<project-slug>_<spec_fingerprint[:12]>
```

`project-slug` 来源（2026-08-16 补充）：编排模式取 ResAgent 传入的
`project_ref`；独立模式取 repo 目录的 **basename**（不含路径——同一 repo
在不同路径下 slug 必须相同）；两者均按 `contracts/README.md` 的规范化
规则处理，为空时回退 `"project"`。slug 仅供人读，环境身份完全由指纹
承载，不同项目 slug 撞名无害。

新增配置：

```yaml
resources:
  root: /root/autodl-tmp/resagent-resources
  reuse_mode: legacy | content_addressed
  cleanup:
    enabled: false
    max_bytes: 0
    min_unused_days: 30
```

建议布局：

```text
<resource_root>/
  environments/
    <env_id>/
      manifest.json
      audits/
      usage/
  locks/
    <spec_fingerprint>.lock
  conda-envs/
    <env_id>/
  cache/
    pip/
    datasets/
```

resource root 必须可配置，不能写死 AutoDL 路径。云端可指向数据盘；本地可放在 workspace root 的受管理子目录。

## 5. `ENVIRONMENT_MANIFEST_V1`

manifest 是物理 env 的权威索引卡，不是日志摘要。建议 schema：

```json
{
  "schema_version": "environment_manifest_v1",
  "env_id": "resenv_torchdiffeq_12ab34cd56ef",
  "prefix": "/data/resources/conda-envs/resenv_torchdiffeq_12ab34cd56ef",
  "manager": "reproagent",
  "state": "ready",
  "spec_fingerprint": "...",
  "resolved_fingerprint": "...",
  "spec": {
    "python": "3.10",
    "platform": "linux-x86_64",
    "accelerator": "cuda",
    "framework_variant": "torch-cu124",
    "dependency_files": []
  },
  "provenance": {
    "repo_origin": "...",
    "repo_commit": "...",
    "created_by_run": "...",
    "created_by_task": "..."
  },
  "certification": {
    "level": "experiment",
    "certified_at": "...",
    "hardware": "NVIDIA RTX 4090 D",
    "cuda_driver": "...",
    "audit_artifact": "audits/audit-....json"
  },
  "created_at": "...",
  "last_used_at": "...",
  "pinned": false
}
```

状态机：

```text
creating -> ready -> drifted
    |          |         |
    v          v         v
  failed    invalid   deleting -> deleted
```

规则：

- 先原子写临时 manifest，再 rename 为正式文件；
- `creating` 不得被其他任务复用；
- 进程崩溃留下的 `creating` 必须由恢复流程识别；
- `ready` 只表示物理创建完成，certification 级别另行判断；
- CodingAgent 最高写 `verification`，reproagent audit 通过后才可升为 `experiment`；
- manifest 更新 `last_used_at` 必须原子写入。

## 6. 创建、复用与认证流程

### 6.1 确定性查找

```text
收集 EnvironmentSpec
  -> 规范化
  -> 计算 spec_fingerprint
  -> 在 resource root 查 manifest
  -> 无候选：进入创建
  -> 有候选：校验 state/prefix/spec
  -> 重算 resolved_fingerprint
  -> 按调用所需 certification 审计
  -> 命中或结构化拒绝
```

LLM 可以帮助理解 README 和选择安装策略，但不参与 fingerprint、manifest 状态、锁和清理候选的判定。

### 6.2 并发创建

1. 以 `spec_fingerprint` 获取创建锁；
2. 获锁后再次查找 manifest，防止等待期间已由其他任务建成；
3. 写 `creating` manifest；
4. 在受管理 prefix 创建 env；
5. 安装、audit，写 resolved fingerprint；
6. 原子转为 `ready`；
7. 释放锁。

锁实现优先使用标准库可验证的原子创建，锁文件记录 host、pid、started_at 和 heartbeat。不得只按“超过 N 分钟”就删锁；恢复前必须确认持有者不再活跃。

### 6.3 验证级到实验级

CodingAgent `auto` 创建的 env 可被登记为 `verification`。当 reproagent 接手实验任务时：

1. 按 fingerprint 确认是同一规格；
2. 运行硬件、CUDA、framework、import 和 repo-specific audit；
3. 通过后追加 audit artifact，将 certification 升为 `experiment`；
4. 失败时根据权限修复或返回结构化 blocker，不伪造认证。

### 6.4 漂移处理

- `frozen`：检测到漂移立即 blocked；
- `reuse_only`：不就地升级重型框架，需要时返回新建请求；
- `auto`：默认创建新 fingerprint 的 env，不在已被其他 run 引用的 env 上盲修；
- 原 env 保留为 `drifted`，由清理策略后续处理。

**操作员（reproagent）自身复用路径的裁定**（2026-08-16 补充）：检测到
漂移时一律**拒绝复用并将 manifest 置为 `drifted`**，返回结构化
blocker（env_id、manifest 路径、期望/实际 resolved_fingerprint、漂移
细节）。操作员不得自动新建同指纹环境（env_id 由指纹派生，重建必然撞
名；且新建是资源消耗决策），也不得就地修复（是否被其他 run 引用是全
局状态，本地不可判）。漂移后的删除重建/换前缀新建/升级用户由
M2-P3（ResAgent 跨 run 资源选择）决策。legacy 模式行为不变。

## 7. 跨 run 登记与清理

### 7.1 两层登记

- 全局物理层：resource root 中的 manifest，描述 env 本身；
- run 逻辑层：`ResearchState.resources`，只记当前 run 使用过的 env、repo、manifest 和 audit artifact。

ResAgent 不维护另一个不可审计的隐式数据库。跨 run 发现以 manifest 为事实源，run state 作为使用记录。

### 7.2 lease 与 pin

任务派发前登记 lease，任务终止时释放。lease 至少记录 run_id、task_id、host、pid 和 heartbeat。崩溃恢复要同时检查 run 状态和进程活性。

`pinned=true` 表示人工保留，不受 LRU 影响。

### 7.3 安全清理

清理分两步：

```text
plan cleanup -> 输出候选、原因、大小、最后使用时间
apply cleanup -> 用户确认后由 manifest.manager 对应执行模块删除
```

默认不在 research loop 中自动清理。首版 CLI 必须 dry-run，显式 `--apply` 才可执行。ResAgent 可统筹候选和保护集，但物理删除由创建/管理该 env 的模块执行。

## 8. 版本化契约

P0 在 ResAgent 冻结下列与语言无关的 JSON 契约：

```text
ENVIRONMENT_SPEC_V1
ENVIRONMENT_MANIFEST_V1
ENVIRONMENT_AUDIT_V1
RESOURCE_LEASE_V1
RESOURCE_CLEANUP_PLAN_V1
```

黄金 fixtures 覆盖：

- CPU 与 CUDA 规格不同指纹；
- 不同 CUDA/framework binary variant 不同指纹；
- 仅 repo 绝对路径或 run id 变化时指纹不变；
- 依赖文件内容变化时指纹必变；
- 普通代码 commit 变化、依赖声明不变时指纹不变；
- manifest 完整/缺字段/未知 schema 版本；
- drift、failed、stale creating 和活跃 lease。

三个实施仓库保持独立，不通过本地绝对路径互相 import。各仓对同一 fixture 必须得到相同解析和 fingerprint 结果；任何 schema 变更先升版本，再实施。

## 9. 按模块的开发任务

### 9.1 ResAgent

1. 添加契约 schema 和 golden fixtures，不改运行逻辑；
2. 扩展 `ResourceRef`：manifest_path、spec/resolved fingerprint、certification、last_used_at、manager、state；
3. 配置 `resource_root`、`reuse_mode`和 cleanup policy；
4. 从 session bindings 回收新字段，兼容旧卡片缺字段；
5. 派发前按精确 spec 与 certification 选择 env，注入 env name/prefix；
6. 记录 lease 和 run 引用，不直接执行 conda/pip；
7. 生成 cleanup plan，经确认后路由到 manifest.manager；
8. 增加上下文摘要，只向 LLM 提供资源证据，不把全量 package list 塞进 prompt。

### 9.2 reproagent

1. 实现 spec 收集与确定性 fingerprint；
2. 实现 resource root、manifest 状态机、原子写和创建锁；
3. `ensure_environment` 变为“精确复用或创建”，保留 legacy 模式；
4. 复用前重审计，记录 resolved fingerprint 和 drift；
5. 维持实验级认证权，audit artifact 必须可追溯；
6. `session.yaml` bindings 写 manifest_path、fingerprints、certification 和 prefix；
7. 暴露可测的 inspect/prune 维护入口，默认 dry-run；
8. 保留独立 CLI 可用性，不要求必须经 ResAgent 调用。

### 9.3 CodingAgent

1. `auto` 模式按 V1 spec/manifest 创建或复用验证级 env；
2. `reuse_only` 只绑定符合契约的现有 env，禁止重型框架漂移；
3. `frozen` 不修改 env，漂移或缺依赖时结构化 blocked；
4. 写入 verification 级 certification，不越权写 experiment；
5. 独立调用与 ResAgent/reproagent 绑定模式共用同一契约；
6. 暴露自己管理的 env 的 inspect/prune 入口；
7. 保持通用编程 agent 定位，不加科研特化 prompt。

### 9.4 ExpAgent

无代码改动。ExpAgent 仍只输出科学动作和逻辑依赖，不输出 env fingerprint、prefix、manifest 路径或清理决策。

## 10. 实施阶段与分支

### M2-P0：契约冻结

- 仓库：ResAgent
- 分支建议：`codex/resource-management-m2`
- 交付：schema、fixtures、字段语义、兼容策略、验收脚本骨架
- 门禁：三仓评审 fixture 无分歧；运行行为不变

### M2-P1：reproagent 环境管理器

- 分支建议：`feat/content-addressed-envs`
- 交付：fingerprint、manifest、lock、audit、legacy/content-addressed 开关
- 门禁：reproagent 旧测试全绿；同 spec 第二次零创建；并发只创建一次

### M2-P2：CodingAgent 独立环境闭环

- 分支建议：`codex/environment-resource-v1`
- 交付：auto/reuse_only/frozen 与 V1 manifest 对齐、verification 认证
- 门禁：独立创建可复用；被编排时不擅自破坏环境

P1 与 P2 在 P0 后可并行，但不得相互修改仓库。

### M2-P3：ResAgent 跨 run 资源选择

- 依赖：P1 + P2
- 交付：资源回收、精确选择、lease、指针注入、恢复逻辑
- 门禁：同 run 与跨 run 均不依赖环境名猜测；旧 session 可读

### M2-P4：安全清理

- 交付：inspect、cleanup plan、dry-run、explicit apply、pin/lease 保护
- 门禁：路径越界删除测试、活跃 run 保护、中断恢复

### M2-P5：四模块与云端验收

- 先跑确定性 fixture 和无 GPU 闭环；
- 再跑一次真实 GPU 环境创建；
- 使用同一 spec 开新 run，证明零安装复用；
- 修改依赖声明，证明创建新 env；
- 人工制造漂移，证明旧 env 不被盲目命中；
- 输出合并凭证：四仓 commit/dirty、manifest、audit、cache hit、env hit、GPU 证据。

## 11. 测试与验收矩阵

| 类别 | 用例 | 通过标准 |
|---|---|---|
| 指纹 | 同 spec、不同路径 | fingerprint 相同 |
| 指纹 | requirements 变化 | fingerprint 必变 |
| 指纹 | CPU vs CUDA / cu121 vs cu124 | fingerprint 必变 |
| provenance | 只改 repo 代码 commit | env 指纹不变，commit 记录更新 |
| manifest | 创建成功/失败/崩溃 | 状态可恢复，无假 ready |
| 并发 | 两进程同 spec | 仅一个创建，另一个等待后命中 |
| 复用 | 第二个 run 同 spec | 不创建 env，不跑安装命令 |
| 认证 | CodingAgent env -> reproagent | verification 经 audit 后升 experiment |
| 漂移 | 手动 pip install/uninstall | resolved fingerprint 不一致，拒绝盲复用 |
| 兼容 | 旧 run/state/session 无新字段 | 可读，按 legacy 策略运行 |
| 清理 | active lease / pinned / 越界路径 | 永不删除 |
| 清理 | 无引用且超 LRU 阈值 | dry-run 可见，apply 后 manifest 与 env 一致消失 |
| 云端 | 首次 + 二次 GPU run | 首次创建，二次零安装，GPU/audit 证据完整 |

## 12. 迁移与回滚

1. 新模式首先通过 `reuse_mode=content_addressed` 显式开启；默认保留 `legacy`。
2. 现有 `repro_*` / `resenv_<run_id>` 环境不自动改名、移动或删除。
3. 可提供 `inspect/import legacy env` 工具，只有审计通过后才写 V1 manifest。
4. 新字段全部可选，旧 state/session 视为“未登记资源”，不视为损坏。
5. 任何阶段可切回 legacy，不影响已验收的 V2 科学编排逻辑。
6. 在 M2-P5 通过前，不删除 legacy 实现和测试。

## 13. 非目标

本里程碑不做：

- 不改 V2 capability 词表、analysis coverage 或 finish gate；
- 不重写 conda/pip 为 Docker；Docker 仍是后续可选 backend；
- 不将四仓合并为 monorepo；
- 不引入长驻资源服务、数据库或分布式调度器；
- 不让 ExpAgent 处理物理资源身份；
- 不用 LLM 替代指纹、锁、manifest 和清理的确定性逻辑；
- 不为单台 AutoDL 服务器写死路径、GPU 型号、镜像或驱动版本。

## 14. 完成定义

只有同时满足以下条件，里程碑二才算完成：

1. V1 契约在 ResAgent、reproagent、CodingAgent 上对同一 fixtures 结果一致；
2. 同 spec 同平台跨 run 真正命中 env，而不只是 pip cache 命中；
3. 依赖、Python、架构或 accelerator 变体不同时不会误复用；
4. 并发创建、崩溃恢复和漂移检测有确定性测试；
5. CodingAgent 独立调用仍可自建验证环境，编排调用仍遵守 env_policy；
6. reproagent 仍是唯一实验级认证者；
7. ResAgent 能追溯每个 run 使用的 manifest、audit 和物理 prefix；
8. cleanup 默认 dry-run，活跃、pinned 和越界资源的保护测试全绿；
9. 四仓现有测试和 V2 确定性闭环无回归；
10. 云端验收报告同时记录 cache hit、env hit、fingerprint、manifest、GPU audit 和四仓 provenance；
11. 文档与代码一致，active 文档移入 completed，并为验收 commit 打统一 tag。
