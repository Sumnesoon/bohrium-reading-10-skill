# 可核验的证据

文字锚点最小结构为 `{"pdf_page":2,"quote":"实际原文短引句","source":"text_layer"}`。有确切区块时才填写 `block_id`。印刷页可另填 `printed_page`。区块与逻辑归属跨章节时保留实际 `section_id` 并解释 `cross_section_reason`。渲染锚点用 `source: rendered_page`，先制作真实页图并实际查看，再填写 `visual_review` 对象中的 `reviewed: true`、`reviewer` 和 `rationale`。OCR 锚点须与实际 OCR 文本匹配。区分 PDF 索引页与印刷页。

## 密集表格

`table_bindings.json` 是数组。下例仅为合成格式说明。

```json
[
  {
    "claim_id": "/key_results/0/value",
    "pdf_page": 2,
    "table": "Table 1",
    "variable": "Rate",
    "parameter_header": "Parameter",
    "columns": ["Baseline", "Extended"],
    "row": "Rate",
    "cells": ["1.4 ± 0.2", "1.8 ± 0.3"],
    "column": "Extended",
    "value": "1.8 ± 0.3",
    "unit": "s^-1"
  }
]
```

`claim_id` 必须直接指向原始契约中的一个标量单元格值，不能指向含多个变量或多个数值的长段落。`variable` 是答案声称的变量，`row` 是表中的完整行表达式；两者数学规范化后必须完全一致。`σ₈` 与 `σ₈Ωₘ^0.25`、`H` 与 `H₀` 均视为不同变量。`unit` 必填，无量纲写 `dimensionless`。逐列记录同一行，包括空格、上下限和必要表头。保持表头与数据行列数一致、显著数字和末位零，区分单位与百分比，区间口径引用表注。

同一行比较两列时建立两个不同 `claim_id` 和两个 binding；它们可以共享完整 `columns`、`row`、`cells`，但各自的 `column`、`value` 必须唯一对应。批次写入先做事务性预检，任一单元格、变量或章节绑定错误时整批不写入。

文本顺序完好时，校验器核对同一编号表的连续列头和整行，再检查目标列以及对应契约值。单元格合并、复杂列层级、空项或文本顺序不可靠时渲染实际 PDF，人工读图后写 `source: rendered_page`，保留 `visual_review.reviewer` 与 `visual_review.rationale`，说明列头如何与表格单元格对应。结果会标记为视觉转录。图形趋势给出图号、面板、轴和比较条件，估读需标注近似及误差条件。计算结果保留输入值和公式。

## 图形坐标

`figure_bindings.json` 每项至少包含 `claim_id`、`pdf_page`、`figure`、`x_axis`、`y_axis` 和 `source`。文本层或 OCR 来源还需 `axis_quotes`，其中横纵轴引句必须分别与完整轴变量精确相等；渲染来源需真实页图和 `visual_review`。文本层只能证明坐标与正文/图注陈述，不能证明曲线走势已经目视核验。若任务要求直接观察趋势而无视觉能力，必须标为未核验。
