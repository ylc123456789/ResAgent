# 资源管理契约（M2-P0 冻结）

本目录是里程碑二**唯一的契约事实源**。四份语言无关的 JSON 契约 +
golden fixtures，供 ResAgent、reproagent、CodingAgent 三仓实现对齐。

权威方案：`docs/active/RESOURCE_MANAGEMENT_MILESTONE_2.md`（§4–§8）。
契约与方案冲突时，先改契约并同步方案，不允许只在实现里绕。

## 契约清单

| 契约 | schema 文件 | 用途 |
|---|---|---|
| ENVIRONMENT_SPEC_V1 | `environment_spec_v1.schema.json` | 请求的环境规格（创建/查找的输入） |
| ENVIRONMENT_MANIFEST_V1 | `environment_manifest_v1.schema.json` | 物理 env 的权威索引卡（状态/认证/溯源/库存） |
| ENVIRONMENT_AUDIT_V1 | `environment_audit_v1.schema.json` | 复用前/升级前审计记录 |
| RESOURCE_LEASE_V1 | `resource_lease_v1.schema.json` | 活跃使用登记（崩溃恢复与清理保护的依据） |

## 指纹算法（跨仓必须逐字节一致）

`spec_fingerprint` / `resolved_fingerprint` 均为：

1. 取契约中**参与身份**的字段子集（各 schema 内 `"identity": true` 标注的字段）；
2. 规范化：UTF-8、JSON 键递归排序、`ensure_ascii=True`、无空白
   （`json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`）；
3. 对该字符串取 SHA-256，hex 全小写。

**不参与身份**：时间戳、run/task id、绝对路径、mirror 临时域名、`notes`
等注释性字段（schema 中未标 `identity` 的字段一律不参与）。

`env_id = "resenv_" + slug(project) + "_" + spec_fingerprint[:12]`，其中
`slug()` 为小写字母数字、`/`与空白转为 `-`、连续 `-` 折叠。

## golden fixtures（`fixtures/`）

三仓实现的指纹/序列化结果必须与 fixtures **逐字节一致**，这是
跨仓一致性的验收手段（里程碑二完成定义第 1 条）：

- `fixtures/spec/*.json` + `fixtures/fingerprint_golden.json`：
  输入 spec → 预期 `spec_fingerprint`（含 §11 矩阵的关键等价/区分案例）；
- `fixtures/manifest|audit|lease/*.json`：各契约的合法示例，
  实现侧读写 round-trip 必须保持字段与值不变。

新增等价/区分案例时**三仓同步更新 fixtures**，不允许单侧扩展。

## 版本与兼容策略

- 契约以文件名内版本号演进（`_V1` → `_V2`）；V1 生命周期内只做
  可选字段的纯增量，不改既有字段语义；
- 新字段全部可选；旧 run/state/session 缺新字段时视为"未登记资源"，
  按 legacy 策略运行（方案 §12）；
- `reuse_mode = legacy | content_addressed`，默认 `legacy`；
  M2-P5 验收通过前不删除 legacy 实现与测试。

## 验收脚本骨架

`scripts/m2_contract_check.py`（随 P0 提供）：读取 fixtures，校验
本仓实现的指纹计算与序列化 round-trip；其他两仓以同一 fixtures 跑
各自的等价检查。
