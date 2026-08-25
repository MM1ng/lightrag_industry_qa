# OFFICIAL-PATH-CONTEXT-001: C007 路径差异

## 受影响问题

- C007（安全警告类，跨页相关证据）。

## 差异

| 路径 | 结果 |
|---|---|
| Harness（Phase 4 R0） | 回答，3 条引用命中 2 条黄金证据 |
| 官方 FastAPI | 拒答（无引用） |

## 原因

- Evidence Policy 候选范围不同：Harness 使用冻结池 top-12 行，官方路径使用全部官方检索候选；
- final-context 渲染不同：build_context 纯文本 vs _selected_context 带 header；
- 因此 full Prompt 不同。

## 判定

- 当前不是模型输出波动（输入本身不同）；
- 当前未修改算法/Prompt；
- 不阻塞本次总体 RC（canonical 口径下 C007 为唯一 baseline-only-success，门禁允许）；
- 后续不得通过 question_id 特判；
- 如需统一两条路径，必须单独进行受控实验；
- 官方 FastAPI 仍为权威发布路径。
