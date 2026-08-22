# 四模块代码审查与简化整理方案

> 日期：2026-08-22
> 状态：Active，待四模块分别执行并由 ResAgent 会话总体验收
> 范围：ResAgent、ExpAgent、reproagent、CodingAgent
> 核心目标：保持现有功能和接口基本不变，删除无效复杂度，收束到一套可解释的主线实现
> 首要约束：防止 AI 过度设计，不以新增抽象、框架或文件数量作为整理成果

## 1. 背景

四模块已经完成多轮本地测试和真实云端闭环测试，当前系统能够完成：

```text
用户目标
  -> ResAgent 编排
  -> ExpAgent 科学规划
  -> CodingAgent 编写或修改代码
  -> ReproAgent 配置环境并执行实验
  -> ExpAgent 分析实验结果
  -> ResAgent 继续、询问或结束
```

现阶段的主要风险不再是“完全跑不起来”，而是长期迭代和逐次修复可能留下：

- 新旧逻辑并存；
- 只实现了一半、没有上下游消费者的结构；
- 同一概念在多个文件或模块重复表达；
- adapter、controller、模型层职责交叉；
- 为单次问题增加的特殊分支仍留在主流程；
- 兼容层已经没有真实调用者；
- 为未来扩展提前增加但没有实际使用的抽象；
- 测试覆盖实现细节，却没有证明真实主线。

本轮不是功能重写，也不是新架构设计。整理工作的价值只由以下结果衡量：

1. 主流程更容易追踪；
2. 核心概念和分支更少；
3. 文件职责更明确；
4. 无效代码被安全删除；
5. 原有行为和跨模块契约保持稳定。

## 2. 本轮目标与非目标

### 2.1 目标

- 画出每个模块当前真实运行路径；
- 找出死代码、重复逻辑、半套实现和断链；
- 明确每个核心概念的唯一所有者；
- 合并模块内部的重复实现；
- 删除无真实调用者的旧路径和兼容代码；
- 在确有必要时整理过大的文件或明显混乱的目录；
- 保留并补足能够证明行为不变的测试；
- 形成四模块统一但不过度同构的代码风格。

### 2.2 非目标

本轮不做：

- 新科研能力；
- 高难度科研任务能力扩展；
- 新 agent 框架或工作流引擎；
- LangGraph、Temporal、MLflow、DVC 等新依赖；
- 四仓合并为 monorepo；
- 公共协议 V3；
- 为所有模块设计共同基类；
- 大规模 Prompt 重写；
- 为追求目录外观一致而强制移动文件；
- 与代码整理无关的性能优化。

高难度任务测试将在本轮整理稳定之后单独设计，不与行为保持型重构混在一起。

## 3. 唯一总体主线与职责

```text
用户输入
  -> ResAgent：管理 run、任务、资源、暂停恢复和调度
  -> ExpAgent：形成科学计划或分析科学证据
  -> ResAgent：把 required action 转换为已承诺 Task
  -> CodingAgent / ReproAgent：执行代码或实验操作
  -> Artifact：冻结下游可消费的结果证据
  -> ExpAgent：分析 Artifact 并形成科学结论
  -> ResAgent：根据状态继续、询问或 finish
```

| 模块 | 唯一核心职责 | 不应承担 |
|---|---|---|
| ResAgent | 编排、Task 生命周期、资源传递、暂停恢复 | 科学结论、具体代码编辑、实验内部执行 |
| ExpAgent | 科学规划、文献检索、结果分析、科学建议 | Task 状态管理、重试策略、workspace 管理 |
| CodingAgent | 通用代码理解、创建、修改和验证 | 科学任务规划、ResAgent 调度、实验结论 |
| ReproAgent | 环境准备、实验执行、证据冻结和复现报告 | 最终科学结论、上层任务编排 |
| Adapter | 参数和结果转换 | 替被调用模块做业务决策 |

出现职责争议时，优先按照该表判断，不新增“中间协调层”。

## 4. 防止过度设计的硬性规则

以下规则对四个模块 AI 都是验收门槛。

### 4.1 禁止事项

