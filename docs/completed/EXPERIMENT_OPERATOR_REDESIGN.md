# 实验操作员重定位与执行层重构 — 开发文档

**日期**: 2026-08-13
**状态**: 里程碑一 P0-P4 已完成，已通过 V2 云端全量验收并合并（tag `v2-validated-2026-08-15`）。本文作为里程碑一的设计与实施档案；原 §9 的里程碑二已抽离为 [`../active/RESOURCE_MANAGEMENT_MILESTONE_2.md`](../active/RESOURCE_MANAGEMENT_MILESTONE_2.md)。
**关联文档**: [CONVERSATION_LAYER_DESIGN.md](../reference/CONVERSATION_LAYER_DESIGN.md)、[SESSION_AND_PROJECT_MODEL.md](../reference/SESSION_AND_PROJECT_MODEL.md)
**涉及模块与分工**:

| 模块 | 改动量 | 负责方 |
|------|--------|--------|
| reproagent → 实验操作员 | 中（P1） | 按 §8.1 任务书 |
| CodingAgent | 中小（P2） | 按 §8.2 任务书 |
| ExpAgent | 极小（可选字段） | 按 §8.3 |
| ResAgent | 中（P3 链接规则） | 按 §8.4，P1、P2、P2.5 验收后开工 |

---

## 1. 背景与动机

### 1.1 触发问题

测试发现断链：ResAgent 想"先让 CodingAgent 改代码、再跑实验"，当前架构不支持。根因是 reproagent 的合同把 `paper_url`/`repo_url` 设为必填——它只能"clone 一个仓库来复现"，无法接手"一个已存在的、刚被改过的工作区"。

### 1.2 核心判断

**复现是一种目标，实验操作是一种能力。** reproagent 的全部基础设施（数据集缓存、pip 缓存、会话、项目级 env、resume）都是通用实验操作能力，模块按目标命名导致组合性被锁死。重定位为**实验操作员（Experiment Operator）**后：

```
复现论文   = 操作员(clone URL + 跑)
先改再跑   = CodingAgent(改 repo X) → 操作员(在 X 里跑)
迭代实验   = 操作员(resume，换参数再跑)
```

统一为一份合同、三种工作区模式（§4）。

### 1.3 角色教义矩阵（最终分工）

| 模块 | 角色 | 教义 | 明确不做 |
|------|------|------|----------|
| ExpAgent | 科学专家 | 判断：问答/讨论/设计/分析/归因 | 不改码、不执行 |
| ResAgent | PM | 编排 + 会话/项目状态 | 不研究、不执行 |
| CodingAgent | 通用编程 agent | 补丁纪律：最小 diff、结构化编辑、修复循环、改完必验证；**全能力含环境配置**，权限按调用方式分级（§3） | 不宣布实验结果/指标 |
| 操作员 | 实验操作员 | 实验规程：探接口 → 小跑验证 → 正式跑；指标必须有日志证据；**实验级环境的认证权**与缓存/审计的所有权 | 不改科学语义（需要时委托 CodingAgent，编排模式下须 ResAgent 路由） |

**环境所有权三段式**（评审修订）：创建对称（CodingAgent 可建"验证级" env，操作员可建"实验级" env）；**认证权仅归操作员**（audit_env 通过才算实验级）；**登记与传递仅归 ResAgent**。

**三权诚信防线**（写进各模块 prompt 与名片）：判断（ExpAgent）/ 测量（操作员）/ 修改（CodingAgent）分离。操作员报告里每个 [MET] 必须有日志文件证据；CodingAgent 的报告只关乎补丁，无权声明实验成功。

**路由试金石**：任务的主要产出是 diff → CodingAgent；是实验结果 → 操作员；先 diff 后结果 → 链式调用。

### 1.4 命名决策

角色/prompt/名片/文档全部改为"实验操作员"；**包名 `reproagent` 保留**（包名牵扯 import、conda env 命名、会话卡片、config、四仓库文档，换名收益远小于成本）。路由 LLM 读名片不读包名。`ReproTask` 等类名同理保留，docstring 改述。

