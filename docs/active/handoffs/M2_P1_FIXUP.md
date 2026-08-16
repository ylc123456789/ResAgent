# M2-P1 修复交办：content_addressed 模式下 env_name 不得绕过指纹校验

**日期**：2026-08-16
**仓库/分支**：reproagent `feat/content-addressed-envs`（当前顶端 `64e9709`）
**严重度**：阻塞 M2-P5（stage 3 失败）
**证据**：`/root/autodl-tmp/resagent-workspace/m2-p5-20260816/runs/res-20260816-a4d56c/`（state.json + 自相矛盾的 session.yaml）

## 问题（已定位到行）

`src/reproagent/runtime/environment.py` 的 `ensure_environment` 分支顺序：

```python
if state.task.env_name:            # M2 之前的显式绑定分支，在最前
    return 直接绑定（不校验、不创建）
if state.task.reuse_mode == "content_addressed":
    return _ensure_content_addressed(...)   # 永远到不了
```

ResAgent 在 content_addressed 模式下会把 manifest 候选 env 的 prefix 注入
`env_name`（设计：**注入只是候选，模块必须按指纹复验**）。显式绑定分支
截胡后，候选被当成命令直接绑定——spec 变更后旧 env 照用、新 env 永不
创建；session 卡的 `manifest_path`/`spec_fingerprint` 却按当前 spec 另算，
产生自相矛盾的卡片。

## 要求的修复

**content_addressed 模式下，env_name 只是候选提示，身份裁决永远在指纹。**

1. 当 `reuse_mode == "content_addressed"` 且 `resource_root` 非空时，
   **内容寻址路径优先于显式绑定分支**；
2. 在 `_ensure_content_addressed` 内：若 `env_name` 非空，将其作为候选——
   仅当它等于当前 spec 算出的 identifier（或其 prefix）时进入既有
   ready-复用流程（重算 resolved 指纹 + 审计）；不匹配则忽略该候选，
   按指纹走正常查找/创建（manifest 缺失 → 创建新 env）；
3. **legacy 模式行为完全不变**（显式 env_name 照旧直接绑定）。

## 必须新增的测试

- spec 变更 + 注入了旧 env 的 env_name → 必须创建新 env（新 manifest、
  新 prefix），不得复用旧 env——这就是 M2-P5 stage 3 的形状；
- env_name 候选与当前指纹一致 → 正常复用（零创建）；
- legacy 模式 + 显式 env_name → 照旧直接绑定（回归不变）。

## 验收

- reproagent 全量测试绿（当前基线 206）；
- 修好后总体会话重跑云端 `m2-env-reuse` 全四段。