1. 不引入新框架、新基础设施或新生产依赖。
2. 不为“以后可能使用”增加扩展点、注册表、抽象接口或插件层。
3. 不为一个调用点创建通用管理器、服务类或抽象基类。
4. 不新增第二套状态、Task、Artifact、Attempt 或 Session 模型。
5. 不使用兼容壳掩盖内部文件移动。
6. 不在一次提交里同时移动文件、重命名接口和修改行为。
7. 不顺手重写 Prompt；Prompt 变化属于行为变化。
8. 不做全仓格式化、全仓 import 排序或无关命名修改。
9. 不因“看起来更工程化”拆出更多层。
10. 不修改其他模块仓库。

### 4.2 新抽象准入条件

新增抽象必须同时满足：

- 至少替代两个真实生产调用点；
- 删除的重复逻辑多于新增的间接层；
- 能用一句话说明唯一职责；
- 不引入新的生命周期或状态所有者；
- 测试能证明两个调用点共享相同行为。

否则保留局部直白实现。

### 4.3 删除优先

处理复杂代码时按顺序考虑：

1. 能否删除；
2. 能否合并到现有主线；
3. 能否使用已有函数；
4. 最后才考虑新增抽象。

### 4.4 复杂度审问

每项修改都必须回答：

- 它减少了哪个真实概念或分支？
- 删除它是否更简单？
- 新开发者因此少需要理解什么？
- 这是当前需求，还是假想未来需求？
- 是否改变了 Prompt、契约或状态语义？
- 生产代码是净减少还是净增加？若增加，为什么不可避免？

## 5. 统一审查流程

每个模块必须按以下阶段执行，不允许直接开始大规模改代码。

### Phase A：冻结基线

记录：

- 仓库路径；
- 当前分支与 commit；
- `git status`；
- Python 和 conda 环境；
- 全量测试数量与结果；
- 现有 CLI / Python 公共入口；
- 当前依赖列表。

从默认分支创建独立整理分支。不得在含不明未提交修改的工作树上整理。

### Phase B：只读审查

先提交审查报告，不修改生产代码。报告必须包含：

1. 真实入口；
2. 主流程调用图；
3. 主要文件职责表；
4. 状态读写位置；
5. 外部输入输出和副作用；
6. 疑似重复、死代码、兼容层和半套实现；
7. 测试与生产路径对应关系；
8. 所有问题的文件和行号证据。

不得仅凭文件名、文件长度或主观观感删除代码。

### Phase C：候选修改分类

| 分类 | 示例 | 本轮处理方式 |
|---|---|---|
| 安全整理 | 死 import、无调用私有函数、失效注释、重复小工具 | 可直接处理 |
| 内部结构整理 | 拆分职责混乱的大文件、合并重复内部路径 | 小步处理并补测试 |
| 行为风险修改 | Prompt、公共模型、状态机、CLI、跨模块契约 | 只报告，单独审批 |
| 新功能 | 新工具、新策略、新扩展点 | 不处理 |

### Phase D：小步整理

每个提交只处理一个主题。推荐提交粒度：

```text
remove unused legacy parser
consolidate duplicate task lookup
separate CLI rendering from controller
clarify evidence persistence ownership
remove unreachable compatibility exports
```

避免 `refactor architecture`、`cleanup everything` 之类的大提交。

### Phase E：模块验收

每个模块完成：

- 定向测试；
- 全量单元测试；
- `git diff --check`；
- 公共入口导入测试；
- CLI `--help` 或等价 smoke test；
- 修改前后依赖对比；
- 删除代码的证据说明；
- 最终工作区状态报告。

## 6. 问题分类和证据标准

### 6.1 问题分类

| 类型 | 定义 |
|---|---|
| Correctness | 当前代码可能产生错误结果、错误状态或断链 |
| Split Mainline | 同一能力存在两条可达生产路径 |
| Dead / Legacy | 无真实调用者或已被新实现替代 |
| Ownership | 同一状态或决策由多个层维护 |
| Redundancy | 重复实现同一规则，可能漂移 |
| Readability | 职责明确但组织方式造成理解成本 |
| Test Gap | 生产路径存在但缺少行为级测试 |
| Overdesign | 抽象、配置或扩展点没有现实消费者 |

### 6.2 每项问题必须包含

