# 最小调用示例（仅示意接口）

以下内容是**合成示例**，不对应平台固定案例，不是任何论文的结果，也不得作为预置答案提交。

## 示例输入

`case.json`：

```json
{
  "case_id": "demo-001",
  "output_contract": {
    "type": "object",
    "properties": {
      "answer": {"type": "string"},
      "evidence": {"type": "array"}
    },
    "required": ["answer", "evidence"]
  }
}
```

调用：

```bash
SKILL_ROOT="${SKILL_ROOT:-/skills/weekly-skill}"
PYTHON="${PYTHON:-python3}"
"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" init \
  --case /input/case.json --task /input/task.md \
  --input-dir /input --work /app/work --output /app/output

"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" extract \
  --pdf /input/example.pdf --work /app/work

"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" sections list --work /app/work
```

## 示例笔记输入

Agent 在读取真实材料后，才可写类似结构；下面的文字仅用于说明字段：

```json
{"note_id":"note_0001","section_id":"sec_0001","field":"research_question","content":"示例：替换为实际论文中的研究问题。","status":"reported","anchors":[{"pdf_page":1,"printed_page":null,"section_id":"sec_0001","quote":"替换为所标页面中实际存在的逐字引句。","source":"text_layer"}]}
```

## 示意输出

```json
{
  "answer": "示例：这里放案例实际要求的答案。",
  "evidence": [],
  "case_id": "demo-001",
  "status": "partial",
  "uncertainty": [
    "示例：尚未完成真实论文处理，因此状态保持 partial。"
  ]
}
```

只有在真实提取、笔记、案例答案和全部校验完成后，才将状态请求为 `success`。
