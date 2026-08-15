# ExpAgent Session Card 修复任务

**来源**: ResAgent 全流程测试分析 (run: res-20260811-80f8b6)
**优先级**: P2（不影响功能，影响可观测性）

---

## 问题: requires_gpu 标记与实际 GPU 使用不一致

ExpAgent 的 `SuggestedPlan` 中 `requires_gpu=false`，但 ReproAgent 检测到 4090D 并使用 GPU 训练。

**建议修复** (`src/experiment_designer/advisor.py` 或 `planner.py`):
在生成 `SuggestedPlan` 时，检查 hardware context 或运行环境。当 `nvidia-smi` 可见或 `torch.cuda.is_available()` 时，`requires_gpu` 应标记为 `true`。

如不方便做硬件检测，至少确保 prompt 要求 LLM 在知道有 GPU 时设置此字段。

---

## 问题: ExpAgent session card 的 key_artifacts 为空

ExpAgent 自己的 `scientific_decision.json` 没有出现在 session card 的 `key_artifacts` 列表中。

**建议修复** (`src/experiment_designer/report.py`):
写 `session.yaml` 时，显式追加 `key_artifacts`:
```yaml
key_artifacts:
  - type: scientific_decision
    path: scientific_decision.json
    summary: <从决策摘要字段取>
```

ResAgent adapter 会在 ExpAgent 返回后补写此字段做兜底。
