# 四模块现状与全面代码审查报告

> 日期：2026-08-20  
> 范围：ResAgent、ExpAgent、reproagent、CodingAgent  
> 性质：只读审查报告与修复建议，不包含代码修改  
> 结论：核心科研执行链已经可用，但仍有两个阻塞稳定收尾的编排契约问题。

## 1. 审查目标与范围

本次审查用于回答：

1. 四个模块分别实现到了什么程度；
2. 最近云端问题是模型波动，还是有确定代码根因；
3. 当前版本能否作为稳定版本收尾；
4. 应按什么顺序修复、如何验收。

重点核对了四仓源码、近期提交、本地测试、跨模块契约测试、确定性系统测试，以及以下云端 run：

- L1：`/root/autodl-tmp/resagent-workspace/runs/mixup-l1-20260818/`
- L2：`/root/autodl-tmp/resagent-workspace/runs/mixup-l2-seblock/`
- L3：`/root/autodl-tmp/resagent-workspace/runs/l3-lrschedule/`

## 2. 当前基线

### 2.1 本地仓库

| 模块 | Git 状态 | 当前提交 | 测试 |
|---|---|---|---|
| ResAgent | `master`，干净，但领先 `origin/master` 16 个提交 | `6a50355` | 209 passed；确定性系统测试失败 |
| ExpAgent | `main`，与远端同步、干净 | `ad67554` | 76 passed，22 deselected |
| reproagent | `main`，与远端同步、干净 | `cfa8843` | 221 passed |
| CodingAgent | `main`，与远端同步、干净 | `cf8ace2` | 167 passed |

四模块 M2 环境契约检查通过，vendored canonical contract 文件保持一致。

### 2.2 云端版本

云端并非当前本地代码的干净快照：

- ResAgent 停在较旧提交 `d6ecd70`，有多处未提交源码修改；
- CodingAgent 停在较旧提交 `5a2204e`，有多处未提交修改和新增文件；
- ExpAgent、reproagent 相对干净。

所以近期云端测试证明的是“服务器临时工作树能运行”，不能直接证明当前本地提交或 GitHub 版本通过。

## 3. 总体判断

主体能力已经形成：

```text
用户目标
  -> ResAgent 编排
  -> ExpAgent 检索、设计、分析
  -> CodingAgent 编写或修改代码
  -> ReproAgent 配置环境并运行实验
  -> ExpAgent 分析结果
  -> ResAgent 判断结束
```

论文检索、原创代码、GPU 训练、环境隔离、镜像与缓存、指标读取、科学结论都已在真实测试中工作。当前问题集中在最后两步：

1. ExpAgent 完成分析后，可能把刚完成的分析再次描述为未来任务；
2. ResAgent 无条件把这些建议入图，并用过宽规则替换旧任务。

因此系统目前属于“能够完成科研任务，但不一定自动停下来”，还不宜定义为稳定收尾版本。

## 4. 问题总表

| ID | 严重度 | 问题 | 模块 | 阻塞收尾 |
|---|---|---|---|---|
| F1 | 严重 | 分析结果递归生成新实验/分析任务 | ExpAgent + ResAgent | 是 |
| F2 | 严重 | 新 action graph 可能错误跳过无关 pending 任务 | ResAgent | 是 |
| F3 | 高 | 普通回答与计划指令混合，Chat 还重复记录 | ResAgent | 是 |
| F4 | 高 | 本地、远端、云端版本不一致 | 发布流程 | 是 |
| F5 | 中 | 科学目标完成与 run 状态不一致 | ExpAgent + ResAgent | 复验 |
| F6 | 中 | 空动作校验依赖英文固定短语 | ExpAgent | 放大 F1 |
| F7 | 中 | 单测全绿但跨层闭环失败 | 四模块测试 | 是 |
| F8 | 低 | 缓存桥接失败缺少诊断 | CodingAgent | 否 |
| F9 | 低 | 文档状态与真实状态不一致 | ResAgent | 否 |

## 5. 详细问题、证据与解决方案

### F1. 分析结果递归生成新任务

**源码证据**

ExpAgent 提示词要求：当 decision 解释实验结果时，必须输出 `analyze_results` action：

- `ExpAgent/src/experiment_designer/prompts/system.py:102-106`

但 ExpAgent 此时通常已经在执行 ResAgent 派发的 `analyze_results`。它已经完成分析，却又被要求推荐一次未来分析。

校验器还规定：

- 所有依赖必须引用同一个 decision 中更早的 action；
- `analyze_results` 没有 `depends_on` 就非法。

