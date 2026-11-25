# 章节、笔记与锚点格式

以下为**接口格式示意**，不是任何论文的真实内容或正式案例答案。

## 章节树

`sections.json` 中每个章节包含：

```json
{
  "section_id": "sec_0002",
  "title": "2 Actual section title",
  "level": 1,
  "pdf_page_start": 3,
  "pdf_page_end": 4,
  "heading_block_id": "p0003_b004",
  "segmentation_evidence": {
    "source": "numbered_heading",
    "quote": "2 Actual section title",
    "needs_semantic_review": true
  }
}
```

切分依据可以是 PDF 书签、章节编号、字体样式或人工复核。必须保留依据，不得把候选标题当成不可质疑的真值。参考文献和附录应保留为独立部分。

## 笔记 JSONL

`notes.jsonl` 每行一个对象：

```json
{
  "note_id": "note_0001",
  "section_id": "sec_0002",
  "field": "method",
  "content": "示例：这里写从实际材料提取的方法及必要条件。",
  "status": "reported",
  "argument_role": "示例：说明该节在研究论证中的作用。",
  "section_transition": {
    "from_section_id": "sec_0001",
    "to_section_id": "sec_0003",
    "relation": "示例：有材料依据时填写，否则省略"
  },
  "anchors": [
    {
      "pdf_page": 3,
      "printed_page": "2",
      "section_id": "sec_0002",
      "block_id": "p0003_b004",
      "quote": "逐字复制所标页面中真正支持本条笔记的原文。",
      "source": "text_layer",
      "bbox": [72.0, 100.0, 300.0, 120.0],
      "table": null,
      "row": null,
      "column": null,
      "figure": null,
      "formula": null
    }
  ],
  "uncertainty": []
}
```

不要照抄示例内容。`printed_page` 不可识别时填 `null`，不得按 PDF 页序推算。坐标不可得时可省略 `bbox`，不能编造。

## 引句与规范化

校验脚本和 Agent 使用相同的规范化规则：

1. Unicode NFKC；
2. 恢复常见拉丁连字；
3. 去除软连字符；
4. 修复字母单词跨行连字符；
5. 将连续空白折叠为一个空格并去除首尾空白。

原始引句必须真实来自页面文本。规范化用于抵消换行、连字和空白差异，不允许用于改写含义、替换数字或拼接不相邻文本。无法匹配时先检查页码、栏序和引句，不得放宽到选择任意相似文本。

## 表格、图和公式

- 表格：增加 `table`、稳定的 `row`、`column`；重复表头不计作数据行。
- 图：增加 `figure`；文本层没有图中数据时写 `source: "rendered_page"` 或标为仅见图且未核验。
- 公式：增加 `formula`；识别不可靠时保留位置，不臆造 LaTeX。
- OCR：写 `source: "ocr"`，其引句须在 `ocr_report.json` 所指向的同页真实 OCR 文本中匹配；编码按 UTF-8 读取，需语义复核。
- 渲染视觉纠错：写 `source: "rendered_page"`，对应页 PNG 必须实际存在，并附 `visual_review: {"reviewed": true, "reviewer": "实际审阅者", "rationale": "从哪幅图核对、修正了什么"}`。仅声明看过图而没有文件或理由不能通过。
- Host 解析：使用 `resourceId`/`block_id` 保留来源；Host 的 `figure`、`caption`、`table`、`equation`、`document_title` 不得成为章节候选。
- 标题纠错：页面文字缺字时保留原引句；如标题采用视觉修正词，须在 `segmentation_evidence.title_correction` 保存 `from`、`to`、`reviewer`、`rationale`。

## 契约与复核决定

`key_results` 每项和 `evidence` 必须引用真实 `note_id`/`source_note_ids`，或带直接 `pdf_page`、`quote` 锚点。`review_decisions.json` 的每项包含 `target`、`code`、`decision: "reviewed"`、`reviewer`、`rationale`、`reviewed_at`；也可通过 `notes_tool.py review` 生成。它只记录具体复核结论，不得用于 blanket 忽视所有错误。运行超过 900 秒不能通过复核决定豁免。

## 变更与不确定性

- 提取、重排、OCR、字段修复和输出组装写入 `changes.jsonl`。
- 作者报告的限制进入 `paper_card.limitations`。
- 缺页、OCR、图表不可读、材料冲突和处理范围限制进入 `uncertainty.json`。
- 不得用变更记录伪装未实际发生的处理。
