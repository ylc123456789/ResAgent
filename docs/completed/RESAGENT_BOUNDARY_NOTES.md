# ResAgent 边界优化建议

**来源**: ResAgent 全系统边界审计 (docs/BOUNDARY_AUDIT_REPORT.md)
**审计日期**: 2026-08-08
**优先级**: 低（风格统一，非功能问题）

---

## 问题: scientific_decision 序列化格式不一致

**涉及文件**:

- `src/resagent/adapters/expagent.py:62` — ResAgent 写 `scientific_decision.json`
- ExpAgent `report.py:141` — ExpAgent 写 `scientific_decision.yaml`

**现象**: 同一个 `ScientificDecision` 对象，ResAgent 存为 `.json`，ExpAgent 内部存为 `.yaml`。两个文件内容等价但格式不同。

**建议**: 统一为 `.json`（与 `state.json` 一致，且 ResAgent 的 artifact 引用已是 `.json`）。在 ExpAgent 侧 `report.py:141` 改 `scientific_decision.yaml` → `scientific_decision.json`。

---

## 注意: 非本次修复范围

以下审计发现属于设计决策，不需要改：

- CodingAgent `verify_commands` 可写任意文件 → 是执行模型决定的，Tier-2 确认保护
- ReproAgent conda 环境路径 → 是 conda 管理的，不由代码控制
- 各模块 run_id 格式不同 → 有前缀区分，不会碰撞