证据：

- `ExpAgent/src/experiment_designer/controller/validator.py:158-170`
- `ExpAgent/src/experiment_designer/controller/validator.py:276-278`

ResAgent 随后无条件把 `recommended_actions` 转成新任务：

- `ResAgent/src/resagent/adapters/expagent/adapter.py:94-110`

实际形成：

```text
执行 analyze_results
 -> ExpAgent 已得出结论
 -> 又推荐 analyze_results
 -> 为满足同图依赖，补一个 execute_experiment
 -> ResAgent 创建新实验和新分析
 -> 再次分析
```

**云端证据**

L3 的 `decision_006` 已经得出 cosine、linear、constant 三组实验的明确结论，但后续又产生三个 `execute_experiment`。这些任务没有训练，只是在读取已有 CSV；随后又追加分析。最终核心实验和结论完成，run 仍为 `running`，并残留 required 实验与分析任务。

L2 已记录相同症状：

- `docs/reference/CAPABILITY_TEST_FINDINGS_L2_SEBLOCK.md:88-112`

**根因**

`ScientificDecision.recommended_actions` 混合了：

1. ExpAgent 当前已经完成的科学工作；
2. 当前结论之后仍需执行的未来工作。

**通用修复方案**

1. 明确 ScientificDecision 是当前咨询任务的结果；
2. recommended_actions 只表达未来工作；
3. 当前能力为 `analyze_results` 且已形成结论时，允许并鼓励 `recommended_actions=[]`；
4. 删除“解释结果必须再次 emit analyze_results”的规则；
5. 只有初始实验计划中的未来实验才配套创建 analyze action；
6. ExpAgent 直接依据输入 artifact 完成当前分析，不重新声明历史实验；
7. ResAgent 拒绝把与当前输入 artifact 等价的 experiment/analyze 动作再次入图；
8. 新方向和扩展实验默认 `required=false`。

**验收标准**

- 给 ExpAgent 一个已完成实验 artifact，执行一次 `analyze_results`；
- 输出完整结论且 `recommended_actions=[]` 合法；
- ResAgent 不新增 required 任务；
- 下一步允许 finish；
- 连续运行 3 个 controller step，任务数不增长；
- L3 类 case 无需用户说“不要追加实验”即可 completed。

### F2. action graph 替换范围过宽

**源码证据**

每次动作图转换后都会调用：

- `ResAgent/src/resagent/adapters/expagent/task_conversion.py:116`

`_retire_superseded()` 会把新图中没有出现的 pending 任务标为 skipped：

- `ResAgent/src/resagent/adapters/expagent/task_conversion.py:120-138`

它没有判断当前 decision 是否为完整 replan，也没有旧 plan ID、`supersedes_task_ids`、plan version 或 replacement scope。尤其当 `new_tasks=[]` 时，现有条件可能跳过全局所有 pending 任务。

现有测试只覆盖同项目的理想替换：

- `ResAgent/tests/test_controller.py:377-404`

没有覆盖空图、多项目、局部分析、普通咨询和 optional follow-up。

**根因**

代码用“新图里没出现”推断“旧任务已取消”，这不是可靠的生命周期契约。

**通用修复方案**

1. 只有明确的 `replan` 调用才能执行 retirement；
2. replan 携带旧 decision/plan ID；
3. 使用显式 `supersedes_action_ids`，或由 ResAgent给出 replacement scope；
4. 只跳过 scope 内仍 pending 且明确被替换的任务；
5. 空图表示“不增加任务”，不能表示“取消全部”；
6. 普通分析、失败诊断、文献咨询不得触发全局 retirement；
7. 记录 `superseded_by`，保证状态可审计。

**验收标准**

- 空 action graph 不改变现有任务；
- 项目 A 的 replan 不影响项目 B；
- 局部结果分析不跳过独立任务；
- 只跳过显式指定任务；
- completed、running、needs_user_input 不被隐式跳过；
- finish 不被僵尸任务阻塞，也不会因误 skip 提前通过。

### F3. 用户回答与计划指令混为一类

**源码证据**

`submit_user_response()` 无条件把回答加入 `user_directives`：

- `ResAgent/src/resagent/persistence/state.py:72-96`

Controller 无条件把未处理 directive 转为 ExpAgent replan：

- `ResAgent/src/resagent/controller/loop.py:62-64`
- `ResAgent/src/resagent/controller/contracts.py:223-273`

所以“确认”“继续”“路径是 X”和“改成单 seed”都会触发科学重规划。