## 2. 操作员化的概念模型

reproagent 是 CodingAgent 的**科研变体，变在教义层而非引擎层**：约七成代码是科研执行外层（env/audit/缓存/报告/规程 prompt），三成机械件（llm/context_policy/loop 骨架）与 CodingAgent 平行实现。**不做代码级包裹**（两 loop 形状不同：编辑中心 vs 命令中心；硬共引擎会扭曲两边并破坏独立演进）。关系落在编排级：操作员通过 `call_coding_agent` 合同委托代码工作。共享引擎抽取留作未来演进，本期不做。

## 3. CodingAgent 全能力化与 env_policy 分级

先例：Claude Code permission modes / Codex approval modes——**能力恒定，权限按调用方式分级**。

```python
class CodeTaskSpec:
    # 新增三个可选字段，缺省即现状（向后兼容）
    repo_url: str = ""      # 给了就先 clone（--depth 1）到 workspace_path 再开工
    branch: str = ""        # 可选分支
    env_policy: Literal["auto", "reuse_only", "frozen"] = "auto"
    env_name: str = ""      # reuse_only/frozen 时指向已存在的 conda env
```

| env_policy | 能力 | 调用方 |
|---|---|---|
| `auto`（默认） | 完整环境能力：可建 env、装包、配依赖 | 独立使用 / 原创代码任务 |
| `reuse_only` | 使用 env_name 指定环境；允许装缺失的小依赖；**禁止**装/升/删重型框架（torch/tf/jax 等），禁止建/删 env | ResAgent shared 链路 |
| `frozen` | 完全不许碰环境；缺依赖如实报告（residual_risks 或 needs_user_input） | 操作员内部委托（替代现在 constraints 里的口头约定，升级为可测机制） |

**env 绑定实现**：`env_name` 非空时，`run_command`/`verify_commands` 经 `conda run --no-capture-output -n <env_name> bash -c` 包装（参照 reproagent `build_backend_command`）；conda 查找顺序 `CONDA_EXE` 系环境变量 > PATH > 常见安装路径。**安全黑名单在包装之前执行**，安全边界不变。

**clone 实现**：`repo_url` 非空时，`workspace_path` 必须不存在或为空目录 → `git clone --depth 1 [--branch] <url> <workspace_path>`；失败清晰报错。clone 目标仍受既有路径安全层约束。

## 4. 工作区三模式（操作员合同核心）

```python
class ReproTask:  # 概念即 ExperimentTask，类名保留
    paper_url: str = ""           # 必填 → 可选（复现场景的参照）
    repo_url: str = ""            # 必填 → 可选（给了就 clone）
    copy_from: str = ""           # 新增：拷贝本地 worktree（保留未提交改动）
    external_repo_path: str = ""  # 新增：就地使用外部 repo（shared 模式）
    setup_only: bool = False      # 新增：只建环境不跑实验
    # 其余字段不变（env_namespace / isolate_env / dataset_cache_dir / parent_run / ...）
```

| 模式 | 触发 | 行为 | 场景 |
|------|------|------|------|
| **isolated**（默认） | 给了 repo_url | clone 进自己 workspace，自建/复用 env | 纯复现 |
| **copy** | 给了 copy_from | `cp -a` 本地 worktree 进 workspace/repo（含未提交改动——git clone 本地路径会丢，这是先改再跑链上唯一的物理陷阱） | 想隔离地跑本地改过的代码 |
| **shared** | 给了 external_repo_path | 不 clone 不拷贝，直接以该路径为 repo_path；logs/state 仍写在自己 workspace | 先改再跑、迭代研究 |

校验（评审修订）：**来源互斥**——`repo_url` / `copy_from` / `external_repo_path` / 复用 workspace 已有 repo，四者只能明确给一个；同时给出多个**直接报错，不按优先级猜测**（优先级可能悄悄忽略用户指定的本地修改）。全部缺省时才允许复用 workspace 已有 repo（resume 语义）。

