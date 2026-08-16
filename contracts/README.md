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

**不参与身份**：时间戳、run/task id、绝对路径、`notes` 等注释性字段
——以及 `pip_index_profile`（2026-08-16 修订：镜像是"从哪下载"的操作
偏好，不是"装了什么"的身份；pypi 镜像与 pypi 内容字节相同，若某 index
提供了不同构建，差异由 resolved_fingerprint 与复用审计兜底。conda
`channels` 不同——它们会改变构建产物，保留在身份内）。

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

## 采集语义（规范性，三仓必须一致）

P0 冻结了"怎么算"；本节冻结"从哪采"。同一 repo + 同一任务，三仓必须
采到逐字节相同的 spec，否则跨模块复用/升级无从谈起。

### accelerator

- `type`：任务的 `requires_gpu` 为真且本机有可用 GPU → `cuda`；
  否则 `cpu`。**驱动探测只用于可行性判断，不进入身份。**
- `variant`：任务/框架约束显式给出（如 `torch==2.6.*+cu124`）时取该值；
  否则留空 `""`，由创建时解析并记录到 manifest.resolved.frameworks。
  **禁止**把驱动支持的最高 CUDA 版本映射为 wheel 变体（driver 13.0 ≠
  cu130），**禁止**读取调用方宿主进程里已装的框架来推断。

### dependency_files

- 文件集合（repo 相对路径）：`environment.yml`、`requirements*.txt`、
  `pyproject.toml`、`setup.py`、`setup.cfg`、lock 文件（`*.lock`、
  `requirements*.lock`）；按相对路径排序；
- `sha256` = **文件原始字节**的 SHA-256（不得先 decode/转码）。

### pip_index_profile

取任务的镜像策略名（如 `autodl`/`aliyun`/`pypi`），由调用方传入；
禁止写入临时域名。**仅操作元数据，不进身份**（创建时决定从哪下载；
复用判定与它无关）。

### provenance（manifest 必填）

- `repo_path`：任务工作区对应的 repo 绝对路径（创建 env 时必填）；
- `repo_commit`：git HEAD（是 git 仓库时必填，用于陈旧候选剔除）；
- `repo_origin`：repo_url 或 `local`。

### resolved_fingerprint 规范化

`sha256(canonical_dumps({python, conda_inventory_sha256,
pip_inventory_sha256, frameworks, abi_summary}))`，其中两个 inventory 哈希
分别为 `conda list -p <prefix> --json` 与 `python -m pip list
--format=json`（在目标 env 内执行）输出的 canonical JSON 的 SHA-256；
必须在**依赖安装完成后**计算。

### lease 布局（定死）

lease 只写一个位置：`<root>/environments/<env_id>/usage/lease_*.json`。
所有读取方（含各模块 prune/清理）必须读这里，不得另立 `<root>/leases/`。

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