Chat 入口还会重复记录：`submit_user_response()` 追加一次，`_advance_run()` 又追加一次：

- `ResAgent/src/resagent/conversation/tools.py:347-353`

**测试证据**

确定性系统测试回答 `accepted` 后尝试 finish，却被新建 replan 阻塞：

- `ResAgent/scripts/deterministic_system_test.py:152-173`

**通用修复方案**

1. `submit_user_response()` 只回答 pending question；
2. PendingQuestion 增加结构化 `response_effect`：
   - `inform`：补充事实；
   - `confirm`：批准或拒绝；
   - `revise_plan`：修改目标或范围；
   - `stop`：停止或跳过；
3. 只有 revise_plan 和必要的 stop 创建 replan；
4. 普通回答写入 answered_questions 并进入上下文；
5. Chat 路径删除第二次 directive 写入；
6. 问题创建者声明回答是否改变计划，不依赖回答文本猜测。

**验收标准**

- 回答 `accepted` 不创建 replan；
- 回答“改为单 seed”恰好创建一个 replan；
- Chat 与 CLI 行为一致；
- 同一句回答只记录一次；
- 确定性系统测试通过；
- 保存、退出、恢复后回答只消费一次。

### F4. 云端测试不可严格复现

**证据**

- 本地 ResAgent：`master...origin/master [ahead 16]`，HEAD `6a50355`；
- 云端 ResAgent：HEAD `d6ecd70`，多处未提交修改；
- 云端 CodingAgent：HEAD `5a2204e`，多处未提交修改；
- 本地 CodingAgent：`cf8ace2`。

**通用修复方案**

1. 审查并推送 ResAgent 本地提交；
2. 测试前生成 provenance manifest：repo、branch、commit、dirty、Python、环境、模型和配置摘要；
3. 正式验收要求四仓 `dirty=false`；
4. commit 不匹配时在 GPU 操作前拒绝运行；
5. 云端不现场修改子模块源码，问题通过日志和交办文档返回；
6. 验收报告必须引用 provenance。

**验收标准**

- 本地、GitHub、云端四仓 SHA 一致；
- 四仓 clean；
- 报告自动记录完整 SHA；
- dirty 或 SHA 不匹配时 fail fast。

### F5. 科学完成与 run 状态不一致

**云端证据**

- L1：run completed，但保留多个 optional pending；
- L2：核心实验和分析完成，run 最终 interrupted；
- L3：核心实验和结论完成，run 仍 running。

L2 的 HTTP 402 是外部问题，但核心目标完成后仍继续调用模型，是 F1 导致的任务膨胀。

**通用修复方案**

优先修 F1，再补：

1. 明确 `goal_resolution`：unresolved/supported/not_supported/inconclusive/blocked；
2. 最终 ScientificDecision artifact 是 goal_resolution 的主要证据；
3. required action 只代表当前 goal 的完成条件；
4. 新方向默认 optional；
5. optional proposals 进入最终报告，但不阻塞 completed。

**验收标准**

- 形成最终结论且无 required 未完成任务时自动 completed；
- optional 不阻塞 finish；
- completed 后不再派发任务；
- interrupted 只表示真实可恢复中断。

### F6. 空动作校验依赖英文短语

当 `recommended_actions=[]` 时，validator 要求 rationale 包含英文 `"no action"`：

- `ExpAgent/src/experiment_designer/controller/validator.py:253-256`

中文“无需后续操作”会被拒绝，模型因而倾向于生成没有必要的动作。

**解决方案**

删除自然语言字符串判断。空动作本身合法；如需更明确，可增加结构化 `next_action_policy: none|optional|required`，或依据 conclusion、evidence、needs_user_input 判定。

**验收标准**

- 中文和英文终局结论都能以空动作通过；
- 不存在魔法短语；
- conclusion、evidence、risks 仍需完整。

### F7. 测试存在跨层缺口

四仓 pytest 总体全绿，但确定性系统测试失败。现有 ExpAgent fixture 主要验证初始：

```text
execute_experiment -> analyze_results
```

没有验证终局：

```text
当前正在 analyze_results
 -> 输出结论
 -> 不再创建 analyze_results
```

**解决方案**

新增无 API、无 GPU 的确定性收敛 case：

1. 初始 ExpAgent 生成 coding → experiment → analysis；
2. CodingAgent mock 产出代码 artifact；
3. ReproAgent mock 产出固定指标；
4. ExpAgent 分析并返回最终结论、空 required actions；
5. ResAgent finish；
6. 再调用一步仍 terminal，任务数不增长；
7. 普通确认不 replan；
8. 真正 scope 修改只 replan 一次并只替换指定任务。