```text
ID：模块缩写-编号
严重度：严重 / 高 / 中 / 低
文件与行号：
现状：
真实调用路径：
为什么是问题：
是否改变行为：
最小处理方案：
删除或修改的风险：
验收测试：
```

### 6.3 死代码删除证据

删除候选至少完成：

- 搜索 import、调用和字符串动态引用；
- 检查 CLI entry point；
- 检查测试和 fixtures；
- 检查跨模块公开导入；
- 确认不是序列化兼容字段；
- 删除后运行全量测试。

无法证明不可达的代码，不按死代码删除。

## 7. 分模块审查重点

### 7.1 ResAgent

重点追踪：

- `controller/loop`、`actions`、`contracts`、`planner` 的决策边界；
- Task 是否只有一个生产创建入口；
- retry、repair、ask_user、answer、finish 是否各只有一条主线；
- action graph 到 Task 的转换是否唯一；
- adapter 是否包含本应由 controller 或子模块负责的业务判断；
- Task、Attempt、Observation、Artifact 是否重复表达状态；
- `current_summary`、Decision、Observation、Artifact summary 的职责；
- run/project/session/workspace 的路径解析是否唯一；
- V1、旧 task contract、旧 action graph 和兼容入口是否仍可达；
- Chat 与 run controller 是否共享正确边界，而不是两套系统。

ResAgent 整理完成后，应能从 `resagent run` 或 `answer` 入口沿一条路径追踪到状态落盘。

### 7.2 ExpAgent

重点追踪：

- 咨询、规划和结果分析是否复用同一清晰 agentic loop；
- 科学建议与已承诺执行任务是否严格分离；
- action graph 是否只有一个权威 schema 和 validator；
- 文献检索、笔记、计划、分析的上下文如何进入最终 Decision；
- 是否越界管理 Task 状态、重试、workspace 或环境；
- 旧 experiment plan 与 V2 action contract 的关系；
- Prompt 是否有重复、互斥或导致递归建议的规则；
- 可选建议是否只保留在 Decision 中。

ExpAgent 不应为了整理而改变科学决策 Prompt；发现 Prompt 问题应单独列为行为修改。

### 7.3 CodingAgent

重点追踪：

- standalone 调用和被上层委托是否进入同一个核心 loop；
- 空 workspace、本地 repo、远程 repo URL 的初始化路径；
- list/read/write/edit/verify 工具的错误是否统一返回 observation；
- 不存在文件、权限问题和路径越界的边界；
- 代码编辑、验证、diff、state、patch report 是否重复记录；
- standalone 环境与 frozen delegated 环境的职责；
- 对 ResAgent/ReproAgent 的适配是否侵入通用核心；
- 已 vendored 或同步的代码是否有单一来源和校验。

CodingAgent 必须保持通用代码 agent，不得整理成只适配当前科研系统的特化实现。

### 7.4 reproagent

重点追踪：

- repo 获取、上下文收集、环境准备、probe、执行、报告的唯一主线；
- standalone code delegation 与 ResAgent 托管模式的边界；
- 环境身份、复用、认证、镜像和缓存的所有者；
- result.json、result.md、evidence、state、session 的唯一职责；
- attempt 重试、blocked outcome 和逻辑 Artifact 替换的审计性；
- 代码修复请求如何返回 ResAgent；
- 旧 smoke/medium/profile/线性 workflow 是否仍有残留；
- 环境命令、实验命令和危险命令限制是否集中且唯一。

reproagent 只提供实验事实和证据，不形成最终科学结论。

## 8. 跨模块冻结契约

模块 AI 不得自行改变：

- capability 名称及能力卡语义；
- 当前 V2 scientific action contract；
- `AgentTask`、ArtifactRef、EnvironmentRef 的跨模块含义；
- `project_ref`、`workspace_path`、repo URL 的传递规则；
- `completed`、`blocked`、`failed`、`needs_user_input` 的语义；
- required action 与 optional recommendation 的区别；
- `result.json` 作为机器接口、`result.md` 作为人类报告；
- Artifact 不可变、workspace 可变的原则；
- ResAgent 管调度、ExpAgent 管科学判断的边界。

发现跨模块问题时：

1. 不直接修改其他仓库；
2. 不在本模块增加补丁式兼容；
3. 写交办文档，包含生产者、消费者和最小协议修改；
4. 由总体审查统一决定。

