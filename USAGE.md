# 使用与方法说明

## 用途

该 Skill 用于“给实际论文，生成章节对齐、带证据、可继续加工的结构化笔记”，并完成案例规定的问答、翻译、提取、核验或知识结构化。它不是预置论文答案，也不是通用聊天提示词。

## 依赖与来源

- Python 3.10 或更高版本；标准库完成初始化、校验和组装；默认启动器为 `python3`，Windows 可设置 `PYTHON=python`。
- 优先解析 Host `.parser/<pdf-stem>.jsonl`，读取真实 `page/kind/text/resourceId/figurePaths`；不重新解析已经存在的预提取答案，只把原始解析区块转换为可复核结构。
- 可选 PyMuPDF：用于文本、区块、字体和渲染；同时尝试 `pymupdf` 与旧版 `fitz`，在 `environment.json` 记录导入名和实际包版本。脚本不会自动联网安装依赖。
- 可选 Poppler `pdftotext`/`pdfinfo`/`pdftoppm`：用于无 PyMuPDF 时的文本与渲染回退。
- 可选 Poppler `pdftotext`/`pdfinfo`/`pdftoppm`：用于无 PyMuPDF 时的文本与渲染回退。
- OCR 完全可选：`ocr` 会自动使用已存在的 `tesseract` 或 `rapidocr`，都没有时诚实退出 2 并记录 `fallback: render+visual_review`。无文本层的扫描件依然可以走“渲染 PNG + `rendered_page` 锚点 + `visual_review`”，或在平台侧直接用 Host 解析器给出的文本。

版本、命令、后端和失败尝试必须按实际运行记录。资源包不包含 OCR 模型或第三方二进制文件；任何依赖未安装都不会触发自动联网安装，也不影响无 OCR 路径的完整流程。

## 典型调用

设 `SKILL_ROOT` 为已加载的技能目录：

```bash
PYTHON="${PYTHON:-python3}"
"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" init \
  --case /input/case.json --task /input/task.md \
  --input-dir /input --work /app/work --output /app/output

"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" extract \
  --pdf /actual/path/paper.pdf --work /app/work --backend auto

# Agent 阅读实际文本后填写 notes.jsonl、paper_card.json、
# uncertainty.json 和 contract_values.json。

"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" validate \
  --case /input/case.json --work /app/work

"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" assemble \
  --case /input/case.json --work /app/work --output /app/output --lang zh
```

`--lang zh`（默认）让 `notes.md` 使用中文小标题；`--lang en` 保留英文小标题。两种模式生成的 JSON 字段完全一致。

`--pdf` 在已知实际文件路径时推荐使用，可重复提供，也可位于输入目录之外；未提供时只递归发现 `--input-dir` 下的 PDF。路径不存在的显式文件会报错，不会被静默忽略。

`--request-success` 只能用于最后一次，且契约类型/非空性、章节、笔记和证据校验均通过，未复核警告为零时。带代码的警告须先修正，或通过 `review --target ... --code ... --reviewer ... --rationale ...` 记录实际复核决定。`status --work ...` 显示该次运行的墙钟耗时（已组装完成的行读取冻结值，未完成的实时累计）；超过 900 秒禁止成功。失败时脚本返回非零并保留 `partial`。

`key_results` 的每项和 `evidence` 必须绑定真实 `note_id`/`source_note_ids` 或给出直接页码/引句锚点。OCR 与渲染锚点分别要求 OCR 文本匹配或真实渲染 PNG 加 `visual_review` 记录，不能仅用文本层不存在的一句话代替视觉验证。

## 章节修正、请求账本与耗时

章节树不满意时用 CLI 修正，不要手改 `sections.json`：

```bash
"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" sections list --work /app/work
"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" sections split --work /app/work \
  --section sec_0024 --at-block p0017_b001 --title "A Implementation Details" \
  --reviewer "Agent semantic review" --rationale "对照第 17 页区块原文"
"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" sections merge --work /app/work \
  --sections sec_0034,sec_0035 --reviewer "..." --rationale "..."
"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" sections rename --work /app/work \
  --section sec_0004 --title "2.1 Models" --reviewer "..." --rationale "..."
```

每批模型请求登记一次，用于 64 次预算审计：

```bash
"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" request --work /app/work \
  --stage section_notes --model "<model id>" --input-tokens 3200 --output-tokens 1800
"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" status --work /app/work
```

`status` 输出 `requests_used`、`requests_remaining`、`request_source`（`ledger`/`native`/`unknown`）和 `stage_seconds`。超过 64 条账本记录时 `validate` 报错。`extract` 会复用比 PDF 新的 `pages/blocks/sections`（`--force` 可强制重跑），各阶段耗时写入 `timings.jsonl`；墙钟 700 秒起给出 `deadline_warning`。

`assemble` 结束时会把 `finished_at` 与 `elapsed_seconds` 冻结进 `run_state.json`：之后再跑 `validate` 或 `status`，报告的是那一次运行的时长，而不是此后的空闲时间。因此复核旧结果不会被误判为“超出 900 秒”，新的运行仍按墙钟实时累计。

扫描件与图片页按需渲染或 OCR，两步都不是必需：

