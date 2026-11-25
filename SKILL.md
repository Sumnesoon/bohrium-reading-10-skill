---
name: section-evidence-notes
description: 用于科研论文的文献总结、章节摘要、结构化笔记、论文卡片、知识点提取和需要原文证据回链的科研阅读任务。按论文实际章节组织笔记，保留研究问题、数据、方法、结果、结论、限制及页码/引句锚点，并适配案例 output_contract。不要用于脱离原文的自由写作、整篇文献综述代写或仅需 PDF 版面复现而不需要结构化笔记的任务。
---

# 章节对齐的证据型科研笔记

版本 1.2.0｜公开仓库 https://github.com/Sumnesoon/bohrium-reading-10-skill ｜许可 MIT ｜脚本依赖 Python 3.10+，PyMuPDF 可选

## 目标与边界

把实际提供的论文转换为可复核、可检索、可比较、可继续加工的结构化笔记，并完成案例本身要求的回答、翻译、提取、核验或知识结构化。章节树和论文卡片是中间组织方式，不能替代 `output_contract` 要求的最终答案。

只使用实际材料。不得根据标题、文件名、领域标签或常识补造论文内容；不得提交预置答案；不得把本地示例称为正式案例结果。

## 不可违反的原则

1. 先读 `/input/case.json` 与 `task.md`，确定 `case_id`、任务类型、语言、材料及原始 `output_contract`。
2. 先建立诚实的结果，再逐步完善。中间文件放 `/app/work`，最终结果放 `/app/output`。
3. 输出语言跟随任务；术语可保留原文。论文未给出的缩写全称不得凭常识补写成原文事实。
4. 结果与结论分开；作者报告的限制与本次处理的缺口分开。
5. 关键论断必须有证据；未报告、无法判断、冲突和失败必须分开记录。
6. 机器校验只证明结构和文本匹配。引句是否支持论断、数值条件是否对应、论证关系是否成立仍需语义复核。
7. 保存真实变更记录和运行状态。不得虚构工具、模型调用、耗时、Benchmark 或平台评分。

## 第一优先级：初始化与契约

将当前已加载的技能目录记为 `SKILL_ROOT`（平台通常安装到 `/skills/weekly-skill`），从该目录解析脚本，不依赖调用时的工作目录。

先设置可移植的 Python 启动器（Linux 通常为 `python3`，Windows 可设为 `python`）。初始化会递归发现 `/input` 下的 PDF；不要照抄不存在的占位路径：

```bash
PYTHON="${PYTHON:-python3}"
"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" init \
  --case /input/case.json --task /input/task.md \
  --input-dir /input --work /app/work --output /app/output
```

只有已知实际路径且需要指定输入目录外的文件时，才追加 `--pdf /actual/path/paper.pdf`；可重复提供。该命令只探测环境、读取案例并生成最小合法的部分结果，不产生论文语义结论。

随后阅读：

- `/app/work/case_context.json`：真实输入和原始契约；
- `/app/work/environment.json`：实际检测到的工具；
- `/app/work/contract_values.json`：需要由 Agent 按案例填写的最终字段；
- `/app/output/result.json`：已提前保存的部分结果。

如果原始契约与上述辅助文件不同，以原始 `case.json` 和 `task.md` 为准。不得为了适应模板而删除或重命名契约字段。

## 提取文本与章节树

对实际需要阅读的 PDF 执行：

```bash
"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" extract \
  --pdf /actual/path/paper.pdf --work /app/work --backend auto
```

`auto` 的真实回退顺序为：工作区或其祖先目录中的 `.parser/<论文文件名>.jsonl` Host 解析器 → PyMuPDF（同时兼容 `pymupdf` 与旧版 `fitz` 导入名）→ `pdftotext -raw`。可用 `--parser-jsonl` 指定真实解析文件，也可用 `--backend host|pymupdf|pdftotext` 固定后端。Host 文件的结构校验失败会记录真实错误；自动模式才允许继续尝试下一后端。

脚本生成：