**数据集缓存写入边界**（评审修订，纠正原文错误断言）：`dataset_cache.py` 按 `repo_path.parent` 解析，shared 模式下 repo 位于 `project_ws/repos/foo` 时，`../data` 实际落到 `project_ws/repos/data` 而非 `project_ws/data`；更危险的是 external 模式下可能在外部仓库旁（workspace 之外）建软链。修复：`prepare_dataset_links` 显式接收"**允许写入的 workspace 根**"参数，解析结果越界则跳过该链接并在 prompt 块中报告，不再盲目依赖 `repo_path.parent`。

## 5. 资源管理模型（2026-08-13 增补，采纳用户提案）

### 5.0 现有能力基线：保留并复用，不重复建设

本重构不是从零建设缓存和环境复用。当前系统已经具备以下能力，里程碑一必须保持行为不变：

- reproagent 已有 pip wheel 缓存，可避免相同 Python/平台/包版本的 wheel 重复走公网下载；
- reproagent 已有数据集缓存和路径桥接，MNIST 等数据可跨任务复用；
- `env_namespace` 已支持同一 ResAgent run 内多个实验任务复用同名 conda env，`isolate_env=True` 时仍可强制任务级隔离；
- `mirror_profile` 已支持国内镜像策略；服务器还可通过 `.condarc`/pip 配置选择可用镜像；
- 已有 env-reuse 集成测试证明同一 run 内的基础环境复用链可工作。

因此两个里程碑的区别是：

- **里程碑一不重写缓存系统**，继续使用现有 pip/数据集缓存和 `env_namespace`，只修复“准备仓库/环境 -> 修改代码 -> 在同一对象上运行实验”的断链；
- **里程碑二升级复用判断**，解决“内容相同但名字不同导致重复建 env”和“名字相同但依赖不兼容导致错误复用”，并补 manifest、锁、审计、漂移检测和清理。

验收时必须分别检查“是否重新下载”和“是否重新创建环境”。二者不是一回事：缓存命中仍可能新建环境，环境命中才会跳过安装。

### 5.1 四类资源、两个共享层级

| 资源 | 默认 | 共享时机 | 表达方式 |
|------|------|----------|----------|
| repo 工作区（实验对象） | 私有（clone/copy 副本） | 项目级可共享 | 路径指针（external_repo_path / workspace_path） |
| conda env | 私有（repro_<task_id>） | 项目级可共享（resenv_<run_id>） | 命名引用（env_name / env_namespace） |
| 会话工作区（logs/state/卡片） | **永远私有** | 永不共享 | — |
| 机器级缓存（数据集/pip） | **永远共享** | 无需选择 | 路径约定 + env 注入 |

注意区分：**共享的是"实验对象"（repo），不是"实验现场"（会话工作区）**。shared 模式下操作员的 logs/state/session.yaml 仍在自己的私有目录。

### 5.2 所有权与身份：资源属于项目，身份由内容决定

资源创建出来即归属 run/会话，创建者只是经手人。但**项目只是资源的登记簿，不是命名空间**——一个完整科研过程会涉及多个 repo、多个 env（复现多个 baseline、改自己的 repo、不同 baseline 依赖不同框架版本），所以资源身份必须由内容唯一确定：

- **repo 身份 = slug**：从 URL 或本地路径派生（`github.com/pytorch/examples` → `pytorch_examples`）；
- **env 身份（里程碑一）**：维持现状命名（任务级 `repro_<task_id>` / 编排级 `resenv_<env_namespace>`）；
- **env 身份（里程碑二，评审修订后延后）= `resenv_<slug>_<fingerprint>`**：fingerprint 必须覆盖依赖声明（requirements/pyproject/setup.py/environment.yml）+ python 版本 + **平台/CPU 架构 + CUDA/框架变体 + repo commit**；且仅凭命名复用不安全，需配套 **env manifest**（记录指纹、创建时间、审计产物）、**创建文件锁**（防并发重名）与**复用前重新审计**。这些是独立工程量，不与基本执行链同时落地（见 §9 里程碑二）。
  - 同 repo 同指纹 → 同一 env（复跑 / resume / 另一任务复用，零安装命中）；
  - 依赖变化 → 指纹变 → 自动成为新 env，新旧并存互不踩踏；
  - env 建成后被增量装包会"漂移"：hash 只描述初始规格，`audit_env` 记录实际状态；漂移检测属里程碑二。

