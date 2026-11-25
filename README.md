# 章节对齐的证据型科研笔记（section-evidence-notes）

版本 1.2.0｜MIT License｜Python 3.10+（PyMuPDF 可选，OCR 可选）

本仓库是“文献总结与结构化笔记”赛题的参赛作品：一个把真实论文转成**按原文章节组织、可回链原文、可继续复用**的结构化笔记的 Skill。它不输出预先写好的摘要模板，也不是任何论文的预置答案。

## 目录

- `REVIEWER_GUIDE.md`：评审零配置指南（先读这一份）。
- `SKILL.md`：技能入口，说明用途、调用方式、约束与完成前复核清单。
- `USAGE.md`：完整调用步骤、输入输出解释、方法与维护说明。
- `references/anchor-format.md`：章节、笔记与锚点的接口格式与规范化规则。
- `references/field-dictionary.md`：论文卡片六要素、状态语义与判定方式。
- `references/validation-and-evaluation.md`：验证分层、对照设计与可机器判定的边界。
- `examples/minimal_example.md`：最小调用示例（仅为接口示意）。
- `assets/paper_card.template.json`：论文卡片空白结构。
- `scripts/notes_tool.py`：主脚本（`init / extract / sections / render / ocr / validate / review / request / assemble / status / compare`）。
- `scripts/parser_adapter.py`：Host 解析器 JSONL 适配（含附录字母标题的连续链检测）。
- `scripts/check_package.py`：资源包自查与 `manifest.json`（逐文件 SHA-256）。
- `scripts/self_test.py`：功能自测（合成测试夹具）。

## 快速开始

```bash
PYTHON="${PYTHON:-python3}"
"$PYTHON" scripts/notes_tool.py init \
  --case /input/case.json --task /input/task.md \
  --input-dir /input --work /app/work --output /app/output

"$PYTHON" scripts/notes_tool.py extract --pdf /input/paper.pdf --work /app/work --backend auto

# Agent 阅读实际页面文本后写入 notes.jsonl、paper_card.json、uncertainty.json、contract_values.json

"$PYTHON" scripts/notes_tool.py validate --case /input/case.json --work /app/work
"$PYTHON" scripts/notes_tool.py assemble --case /input/case.json --work /app/work --output /app/output --lang zh
```

`extract` 的后端顺序为 `.parser/<pdf-stem>.jsonl` → PyMuPDF → `pdftotext`；章节修正用 `sections list|split|merge|rename`，不要手改 JSON，所有变更写 `changes.jsonl`。

## 自检

```bash
python scripts/check_package.py .
python scripts/parser_adapter.py --self-test
python scripts/self_test.py --work-root /tmp/self-test
```

当前版本：`self_test.py` 29 项、`parser_adapter.py` 20 项检查全部通过；资源包 16 个 UTF-8 文件、222,160 字节，未超过 50 文件 / 500KB 单文件 / 2MB 总量的限制。

## 设计要点

- **对得上**：章节树保留切分依据（编号标题、已知命名标题、连续 A→B→C 附录链、PDF 大纲），`validate` 用 `section_completeness` 检查“真实标题未入树”。
- **留得住**：论文卡片六要素与笔记均带状态（`reported / not_reported / uncertain / conflict`），作者限制与分析者推断分开，`status_semantics` 检查状态与内容矛盾。
- **找得回**：每条笔记带 PDF 页序、逐字引句与区块 ID；引句经固定规范化后逐页匹配，匹配率与锚点有效率写入 `validation_report.json`。
- **用得上**：稳定 ID、统一字段、中文可读输出（`--lang zh`）与 JSON 同源；`timings.jsonl` 与请求账本（64 次上限）记录真实预算。

## OCR 与依赖：评审侧无需配置

- 有文本层的 PDF 走文本层或平台 Host 解析器，不碰 OCR。
- 扫描件优先用 Host 文本；没有时用 `render` 生成真实 PNG，再用 `rendered_page` 锚点加 `visual_review`。
- `ocr` 自动选用已存在的 `tesseract` 或 `rapidocr`；两者都没有时退出码 2，并记录 `fallback: render+visual_review`。**不需要安装任何 OCR 组件。**

## 证据边界

- 机器校验只证明结构与文本匹配，不证明引句支持论断；语义复核仍需人工或模型完成。
- 本仓库不包含、也不声称包含平台正式三案例的答案或官方评分。
- 跨论文对照尚未补齐，现有 benchmark 只覆盖 ColBERT 单案例。

## 许可

MIT，见 `LICENSE`。版本号见 `VERSION` 与 `scripts/notes_tool.py` 的 `VERSION`，两者保持一致。