- `pages.jsonl` 与 `pages/page_XXXX.txt`：逐页原始文本和可读性信息；
- `blocks.jsonl`：区块、坐标、阅读顺序和候选章节归属；
- `sections.json`：章节候选及切分依据；
- `extract_report.json`：实际后端、回退尝试、页数与异常；
- 缺字和图表检查：页面中的 `suspect_glyph_tokens`、`text_layer_integrity_review`、`needs_visual_review`。疑似缺字不得静默改正，必须渲染核对并保存依据。

这些结果是候选，不是绝对真值。Host `kind` 会阻止 figure/caption/table/equation/document_title 被误检为章节；普通文字仍需检查图注、坐标轴、年份和页码误检。编号标题与正文同块时，完整原文保留在区块中，而 `segmentation_evidence.quote` 只含真实标题片段。检查章节树是否来自原论文，标题层级和页码是否合理；无编号标题、中文标题、参考文献、致谢和附录不得静默丢弃。标题纠错必须有 `title_correction` 的来源原词、修正词、审阅人和理由。切分错误必须先修正，再继续写笔记。

### 附录字母标题与漏检修复（v1.2）

Host 解析器常把 `Broader Impact`、`Acknowledgments`、附录 `A Implementation Details` 一类真实标题标成普通 `text`。本版规则：

- 字母标题只有形成**连续的 A→B→C… 链**才会被接受（避免把正文里 "A model …" 的句子误判成章节）；`References/参考文献` 之后允许 2 个连续的字母标题作为前序。
- `Broader Impact`、`Limitations`、`Ethics Statement`、`Data Availability` 等无编号标题走已知标题白名单。
- **References 边界保护**：参考文献章节不得继续吞并其后出现的字母标题；若仍吞并，`validate` 会报 `references_contains_heading`。
- **章节完整性检查**：`validate` 会把「区块标题候选 + PDF 大纲」与 `sections.json` 比对，有候选但未入树即 `section_completeness` 警告；这类警告在请求 `success` 前必须修正或带理由复核。

章节修正必须走 CLI，不要手改 JSON：

```bash
# 查看章节树
"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" sections list --work /app/work

# 在某个区块处切分（after：该区块起为新节；before：该区块之前的块为新节）
"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" sections split \
  --work /app/work --section sec_0024 --at-block p0017_b001 \
  --title "A Implementation Details" --reviewer "Agent semantic review" \
  --rationale "Host 解析器把真实附录标题标为 text；已对照第 17 页区块原文核对"

# 合并相邻重复章节（会同时把被合并章节的笔记迁到保留章节）
"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" sections merge \
  --work /app/work --sections sec_0034,sec_0035 \
  --reviewer "Agent semantic review" --rationale "两次切分产生的重复附录节"

# 修正标题（写 title_correction）
"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" sections rename \
  --work /app/work --section sec_0004 --title "2.1 Models" \
  --reviewer "Agent semantic review" --rationale "标题行含正文尾巴，已按原文首行截断"
```

每次修改都会写入 `changes.jsonl`（含 reviewer、rationale、原块 ID、页码、移动块数），并重算 `pdf_page_start/end`；可审计、可回放。

三个后端都不可用时必须保留部分结果并写明人工接管位置，不得假装提取成功。`pdftotext -raw` 没有坐标或字体证据，章节覆盖和阅读顺序必须额外检查。

浮动表格或图片可能物理上位于其逻辑章节标题之前。保留实际区块和阅读顺序，不要为校验而篡改；笔记按语义归属，跨章节锚点须保留真实 `section_id` 并附 `cross_section_reason`，说明实际物理位置与逻辑引用关系。

## 阅读顺序与异常处置

按以下顺序尝试处置，而不是一开始就放弃：

1. **双栏错序**：检查区块坐标与阅读顺序；使用可用工具重排后重新生成页面文本。错误未解决时缩小可信范围。
2. **扫描页**：使用实际可用的 OCR；标注 `source: "ocr"`，低置信内容不得写成确定事实。
3. **图表或公式文本缺失**：在 Host 支持时渲染相关页面并读取；图内结果仍不可核验时，标注“仅见图 X，未能核验”。
4. **加密或权限受限**：停止该文件的内容推断，记录需要的密码或权限。
5. **超长文**：先处理契约和任务相关章节，同时保留全篇章节树与未处理范围。
6. **非中文论文**：按原文证据提取，输出语言跟随任务，不默认翻译。

本脚本提供显式的局部渲染与 OCR 入口：

