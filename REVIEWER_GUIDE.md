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
- 自测：`self_test.py` 29 项、`parser_adapter.py --self-test` 20 项，全部通过；夹具为合成数据，不是论文结论。
- 对照：ColBERT 单案例 3 次重复（基础 vs 加载技能）在本地记录过，数值不随包提交；跨论文重复未做，不作增益结论。

## 想亲手跑（可选）

只有这一条需要 Python 3.10+，其余依赖都可缺省：

```bash
python3 scripts/self_test.py
python3 scripts/check_package.py .
```

`check_package.py` 会校验文件数、单文件体积、UTF-8、frontmatter 与 `GITHUB_URL.txt`，并重算 `manifest.json`。没有 PyMuPDF 也能跑；`extract` 在没有 PyMuPDF 时会尝试 Poppler，再不行就老实报错。

## OCR 与扫描件：不需要配置

- 有文本层的 PDF 直接走文本层或平台 Host 解析器，不碰 OCR。
- 扫描件优先用 Host 解析器给出的文本；没有 Host 文本时，用 `render` 生成真实 PNG，再用 `rendered_page` 锚点加 `visual_review` 记录。
- `ocr` 是可选项：存在 `tesseract` 或 `rapidocr` 时自动使用，都没有时退出码 2 并记录 `fallback: render+visual_review`。**评审侧无需安装 Tesseract 或任何模型。**

## 已知限制

- 章节标题、层级与边界是启发式，仍需语义复核；校验器只能证明“对得上原文”，不能证明科学结论正确。
- OCR 依赖外部引擎；本包不携带 OCR 模型或二进制。
- 跨论文 benchmark 未补齐；现有对照只覆盖 ColBERT 单案例。
- `human_review_only` 规则与正式提交窗口以赛事方书面说明为准。

## 文件地图

- `SKILL.md`：技能入口与执行原则。
- `USAGE.md`：命令、参数、失败处置与维护方式。
- `references/`：锚点格式、字段字典、验证与评估口径。
- `scripts/`：`notes_tool.py`（主流程）、`parser_adapter.py`（解析适配）、`self_test.py`、`check_package.py`。
- `examples/`、`assets/`：最小示例与论文卡片模板。
- `GITHUB_URL.txt`、`VERSION`、`LICENSE`、`manifest.json`：形式合规与可追溯信息。
