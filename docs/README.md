# ResAgent 文档索引

按生命周期分三类。读文档前先看你属于哪类读者：

- 想了解**系统现在长什么样** → `reference/`
- 想知道**正在/接下来做什么** → `active/`
- 想查**某段历史是怎么决策的** → `completed/`（审计轨迹，不代表现状）

## active/ — 当前进行

| 文档 | 内容 |
|---|---|
| RESOURCE_MANAGEMENT_MILESTONE_2.md | 里程碑二：内容寻址环境、manifest、并发锁、复用前审计、漂移检测、跨 run 复用与安全清理 |
| handoffs/M2_P1_REPROAGENT.md | 交办单：reproagent 环境管理器（P1） |
| handoffs/M2_P2_CODINGAGENT.md | 交办单：CodingAgent 环境政策对齐（P2） |

## reference/ — 常驻（现行契约与架构）

| 文档 | 内容 |
|---|---|
| SCIENTIFIC_ORCHESTRATION_MAINLINE_REDESIGN.md | **V2 科学编排主线的权威契约**：capability 词表、闭环语义、验收标准（附录 H/I）。已实施并云端验收（v2-validated-2026-08-15） |
| CONVERSATION_LAYER_DESIGN.md | 对话层（chat）设计：会话状态、承诺分级、专家咨询 |
| SESSION_AND_PROJECT_MODEL.md | 会话/项目模型：session.yaml 索引卡、子会话只索引不合并 |
| ARTIFACT_AND_WORKSPACE_MANAGEMENT.md（+ _CN） | 产物与工作区管理契约 |
| TESTING_GUIDE.md | 测试组织与运行方式 |

## completed/ — 已完成（历史档案）

里程碑计划、交办单、handoff、审计报告。内容反映**当时**的语境，不作为现状依据；其中被取代的已在文首标注 SUPERSEDED。

- 实验执行与 V2 主线：`EXPERIMENT_OPERATOR_REDESIGN`（里程碑一 P0-P4 已完成，里程碑二已抽离到 active）、`FOUR_MODULE_READABILITY_REFACTOR_PLAN`、`REFACTOR_PHASE0_BASELINE`、`EXECUTION_CONTRACT_V1`（SUPERSEDED）、`INTERFACE_REDESIGN`（SUPERSEDED）
- 交互架构历史：`SYSTEM_INTERACTION_ARCHITECTURE`（旧 IntakeRouter 提案，已被 `CONVERSATION_LAYER_DESIGN` 取代）
- 修复计划：`CLOUD_E2E_FINDINGS_AND_ORCHESTRATION_REPAIR_PLAN`、`COMPREHENSIVE_TEST_ROOT_CAUSE_AND_GENERIC_REPAIR_PLAN`、`PHASE_A_REPAIR_COMPLETION_PLAN`
- 边界与审计：`BOUNDARY_AUDIT_REPORT`、`EXPAGENT_BOUNDARY_FIXES`、`RESAGENT_BOUNDARY_NOTES`
- 交办单：`EXPAGENT_INTEGRATION_REQUEST`、三个 `*_ARTIFACT_REQUEST`、`WORKSPACE_HYGIENE_INTEGRATION_REQUEST`、两个 `*_SESSION_FIXES`、两个 `*_P4_FOLLOWUP`、`IMPLEMENTATION_SCOPE_AND_HANDOFF`
- 对话层交付：`HANDOVER_CONVERSATION_LAYER`（§6 仍列有未来候选项：LLM 压缩 scratch_summary、长跑异步化、`--check-config` 等）
- `handoffs/`：两个跨模块交接单