```bash
"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" render \
  --pdf /actual/path/paper.pdf --pages 3,7-8 --work /app/work

"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" ocr \
  --pdf /actual/path/paper.pdf --pages 3 --lang eng --work /app/work
```

OCR 和渲染只针对任务相关页面，并受请求和时间预算约束。渲染优先 PyMuPDF，不可用时使用 `pdftoppm`。中文扫描页按实际可用语言使用 `--lang chi_sim`，英文使用 `eng`，也可使用实际安装的多语言组合；不得声称未安装的语言已执行。OCR 文本与原始文本层分开保存，不能把 OCR 结果当成逐字可靠的原文。OCR 锚点须写 `source: "ocr"` 并与对应 OCR 文本匹配；视觉上更正的引句须写 `source: "rendered_page"`、确保 `render_report.json` 中对应 PNG 真实存在，并附 `visual_review: {reviewed: true, reviewer, rationale}`，说明实际从何图核验。

实际发生重排、OCR、章节修正或字段修复时，记录真实变更：

```bash
"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" record-change \
  --work /app/work --event section_review \
  --detail "Reviewed heading candidates against actual page text" \
  --files sections.json
```

## 逐节写笔记

阅读相关区块后，把实际笔记逐行写入 `/app/work/notes.jsonl`。每行是一个 JSON 对象，格式详见 `references/anchor-format.md`。

每条笔记至少包含：

- 稳定的 `note_id`；
- 有效的 `section_id`；
- `field`、`content`、`status`、`anchors`；
- 可判断时增加 `argument_role` 与 `section_transition`，写明承接上节或引出下节的关系；无法判断就省略，不写套话。

状态只能是：

- `reported`：材料明确报告；
- `not_reported`：在明确的 `searched_scope` 内未找到；
- `uncertain`：材料存在但不能可靠判断；
- `conflict`：材料之间存在冲突，分别列出，不擅自裁决。

`reported`、`uncertain` 和 `conflict` 必须有真实锚点。锚点优先包含 PDF 页序、逐字引句、章节和区块；印刷页码只在实际可识别时填写。表格必要时给出行列，图或公式给出编号。引句必须来自所标页面；规范化只用于匹配，不得静默改写原文。

数值约定（v1.2）：

- 原文写作 `100GB`、`32GB`、`9M`、`880 万`、`5 · 10^-3` 时，按数值 100/32/9000000/8800000/0.005 比对，不会截断成 10/3。
- 原文用英文数词（`ten retrieved documents`、`beam size of four`、`Two annotators`）时，笔记写 10/4/2 也算命中。
- 自己算出的差值/比值（如 57.9−56.8=1.1）请在该条笔记加 `"derived": true`，或不要写进正文；校验器不要求派生值在引句里逐字出现，并会记录可解释的派生值。

六要素之外的重要概念、变量、方法和结论可写成独立知识条目。术语与缩写归一并保留原文表面形式，不得把不同概念错误合并。

## 汇总论文卡片

填写 `/app/work/paper_card.json` 的六个字段：

1. `research_question`；
2. `data`；
3. `method`；
4. `results`；
5. `conclusion`；
6. `limitations`。

每项包含 `status`、`content`、`source_note_ids`；`not_reported` 必须附 `searched_scope`。保留字段到章节/笔记的映射。

结果记录实验或观察事实；结论记录作者据此作出的解释。`limitations` 只放作者明确报告的限制；OCR、缺页、图表不可读等放入 `uncertainty.json`。Agent 自己推断的限制必须标为推断，不能冒充作者观点。

状态与内容必须自洽（v1.2 由 `status_semantics` 检查）：只要内容里出现「附录 E 承认…」「Broader Impact 指出…」这类作者自己报告的限制，状态就必须是 `reported`；`not_reported` 又列出明确限制会被告警。`conflict` 至少要有两条指向不同表述的锚点。判定示例见 `references/field-dictionary.md`。

## 回答案例并组装输出

把案例要求的最终答案、翻译、字段或知识结构写入 `/app/work/contract_values.json`。该文件必须忠于原始 `output_contract`，论文卡片不能代替它。契约字符串、数组和对象不得用空值冒充完成。若存在 `key_results`，每项必须通过 `note_id`/`source_note_ids` 绑定真实笔记，或直接包含可检查的 `pdf_page` 与 `quote` 锚点；`evidence` 同样必须引用实际笔记或提供直接锚点。数组元素内部可增加这些来源字段，但不得删除或重命名原契约既有字段。