```bash
"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" render --pdf /input/scan.pdf --pages 1-3 --work /app/work
"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" ocr --pdf /input/scan.pdf --pages 1-3 --work /app/work --engine auto
```

`ocr` 的引擎选择：`auto` 先找 `tesseract`，再找 `rapidocr`；可用 `--engine tesseract|rapidocr` 指定。没有可用引擎时返回 2，`ocr_report.json` 记 `needs_ocr_review=true` 与 `fallback=render+visual_review`；扫描页仍可只靠渲染 PNG 加视觉复核走通。

扫描件与图片页按需渲染或 OCR，两步都不是必需：

```bash
"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" render --pdf /input/scan.pdf --pages 1-3 --work /app/work
"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" ocr --pdf /input/scan.pdf --pages 1-3 --work /app/work --engine auto
```

`ocr` 的引擎选择：`auto` 先找 `tesseract`，再找 `rapidocr`；可用 `--engine tesseract|rapidocr` 指定。没有可用引擎时返回 2，`ocr_report.json` 记 `needs_ocr_review=true` 与 `fallback=render+visual_review`；扫描页仍可只靠渲染 PNG 加视觉复核走通。

哪些命令消耗模型请求：`extract`、`validate`、`assemble`、`render`、`ocr`、`sections`、`status` 都是纯本地脚本，不消耗；只有 Agent 自己调用模型（写笔记、写卡片、写契约）才需要 `request` 记账。

## 输入

必需：

- `case.json`：应含 `case_id`（或 `id`）及任务输出契约；
- `task.md` 或等价任务说明；
- 实际论文和案例材料。

`output_contract` 也兼容 `outputContract`、`output_schema`、`outputSchema` 命名；如果案例采用其他结构，应读取原案例并适配，不能猜字段。

## 输出

- `result.json`：案例字段、`case_id`、状态、结构化笔记、卡片、章节、来源、不确定性、变更记录与机器核验摘要；
- `notes.md`：同一实际笔记生成的可读版；
- `validation_report.json`：错误、警告、锚点检查与仍需语义复核的项目。

中间文件包括环境、页面、区块、章节、卡片、案例字段和变更日志，供独立复核。JSON 与 Markdown 的内容以同一次实际运行数据为准。

## 失败处理

- 任何处理失败都保留最小的 `partial` 结果；
- 加密、空文件、无文本层、OCR 不可用和缺字段分别说明；
- `not_reported` 必须限定实际检索范围；
- 不能把未运行、无法判断和作者未报告写成同一状态；
- 修复后重新 validate 和 assemble，不手工修改成功状态。

## 适用范围与限制

适用于具有可读取材料的科研阅读与结构化笔记任务。无法保证所有 PDF 的章节自动切分、双栏重排、扫描 OCR、图中数据和公式识别都正确；这些环节需要可用工具与语义复核。脚本不会证明论文论断科学正确。

## 维护与复用

修改字段时同步更新 `references/field-dictionary.md`、`paper_card.json` 模板、校验器和示例。修改规范化规则时同步更新 `references/anchor-format.md` 与 `normalize_text`，并用匹配和不匹配两类用例测试。升级 PDF 后端时记录版本并重新跑公开论文对照。

## 自检与回归

```bash
"$PYTHON" "$SKILL_ROOT/scripts/parser_adapter.py" --self-test
"$PYTHON" "$SKILL_ROOT/scripts/self_test.py" --work-root /app/work/self-test
```

前者使用合成 Host JSONL 验证图注过滤、合并标题和中文标题；后者覆盖有效契约、空值拒绝、无来源关键结果、伪造章节、OCR 路径、数值归一与预算失败。所有夹具均为功能测试，不是论文结果。

## 资源与提交包自查

技能资源须为不超过 50 个 UTF-8 文本文件，单文件不超过 500KB，总量不超过 2MB，且不得包含符号链接。检查：

```bash
"$PYTHON" "$SKILL_ROOT/scripts/check_package.py" "$SKILL_ROOT"
```

该检查同时验证 `SKILL.md` 只含 `name`、`description` 两项 frontmatter、关键文档是否存在、`GITHUB_URL.txt` 首行是否为公开 `https://` 链接，并输出 `manifest.json`（逐文件 SHA-256）与上层目录的 `submission.sha256`。通过不代表科学内容正确或平台评审通过。

许可证为 MIT（见 `LICENSE`），版本号见 `VERSION` 与 `scripts/notes_tool.py` 的 `VERSION`；两者必须一致。

两份真实运行结果可做开发对照：

```bash
"$PYTHON" "$SKILL_ROOT/scripts/notes_tool.py" compare \
  --baseline-result /path/to/baseline/result.json \
  --skill-result /path/to/skill/result.json \
  --out /path/to/comparison.json
```

比较结果只作描述性开发证据；同论文、同任务、同解析条件和同预算仍由实验协议保证。

最小调用示例见 `examples/minimal_example.md`；该示例仅展示接口，不是正式案例输出。公开作品链接另按赛题要求放在提交根目录的 `GITHUB_URL.txt`，未提供真实公开链接时不应虚构。
