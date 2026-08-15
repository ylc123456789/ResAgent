# CodingAgent 集成请求：产物路径结构化返回

**来自**: ResAgent Phase 2 产物管理修复计划
**日期**: 2026-08-08
**优先级**: 中（提升可观测性，非阻塞）

---

## 背景

ResAgent Phase 1 已修复 adapter 覆盖 CodingAgent `state.json` 的问题（改为写 `resagent_adapter_result.json`）。但 ResAgent 仍需要知道 CodingAgent 实际产出了哪些文件才能正确注册 artifact。

目前 ResAgent 按惯例猜测 `patch_report.md` 等路径——这在 CodingAgent 输出结构变化时会断裂。

## 请求

在 `PatchReport`（或等效返回值）中增加产物文件列表：

```python
class PatchReport(BaseModel):
    # ... existing fields ...
    produced_files: list[str] = Field(default_factory=list)
    # 例: ["state.json", "diff.patch", "patch_report.md", "logs/action_01.json", ...]
```

路径相对于 `output_dir`。

## 替代方案

如果不想改模型，提供一个独立函数：

```python
def list_output_files(output_dir: Path) -> list[str]:
    """Return all files CodingAgent wrote into output_dir."""
```

ResAgent 在 CodingAgent 返回后调用即可。

## 优先级说明

当前 ResAgent 按约定路径注册 artifact，实际运行中路径是正确的。这个请求是为了消除硬编码约定的脆弱性，不阻塞任何功能。