执行：

```bash
"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" validate \
  --case /input/case.json --work /app/work

"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" assemble \
  --case /input/case.json --work /app/work --output /app/output --lang zh
```

`--lang zh`（默认）输出中文标签的可读笔记（`结构化研究笔记 / 论文卡片 / 章节对齐笔记`），JSON 字段名保持英文不变；需要英文标签时用 `--lang en`。

最终至少保留：

- `/app/output/result.json`；
- `/app/output/notes.md`；
- `/app/output/validation_report.json`。

检查 `validation_report.json` 的 `errors`、`warnings` 与 `warning_details`。每个实质警告都要修正；确属误报或有意略去的章节，只能在实际复核后记录带理由的决定：

```bash
"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" review \
  --work /app/work --target sec_0003 --code section_coverage \
  --reviewer "Agent semantic review" \
  --rationale "Checked the actual section and recorded why this parent heading needs no separate note"

"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" status --work /app/work
```

只有契约非空且类型正确、实际笔记和章节存在、章节/锚点/关键结果证据链通过，并且 `errors=0`、未复核 `warnings=0` 时，才可在最后一次组装时附加 `--request-success`。未通过时脚本返回非零并保持 `partial`，不得手工把状态改成成功。`status` 报告真实墙钟耗时；超过 900 秒会强制失败，接近预算时应先保留部分结果。

如果案例要求可编辑 Office、幻灯片或图示产物，还需生成真实文件和对应的 PDF/PNG 渲染预览；不得伪造预览。

## 请求与时间预算

脚本提取和机械校验不消耗模型请求。模型只处理必要的语义工作：

- 不默认逐页或逐节各发一次请求；
- 先按任务检索相关章节和区块，再批量生成小结构；
- 每批立即落盘，单次回复远离 16384 token 上限；
- 预留最后字段修复和复核预算；
- 总计不得超过 64 次模型请求、900 秒；
- 工具没有提供真实计数时，不得自行编造请求数或耗时。

每发一批模型请求就记一行真实账本，`status` 会据此报告已用/剩余额度：

```bash
"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" request \
  --work /app/work --stage section_notes --model "<model id>" \
  --input-tokens 3200 --output-tokens 1800 --source ledger
```

- `source=ledger`：Agent 自己按实际批次登记；`native`：平台提供了原生 usage ledger 时使用；`estimated`：只能标注为估算。
- 超过 64 行时 `validate` 直接报错、`assemble` 保持 `partial`。
- 平台完全不给计数时，保持 `request_source=unknown`，不要写 0。

提取阶段支持缓存：`pages.jsonl`/`blocks.jsonl`/`sections.json` 比 PDF 新时 `extract` 直接复用（`extract_report.json` 记 `cached: true`），需要重跑时加 `--force`。各阶段耗时写入 `timings.jsonl`，`status` 输出 `stage_seconds`；墙钟达到 700 秒会给出 `deadline_warning`，到 900 秒强制 `partial`。

优先保证：合法部分结果 → 契约答案 → 关键证据 → 六要素 → 扩展知识条目。

## 完成前复核

逐项检查：

- 笔记骨架是否对应原论文章节和论证顺序，而非通用摘要模板；
- 研究问题、数据、方法条件、结果、结论和限制是否覆盖；
- 关键结论是否保留前提、对象、数值、单位和比较条件；
- 章节承接是否有材料依据；
- 每条关键笔记是否能回到实际页码、章节、段落、图表或公式；
- 缩写、同义词是否归一且未错误合并；
- JSON 与可读笔记是否一致，稳定 ID 是否支持跨篇比较；
- 字段字典是否区分机器判定与人工确认；
- 异常是否先尝试处置，再诚实标记剩余边界；
- `case_id`、原始契约字段、状态、来源、不确定性、变更记录是否齐全。

验证指标、对照方法与创新边界见 `references/validation-and-evaluation.md`。调用与维护说明见 `USAGE.md`，最小示例见 `examples/minimal_example.md`。
