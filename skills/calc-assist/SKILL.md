---
name: calc-assist
description: 数值与算术辅助：拆解计算步骤，优先用 calculator 工具，避免心算错误。
tools:
  - calculator
---

# 计算辅助 Skill

当问题包含算术、比例、汇总金额时：

1. 把表达式整理成 `calculator` 可接受的形式（仅 `+ - * /` 与括号）。
2. 调用 `calculator`，以工具返回值为准。
3. 在回答中展示计算步骤与最终数值。

不要心算复杂表达式；不要发明未给定的数字。