## 9. 每个模块 AI 的交付物

每个模块最终必须交付：

1. **只读审查报告**：真实架构、问题和证据；
2. **修改清单**：实际删除、合并、移动和保留内容；
3. **主流程说明**：修改后的入口到输出调用链；
4. **文件职责表**：核心文件存在的理由；
5. **测试报告**：修改前后测试数量与命令；
6. **风险与未处理项**：行为风险问题单独列出；
7. **Git 信息**：分支、commit、diff stat、工作区状态。

建议交付表：

| 项目 | 修改前 | 修改后 | 说明 |
|---|---:|---:|---|
| 生产 Python 文件数 |  |  | 不以减少为硬指标 |
| 生产代码行数 |  |  | 增长必须解释 |
| 兼容文件数 |  |  | 每个保留项说明调用者 |
| 全量测试 |  |  | 不得减少关键覆盖 |
| 生产依赖 |  |  | 原则上不得新增 |

## 10. Git 与协作规则

- 每个仓库从当前默认分支创建独立整理分支；
- 一个 AI 只修改一个仓库；
- 不直接合入默认分支；
- 不 rebase 或覆盖其他人的未提交修改；
- 文件移动与逻辑修改分开提交；
- 删除兼容入口单独提交；
- 测试必须和对应整理同提交或紧邻提交；
- 各模块完成后由总体审查统一检查再决定合并顺序。

推荐分支：

```text
codex/readability-cleanup
```

如果已有同名分支，使用带模块名或日期的变体。

## 11. 总体验收

四个模块分别完成后，由 ResAgent 会话进行第二轮总体审查。

### 11.1 静态验收

- 四模块公开入口和能力卡一致；
- 跨模块契约没有漂移；
- 没有同一概念出现四种不同命名；
- 没有新增无消费者抽象；
- 没有因文件移动新增兼容壳；
- 生产代码总体不无理由增长；
- 所有删除都有可达性证据。

### 11.2 本地行为验收

- 四模块全量单测通过；
- ResAgent 确定性四模块闭环通过；
- CodingAgent 空 workspace 创建文件路径通过；
- ReproAgent structured result 和 evidence freeze 通过；
- ExpAgent action contract 和结果分析通过；
- ask_user / answer / finish 恢复路径通过；
- `git diff --check` 全部通过。

### 11.3 云端验收

本轮不要求每个模块 AI 各自运行昂贵云端测试。总体审查通过后，只选择：

1. 一条无 GPU 的确定性或轻量真实闭环；
2. 一条已有缓存的短 GPU 实验；
3. 必要时复验 artifact overwrite 或 repair propagation。

整理阶段不测试高难度科研能力。高难度能力评估应使用独立 eval 方案，避免把能力缺口误判为重构回归。

## 12. 完成定义

本轮只有在以下条件全部满足时才算完成：

- 四模块分别完成只读审查和整理；
- 总体审查确认只剩一条生产主线；
- 没有已知上下游断链；
- 没有无消费者的半套模型或流程；
- 没有因整理新增框架或第二套抽象；
- 原有公共接口和核心行为未被无意改变；
- 本地测试全部通过；
- 代表性云端闭环通过；
- 文档能让新开发者解释主要文件和完整工作流。

“文件更少”不是完成标准。“概念更少、路径更直、职责更清楚、行为仍正确”才是完成标准。

## 13. 可直接交给模块 AI 的任务说明

```text
请严格按照
ResAgent/docs/active/FOUR_MODULE_CODE_REVIEW_AND_SIMPLIFICATION_PLAN.md
审查并整理你负责的模块。

先完成只读审查和问题清单，再做代码修改。目标是保持功能与公共接口基本不变，
删除死代码、重复逻辑、半套实现和无真实消费者的兼容路径，使主线更容易追踪。

禁止引入新框架、新生产依赖、共同基类、插件层或面向未来的扩展点；禁止修改其他
模块；禁止自行改变跨模块契约和 Prompt 行为。能删除就不要新增封装，只有一个调用点
的逻辑不要抽象。

完成后提交：审查报告、修改清单、主流程说明、文件职责表、测试报告、未处理风险、
分支与 commit 信息。不要直接合并默认分支，等待总体审查。
```
