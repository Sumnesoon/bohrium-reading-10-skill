# 评审指南（零配置）

这份指南面向评审：不需要安装任何东西，也不需要跑任何命令，就能核对这份作品。

## 30 秒路径

1. 打开公开仓库 `https://github.com/Sumnesoon/bohrium-reading-10-skill`（未登录可读）。
2. 读 `SKILL.md`：作品的目标、原则、可执行流程与边界。
3. 读本目录的 `examples/minimal_example.md`：最小调用形态。
4. 正式案例的产出由平台在固定案例上运行生成，不在本仓库内；本仓库只含 Skill、脚本、模板与说明，不含预置答案。
5. 如需复核数据来源：`manifest.json` 给出每个文件的字节数与 SHA-256。

## 作品做了什么

给定一篇真实论文和一个输出契约，产出“按论文实际章节组织、每条结论都能回到页码与逐字引句”的结构化笔记与案例字段。核心不是模板，而是三件事：

- 章节树来自真实 PDF 的区块与标题证据，漏标题会被校验器抓出来；
- 每条关键结果绑定笔记/引句锚点，锚点用规范化文本与页面核对；
- 失败时保留诚实的 `partial`，不把未测量写成 0、不把未报告写成已报告。

## 期望数字（本地开发自查，非平台固定案例）

- `judge-v3`：19 页 / 194 区块 / 34 节 / 42 条笔记 / 124 个锚点（124 通过）/ 0 错误 / 0 警告 / `status=success`；这是本地自查记录，不随包提交。
- 自测：`self_test.py` 33 项、`parser_adapter.py --self-test` 20 项，全部通过；夹具为合成数据，不是论文结论。
- 包内提供 `vendor/PyPDF2`（reader-only，30 个文件，859,404 字节），所以官方镜像即使没有 PyMuPDF/Poppler，`extract --backend auto` 仍有纯 Python 文本兜底；该兜底不替代布局解析，章节仍需复核。

## 想亲手跑（可选）

只有这一条需要 Python 3.10+，其余依赖都可缺省：

```bash
python3 scripts/self_test.py
python3 scripts/check_package.py .
```

`check_package.py` 会校验文件数、单文件体积、UTF-8、frontmatter 与 `GITHUB_URL.txt`，并重算 `manifest.json`。没有 PyMuPDF 也能跑；`extract` 在没有 PyMuPDF/Poppler 时会使用包内 `vendor/PyPDF2`，它是纯 Python 读取器，不要求评审安装任何依赖。若连 PyPDF2 也无法读取（例如加密或纯图像），脚本会如实报错并保留接管位置。

## OCR 与扫描件：不需要配置

- 有文本层的 PDF 直接走 Host 解析器、PyMuPDF、包内 PyPDF2 或 `pdftotext`，不碰 OCR。
- 平台声明并提供 `pdf-text-tool` / `ocr-tool` 时，Agent 按平台实际说明调用，把真实输出归一化为 `{page, kind, text}` JSONL，再运行 `ingest`；包内不假装知道平台 CLI 参数。
- 扫描件没有平台文本时，用 `render` 生成真实 PNG，再用 `rendered_page` 锚点加 `visual_review` 记录。
- 本地 `ocr` 是可选项：存在 `tesseract` 或 `rapidocr` 时自动使用，都没有时退出码 2 并记录 `fallback: render+visual_review`。**评审侧无需安装 Tesseract 或任何模型。**

## 已知限制

- 章节标题、层级与边界是启发式，仍需语义复核；校验器只能证明“对得上原文”，不能证明科学结论正确。
- OCR 依赖外部引擎或平台工具；本包不携带 OCR 模型或二进制，只带纯 Python PyPDF2 读取器。
- 包内不含平台正式三案例的答案、评分或隐藏测试数据。
- `human_review_only` 规则与正式提交窗口以赛事方书面说明为准。

## 文件地图

- `SKILL.md`：技能入口与执行原则。
- `USAGE.md`：命令、参数、失败处置与维护方式。
- `references/`：锚点格式、字段字典、验证与评估口径。
- `scripts/`：`notes_tool.py`（主流程，含 `ingest`）、`parser_adapter.py`（解析适配）、`self_test.py`、`check_package.py`。
- `vendor/PyPDF2/`：随包携带的 PyPDF2 3.0.1 reader-only 裁剪版（BSD-3-Clause），官方镜像无 PDF 库时的纯 Python 兜底。
- `examples/`、`assets/`：最小示例与论文卡片模板。
- `GITHUB_URL.txt`、`VERSION`、`LICENSE`、`manifest.json`：形式合规与可追溯信息。
