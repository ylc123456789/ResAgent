# CodingAgent 问题：apply_patch 生成损坏的 unified diff

- **日期**: 2026-08-20
- **来源**: L3 能力验证测试（task_002，从零写 CIFAR-100 train.py）
- **严重度**: 高（导致 coding 任务连续失败，阻塞整个 run）
- **现场**: `/root/autodl-tmp/resagent-workspace/runs/l3-lrschedule/tasks/codingagent/task_002/`
- **归属**: CodingAgent（本文件供 CodingAgent AI 修复参考）

---

## 现象

task_002（在 CIFAR-100 训练脚本里实现 cosine/linear 两种调度）失败，报错：

```
patch failed validation/application after 2 repair attempt(s):
error: patch fragment without header at line 25: @@ -39,12 +40,12 @@ ...
error: corrupt patch at line 14
```

attempt_001、attempt_002 两次都失败，根因一致。

## 根因

模型选择了 `apply_patch`（手写 unified diff），hunk 头 `@@ -X,Y +A,B @@` 的**行号/计数算错了**。这是 LLM 手写 unified diff 的固有弱点——精确行号极难生成对。而 repair 循环（`_apply_patch_with_repair`）让模型修 patch 时，**模型又生成一个新的 diff**（还是错），而不是退回到结构化编辑或整文件重写，于是修 2 次都栽在同一个"手写 diff 行号错"上。

## 证据文件

- `attempt_001/logs/failed_patch_04_01.patch` + `.stderr`：损坏的 diff 和错误信息。
- `attempt_001/logs/repair_context_04_01.json` / `repair_response_04_01.json`：修复尝试（模型又返回了一个 diff）。
- `attempt_002/logs/failed_patch_12_01.stderr`：`corrupt patch at line 14`。

## 优秀实践对照

| 系统 | 编辑方式 |
|---|---|
| SWE-agent | `str_replace_editor`：只做精确 old_str/new_str 替换，无行号 |
| Aider | SEARCH/REPLACE 块（精确文本）+ 模糊匹配兜底 |
| Claude Code | `Edit`（old_string/new_string 精确匹配）+ `Write`（整文件） |

**共识：永远不让 LLM 依赖行号。小改用精确文本锚，大改整文件重写。**

## 修复建议（供 CodingAgent AI 决策）

CodingAgent 已经有 `replace_text` / `insert_before` / `insert_after` / `write_file` 这些无行号工具，prompt 里也写了"prefer structured edits, apply_patch only for 不适合精确编辑的改动"。问题不在缺工具，而在 **apply_patch 失败后的兜底**：

1. **`_apply_patch_with_repair` 的 repair 兜底改成 `write_file`**（`src/coding_agent/controller/actions.py:301`）：diff 修不好时，让 `repair_patch` 返回 `write_file`（整文件内容），系统直接写文件，而不是让模型再生成一个 diff。这是 Claude Code / Aider 的标准兜底——diff 一旦对不上就用整文件重写，绝不反复猜行号。

2. **更彻底（推荐）**：把 `apply_patch` 从动作列表（`src/coding_agent/controller/prompts.py:13`）里去掉，或标记为"绝对最后手段"。`replace_text`（小改）+ `write_file`（大改）已覆盖所有场景；`apply_patch` 是唯一依赖行号的工具，留着就是留一个必炸的雷。

3. 保留 `git apply --check` 校验（`src/coding_agent/runtime/apply.py`，已有），作为 `write_file` 之后 diff 的校验，确认改动符合预期。

## 关联问题（ResAgent 侧，已修）

本次 run 还暴露了一个 ResAgent 侧的 workspace 回退 bug：`modify_code` 任务的 `repo_url` 与 `workspace_path` 都为空时，adapter 传了 `Path("")`，CodingAgent 退回 process cwd（ResAgent 仓库）。已由 ResAgent 侧修复（commit `a742529`）。本文件只针对 CodingAgent 的 patch 生成问题。