**验收标准**

- 四仓单测、M2 contract、deterministic system test 全绿；
- 新 orchestration convergence test 全绿；
- 云端只需一次短 GPU E2E。

### F8. CodingAgent 缓存桥接失败缺少诊断

CodingAgent 初始化 dataset link 时捕获所有异常，只设置 `dataset_links=[]`：

- `CodingAgent/src/coding_agent/controller/loop.py:28-41`

权限、路径和实现错误可能表现为“缓存没命中”。

**解决方案**

保留 best-effort，但在 state/report/log 中记录 `no_cache`、`no_match`、`permission_denied`、`link_failed` 等降级原因。

### F9. 文档状态不一致

- `docs/reference/CAPABILITY_VALIDATION_TEST_PLAN.md:7` 仍写 L3 待跑；
- active L3 文档已经记录测试与修复；
- L3 文档没有完整反映 run 仍因新增 required task 保持 running。

代码问题关闭后，再把稳定结论写入 reference，未关闭问题保留 active，基于 clean commits 验收后才能归档 completed。

## 6. 各模块现状

### ResAgent

已具备 agentic controller、能力注册、任务派发、依赖图、artifact/workspace/session/project 管理、失败分类、有限重试、finish gate、分析覆盖、环境复用和 Chat/CLI 恢复。主要风险是 action graph 生命周期、用户输入语义和发布基线。

### ExpAgent

已具备论文检索、科学分析、实验设计、结果分析、失败诊断和 typed action union。主要风险是当前结果与未来建议混淆，以及终局空动作语义不可靠。

### reproagent

已具备论文/仓库上下文、Conda 隔离、GPU 探测、镜像、pip/数据集缓存、环境审计、实时实验日志、代码修改请求和偏差报告。近期本地测试通过，未发现新的确定性崩溃；复现止损仍主要依赖 prompt。

### CodingAgent

已具备独立仓库/工作区执行、代码检索修改、验证、patch repair、有限步数、环境契约、镜像和数据集缓存。近期本地测试通过，主要剩余问题是 best-effort 功能的可观测性。

## 7. 推荐修复顺序与分工

### Phase A：只修主线语义

**ExpAgent**

1. 修 F1：区分当前结果与未来 action；
2. 修 F6：删除 `"no action"` 魔法字符串；
3. 增加终局分析 fixture 和 validator 测试。

**ResAgent**

1. 修 F2：显式限定 supersede scope；
2. 修 F3：区分 answer/directive，删除 Chat 重复写入；
3. 增加跨层收敛测试。

**reproagent、CodingAgent**

本阶段原则上不修改，除非集成测试出现明确模块内 bug。

### Phase B：建立可信发布基线

1. 审查并推送 ResAgent 本地提交；
2. 四仓记录同一批次完整 SHA；
3. 云端同步为 clean 工作区；
4. 运行单测、契约测试和确定性闭环。

### Phase C：最后一次短云端 E2E

- 复用本地 repo、数据集和 pip cache；
- 1 个代码任务；
- 1 个短 GPU 实验；
- 1 个科学分析任务；
- 自动 finish；
- finish 后任务数不增长；
- 四仓 provenance clean。

## 8. 稳定收尾标准

以下条件全部满足后再收尾：

1. F1、F2、F3、F4 关闭；
2. 四仓单测全部通过；
3. M2 契约测试通过；
4. ResAgent 确定性系统测试通过；
5. 结果分析收敛测试通过；
6. 普通回答不触发 replan，真实修改只触发一次；
7. 空 action graph 不跳过无关任务；
8. 最终分析后不再生成 required experiment/analyze；
9. 云端四仓 SHA 一致且 `dirty=false`；
10. 最后一次短 GPU E2E 自动 completed。

## 9. 最终判断

主体架构无需推倒重来。ExpAgent 作为科学顾问、ResAgent 作为项目经理，ReproAgent 作为实验算子、CodingAgent 作为通用代码专家的边界是合理的。

当前需要收紧两个契约：

1. ExpAgent 的 decision 是当前任务结果，recommended actions 是结论之后的未来工作；
2. ResAgent 只能显式替换任务，不能因新图未出现某任务就推断它被取消。

完成这两点，再修用户输入分类和发布基线，系统才能从“能完成实验但偶尔停不下来”进入“能稳定完成并自动收尾”。