run 级资源表存于 `ResearchState.resources`，repo↔env 有关联：

```yaml
resources:
  - kind: repo
    id: pytorch_examples
    path: tasks/reproagent/task_001/.../repo     # isolated 复现对象
    origin: https://github.com/pytorch/examples.git
  - kind: env
    id: resenv_pytorch_examples_a1b2c3d4
    repo: pytorch_examples                        # 关联：服务哪个 repo
    certified: true                               # audit_env 通过 = 实验级
  - kind: repo
    id: my_research
    path: project_ws/repos/my_research            # 迭代研究对象，两模块共享
```

**跨 run 物理复用是白送的**：conda env 名在机器上全局唯一，内容寻址命名使另一个 run 遇到同 repo 同依赖时自然命中同一 env；但逻辑登记保持项目级（每个 run 只记自己用过的），隔离账目清晰、物理复用免费。

阶段边界：里程碑一只实现支撑当前 run 链式派发所需的**最小资源表**（repo/env 的路径、名称、来源、创建任务和简化认证记录），不做跨 run 自动匹配；上面的内容寻址身份、完整 manifest 与跨 run 安全复用属于里程碑二。

### 5.3 放置规则、对称创建与派发配对

**repo 放置两类**（关键决策点）：

| 类别 | 位置 | 场景 |
|------|------|------|
| 一次性复现对象 | 任务私有 workspace（isolated，现状） | 跑 baseline 拿个数 |
| 迭代研究对象 | `project_ws/repos/<slug>/`（共享区） | 改→跑→再改→再跑（自己的代码或要改进的 baseline） |

**对称创建与绑定**（解决"不知道先调谁"）——每个模块的统一行为规则：

```
给了指针（env_name / external_repo_path / workspace_path）→ 绑定复用
没给指针、但内容寻址的资源已存在（resenv_<slug>_<hash> / repos/<slug>）→ 复用
都没有 → 按自己的默认规则自建（独立可用性不变）
```

确定性命名 + run loop 顺序执行 ⇒ 先来先建、后到复用、无竞态。

**ResAgent 派发配对**：实验任务要跑 repo X → 查资源表 X 的注册 env → 有则注入 `env_name`，无则让操作员建并回登记；链式继承规则不变（实验任务缺省继承前一 coding 任务的 repo+env 对）。

### 5.4 注册通道：会话卡片 bindings（不建新通道）

模块创建/使用资源后写入卡片 bindings（conda_env、repo 路径、缓存路径）。ResAgent 任务完成后读卡片并入资源表；派发前从资源表注入指针到 task.input。**卡片契约直接承担注册职能。**

### 5.5 认证不对称

对称创建 ≠ 对称认证。CodingAgent 建的 env 是"验证级"（够跑 verify）；操作员跑实验前的 `audit_env` 是"实验级"把关（CUDA 变体、依赖一致性），不合格则修复。教义边界因此保住。

认证记录不是布尔值（评审修订）：`certified: true` 升级为认证对象——`{certified_at, hardware, cuda_version, env_fingerprint, audit_artifact}`（audit_artifact 指向审计日志路径）。里程碑一可先写简化版（certified_at + audit_artifact），完整指纹随里程碑二。

### 5.6 call_coding_agent 去留（决议：不删，加开关）

```python
class ReproTask:
    allow_code_delegation: bool = True   # 独立使用默认开（自愈能力）
```

ResAgent 编排时传 `False`：遇代码问题以结构化状态退出（blocked + coding_issues 清单），由 ResAgent 路由 CodingAgent 后 resume 操作员会话。与 env_policy 同一哲学：能力恒定，权限按调用方式分级。

### 5.7 多 repo / 多 env 示例

```
目标：改进 attention，和两个 baseline 比（A: pytorch 官方 CNN；B: 某老 repo，要 torch 1.x）

task_001 操作员复现 A   → 登记 repo:A(isolated) + env:resenv_A_h1（torch 2.x）
task_002 操作员复现 B   → 登记 repo:B(isolated) + env:resenv_B_h2（torch 1.x，与 A 并存）
task_003 CodingAgent 在 project_ws/repos/my_research 实现新模块
                        （env_policy=auto，自建验证级 env 并登记）
task_004 操作员在 my_research 跑实验
                        → 查表拿到 task_003 的 env，audit 认证为实验级
task_005 操作员 resume task_001 会话补跑 A 的 5-epoch 版
                        → env resenv_A_h1 直接命中，零安装
```

每步指针都来自资源表；多 repo 多 env 就是表里的多行。

### 5.8 清理策略（分层）

- **机器级缓存**（数据集/pip）：永久，手动清；
- **env**：机器级 LRU——磁盘超阈值清最久未用；重建便宜（pip 缓存兜底）；
- **isolated repo 副本**：随任务 workspace 生灭，删除不影响 env；
- **project_ws/repos/**：随 run 归档存活；删会话不连带删 env（会话模型既定规矩）。

### 5.9 bindings 跨模块子模式（评审新增：契约先行）

资源注册依赖卡片 bindings，但当前各模块现状不一（reproagent 仅有 conda_env；CodingAgent 无 bindings；reproagent 的 project_path 指向会话现场而非实验 repo）。因此先统一定义 bindings 子模式，各模块按此写出，ResAgent 按此回收：

```yaml
bindings:
  repo:
    path: /abs/path/to/repo        # 实验对象 repo（非会话现场）
    origin: https://... | local    # 来源
    commit: a1b2c3d                # 可选
    mode: isolated | copy | shared
  environment:
    name: resenv_xxx
    policy: auto | reuse_only | frozen   # 创建/使用该 env 时的权限档（§3）
    fingerprint: ""                # 里程碑二填
    certification: none | verification | experiment
    certified_at: ""               # certification=experiment 时必填
    audit_artifact: ""             # 审计日志相对路径
  dataset_cache: /root/autodl-tmp/datasets
  pip_cache: /root/autodl-tmp/pip-cache
```

兼容性：读者必须容忍旧卡片缺少 `bindings` 或其内缺少 `repo`/`environment` 段（一律视为未登记）；写字段只增不改。

## 6. setup_only：环境 provisioning 的一等任务

有了 §5 的对称创建后，"先改代码时 env 不存在"不再是硬问题（CodingAgent 可自建验证级 env）。但把"配环境"做成一等可调度任务仍有价值——实验级 provisioning（CUDA 对齐、镜像、数据集接线）是操作员专长，显式排一个 setup_only 任务比指望链上顺手建好更可控。**ResAgent 不学建 env**，只排序：

```python
setup_only=True  # clone/copy → 建 env → 装依赖(environment.yml/requirements)
                 # → audit_env → 以环境摘要 finish，不跑实验
```

标准 shared 链（纯任务排序，零新机制）：

```
task_1  操作员(setup_only)                      → resenv_<run_id> 就绪
task_2  CodingAgent(env_policy=reuse_only, env_name=resenv_<run_id>)  → 改码 + 验证
task_3  操作员(external_repo_path=同一 repo)     → 同 repo 同 env 直接跑
```

setup_only 对纯复现同样有用（预建环境后反复 resume 跑实验）。

## 7. ResAgent 链接规则（P3）

1. **任务字段透传**：`build_reproagent_context` 与 `_actions_to_tasks` 透传 `copy_from`/`external_repo_path`/`setup_only`。
2. **链式继承**：experiment/run 类任务未显式给出工作区时，继承**最近一次 coding 任务的 workspace_path** 作为 external_repo_path（复用现有 `_infer_workspace_path` 的推断位）。
3. **模式开关**：`policy.shared_workspace: auto | always | never`，默认 `auto`——目标/任务含本地 repo 路径时走 shared，纯 URL 复现走 isolated。
4. **run 级工作区**：shared 模式下 ResAgent 在 run 创建时定 `runs/<run_id>/project_ws/`（clone URL 或绑定本地路径），作为两模块的共同指向；记入 run state 与索引卡 bindings（`workspace_mode` 可审计）。
5. **名片更新**：ResAgent 内置名片中 reproagent 描述改为实验操作员（三模式 + setup_only）；CodingAgent 名片补 env_policy 说明。

## 8. 模块任务书

### 8.1 reproagent → 实验操作员（P1，~200 行 + 测试）

| 项 | 内容 |
|---|---|
| O1 | `ReproTask` 四字段选填化 + `copy_from`/`external_repo_path`/`setup_only` 新增；校验"至少一个 repo 来源"，报错信息可读 |
| O2 | `clone_repo` 重构为 `setup_workspace`：三模式分发；copy 用 `cp -a`（保留未提交改动与 .git）；external 模式校验路径存在且是 repo |
| O3 | `setup_only`：provision 完成后以环境摘要 finish，报告写明 env 名/已装包/audit 结论 |
| O4 | `SYSTEM_PROMPT` 重定位："你是实验操作员；当给出 paper_url 时目标才是复现"；报告模板 Deviations 从"vs paper"泛化为"vs goal"；Metrics 证据表不变 |
| O5 | 内部委托 CodingAgent 时传 `env_policy="frozen"`（依赖 CodingAgent P2 字段；字段缺失时维持现状口头约束，优雅降级） |
| O6 | CLI：`run` 的 `--paper/--repo` 改可选，新增 `--copy-from/--external-repo/--setup-only` |
| O7 | `agent.yaml` 重写为实验操作员描述（含三模式与 setup_only 的正反适用例） |
| O8 | 索引卡 bindings 按 **§5.9 子模式**写出（repo path/origin/commit/mode + environment name/certification + 缓存路径）——这是 ResAgent 资源表的注册通道（§5.4） |
| O9 | `allow_code_delegation: bool = True`（§5.6）：False 时遇代码问题以 blocked + coding_issues 结构化退出，不自行委托 |
| O10 | env 命名里程碑一维持现状（任务级 / `env_namespace`）；内容寻址命名（§5.2 指纹版）随里程碑二实施 |

**验收**：
- 新测试：三模式各自的 workspace 准备行为；copy 模式保留未提交改动（在源 repo 改文件不 commit，copy 后内容在）；setup_only 不跑实验且 env 已建；external 模式 logs 落在自己 workspace 而 repo 未被复制；
- 复现场景回归（mock + 真实 MNIST smoke）；
- 现有测试全绿。

### 8.2 CodingAgent 全能力化（P2，~80 行 + 测试）

| 项 | 内容 |
|---|---|
| C1 | `CodeTaskSpec` 新增 `repo_url`/`branch`/`env_policy`/`env_name`（§3 语义） |
| C2 | clone 前置：repo_url → clone 到 workspace_path（须不存在或为空） |
| C3 | env 绑定：env_name 非空时 run_command/verify 经 conda run 包装；黑名单先检 |
| C4 | prompt 按 env_policy 分档：auto 明说可配环境；reuse_only 给限制清单；frozen 明说禁碰并要求如实上报 |
| C5 | README/名片更新：全能力定位 + env_policy 三档说明 |
| C6 | 索引卡补 bindings（§5.9 子模式）：记录 workspace_path（repo）、origin/commit、env_name/env_policy——当前 CodingAgent 卡片无 bindings，本项为新增 |

**验收**：clone→修改→验证全链 mock 测试；env_name 时命令被 conda 包装（断言命令形态）；reuse_only 下装 torch 被拒/被警告；frozen 下任何 env 改动被拦；现有测试全绿。

### 8.3 ExpAgent（极小；评审修订：只传逻辑引用，不传物理路径）

科学层不应感知执行层物理路径（与 ExpAgent 现有 prompt 约定一致：运行路径由 ResAgent 决定）。`SuggestedPlan` 的 run_task/repro_task 计划只增加**逻辑引用**字段：

```python
project_ref: str = ""        # 逻辑项目/repo 引用（如 slug 或前序任务标记）
depends_on: list[str] = []   # 依赖的前序 action_id（如 ["patch_model"]）
workspace_intent: Literal["shared", "isolated", ""] = ""  # 共享意图
```

物理 `workspace_path`/`external_repo_path`/`env_name` 一律由 ResAgent 在派发时解析注入（§8.4）。ExpAgent 不应猜测尚未分配的 `task_001`；`depends_on` 必须引用同一决策内稳定且非空的 `action_id`，由 ResAgent 转换为真实 task id。prompt 只需说明："先改后跑流程中，run 任务用 depends_on 引用前序 coding action_id，用 workspace_intent 表达共享意图"。现有测试全绿。

### 8.4 ResAgent（P3，依赖 P1 **和** P2；§7 全部 + §5 资源模型）

额外实现：

- **逻辑引用 → 物理指针的解析**：把 ExpAgent 的 `project_ref`/`depends_on`/`workspace_intent`（§8.3）解析为具体 `workspace_path`/`external_repo_path`/`env_name` 注入 task.input；链式继承规则（实验任务缺省继承前一 coding 任务的 repo+env 对）在此落地；
- **透传 CodingAgent 合同字段**：`repo_url`/`branch`/`env_policy`/`env_name`（故 P3 依赖 P2）；
- `ResearchState.resources` 资源表：ResourceRef{kind, id, path, origin, repo(关联), certification, created_by, created_task}，schema 按 §5.2/§5.9；
- **回收登记**：任务完成后按 §5.9 子模式从卡片 bindings 更新资源表；
- 编排时向操作员传 `allow_code_delegation=False`；遇 blocked+coding_issues 时自动生成 CodingAgent 任务并 resume 操作员会话。

**验收**：mock 端到端——brief 含本地 repo 路径时：run 内出现 setup_only → coding → external_repo 实验的任务链，且任务 2/3 的 workspace/env 指向同一处；`policy.shared_workspace=never` 时退化为 isolated；现有 102+ 测试全绿。

## 9. 实施顺序（评审修订：两里程碑拆分）

### 9.0 P0：跨模块合同冻结（所有实现开始前）

先在本文档中冻结以下逻辑合同，并为每个子模块提供相同的样例 fixture：

- `RepoBinding`：path/origin/commit/mode；
- `EnvironmentBinding`：name/policy/certification/audit_artifact；
- `project_ref`、`workspace_intent`、`action_id`、`depends_on(action_id)`；
- workspace 来源互斥规则；
- `env_policy` 三档权限；
- 新旧 `session.yaml` 的兼容读取规则。

P0 只定义合同与 fixture，不改变运行行为。合同未冻结前，P1/P2 不得各自发明字段含义。

**里程碑一：修复真实断链**（目标链路见下）

```
P1 ReproAgent 工作区/操作员合同（三模式互斥 + setup_only + delegation 开关 + 缓存写入边界）
P2 CodingAgent clone/env 合同（repo_url/branch + env_policy/env_name + 卡片 bindings）
   —— P1、P2 无相互依赖，可并行
P2.5 ExpAgent 逻辑引用（action_id 依赖图 + project_ref/workspace_intent；不传物理路径）
P3 ResAgent 集成（依赖 P1、P2、P2.5）：逻辑引用解析、链式传递、最小资源表回收
P4 服务器同步 + 真实链验证：
   远程仓库 → 准备 repo/env → CodingAgent 修改 → 操作员在同一 repo/env 运行
   → ExpAgent 分析结果；外加 MNIST 纯复现回归
```

**里程碑二：资源优化**（断链修复验证稳定后启动）

- `ResearchState.resources` 完整资源表与跨 run 复用
- 内容寻址 env（§5.2 指纹版：依赖+平台+CUDA 变体+commit）+ env manifest + 创建文件锁 + 复用前重审计
- 认证记录完整化（§5.5）与漂移检测
- LRU 清理策略

里程碑一刻意不做：内容寻址 env、跨 run 资源复用、LRU——先让链路跑通，再让资源变聪明。

### 9.1 分工与所有权

| 工作包 | 实施负责人 | 代码所有权 | 总体会话职责 |
|---|---|---|---|
| P0 跨模块合同与 fixture | 总体会话（本会话） | 只改 ResAgent 设计/合同测试材料 | 冻结字段、样例和验收口径 |
| P1 reproagent 操作员化 | reproagent 专用会话 | 只改 reproagent | 本会话审查合同、测试和兼容性 |
| P2 CodingAgent 全能力化 | CodingAgent 专用会话 | 只改 CodingAgent | 本会话审查 clone/env 安全边界 |
| P2.5 ExpAgent 逻辑引用 | ExpAgent 专用会话 | 只改 ExpAgent | 本会话审查科学层未泄漏物理路径 |
| P3 ResAgent 集成 | 总体会话（本会话） | 只改 ResAgent | 实现复杂编排、资源回收和恢复闭环 |
| P4 跨模块与云端验收 | 总体会话（本会话） | 测试优先；发现子模块问题只出问题单 | 统一复核四仓版本、产物和真实 GPU 结果 |

任何会话不得直接修改不归其所有的模块。发现跨模块缺陷时，生成带复现步骤、期望合同和验收标准的问题单，交给对应模块会话修复；修复后由总体会话同步验收。

### 9.2 阶段门禁与停止条件

每个工作包只有同时满足以下条件才算完成：

1. 本模块现有测试全部通过；
2. 新合同测试覆盖成功、冲突、失败和旧输入兼容；
3. `session.yaml` 产物符合 §5.9，且旧卡片仍可读取；
4. 没有把绝对服务器路径、密钥或特定机器配置写死进代码；
5. 代码和文档已提交到该模块自己的候选分支；
6. 总体会话审查通过后，下一依赖阶段才开工。

若测试暴露的问题超出当前工作包合同，先停止扩改并回到本文档更新设计；禁止为了让单个 E2E 通过而增加项目/服务器特判。

### 9.3 里程碑一交付与验收矩阵

| 层级 | 必测内容 | 通过标准 |
|---|---|---|
| ReproAgent 单元/集成 | isolated/copy/shared、setup_only、delegation 关闭、缓存越界 | 三模式互斥；copy 保留未提交改动；shared 不复制 repo；日志仍私有 |
| CodingAgent 单元/集成 | clone、目录冲突、三档 env_policy、conda 包装 | 不覆盖非空目录；安全检查在包装前；环境权限由代码强制 |
| ExpAgent 单元/集成 | action_id 依赖、project_ref、workspace_intent | 不输出物理路径；依赖均可解析；兄弟任务共享同一逻辑项目 |
| ResAgent 确定性 E2E | setup -> coding -> experiment -> analysis | repo/env 指针一致；任务状态闭环；无僵尸/重复任务 |
| 云端真实链 | 小仓库先改后跑 + Neural ODE MNIST 3 epoch GPU 回归 | GPU 有日志证据；指标有来源；旧纯复现行为不回归；缓存/环境命中分别记录 |

P4 通过后才宣告里程碑一完成。里程碑二不得提前混入 P1-P3，以免将“链路正确性”和“资源优化”问题混在一起。

## 10. 反模式清单

1. 不改包名/类名（reproagent/ReproTask 保留），只改角色与合同；
2. 不让 ResAgent 学建 env——env 创建对称、**认证权始终在操作员**（§5.5），setup_only 是载体；
3. 不合并两个执行模块（教义不同，见 §1.3；合并即失去测量/修改分离防线）；
4. shared 不设为默认——纯复现保持 isolated；
5. 引擎级共享本期不做（两 loop 形状不同，见 §2）；
6. 口头约束升级为字段时必须优雅降级（对方未实现 env_policy 时维持 constraints 文本）。

## 11. 迁移说明

- `ReproTask` 必填放松向后兼容：旧调用方两个 URL 照传，行为不变；
- `env_namespace` 已存在（resenv_<run_id>），shared 模式直接复用；
- 服务器同步：P1-P3 完成后同步四个仓库，先在隔离 workspace 跑一次"先改再跑"冒烟，再进日常用。
