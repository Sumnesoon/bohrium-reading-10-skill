# 安装、执行与交付

## 安装

在 DeepSeek Harness 项目根目录，将完整的 `paper-evidence-audit` 文件夹复制到 `.dsh/skills/`。也支持当前 DSH Home 的 `skills/`。启动该项目会话，显式请求使用 `paper-evidence-audit`。Agent 用 `skill` 工具加载入口及资源路径。官方环境由评测器直接安装本包至 `/skills/weekly-skill`。

需要 Python 3.10+。自带 PyPDF2 3.x，第三方许可证在 `vendor/PyPDF2/LICENSE`。可选 PyMuPDF 用于版面解析和渲染，可选 Poppler、Tesseract 或 Windows 系统组件用于渲染和 OCR，可选 jsonschema 用于按声明版本校验高级契约。无 jsonschema 时执行包内契约字段及类型检查。可用平台工具时读取 `environment/resources.yaml` 的真实声明，再根据真实帮助调用。

## 最小调用示例

**合成示例。** 测试输入为 `case.json`、`task.md` 和 PDF。不使用比赛案例或答案。示例 case 为 `{"case_id":"demo","output_contract":{"type":"object","properties":{"answer":{"type":"string"}},"required":["answer"]}}`。任务可写“按原文章节概述研究问题，并引用一条可以复核的证据”。

```sh
python "$S/scripts/audit.py" prepare
python "$S/scripts/notes_tool.py" extract --pdf /input/demo.pdf --work /app/work
python "$S/scripts/audit.py" find --term Introduction --term Results
python "$S/scripts/audit.py" read --page 2 --page 3 --max-chars 12000
```

先读 `case_context.json` 确认材料路径。上面 `demo.pdf` 仅表示示例文件名。本地执行时显式传 `--input`、`--work`、`--output`。底层新建案例可用 `notes_tool.py init --help` 查看参数。使用不同文件或抽取来源时重建索引，复核旧锚点。

写入工作目录中的文件。

| 文件 | 内容 |
| --- | --- |
| `notes.jsonl` | 每行含 `note_id`、`section_id`、`field`、`status`、`content` 和 `anchors`。编号如 `note_0001`。用实际章节 ID。可用字段 `research_question`、`data`、`method`、`results`、`conclusion`、`limitations`、`section_summary` |
| `paper_card.json` | 用六个基本字段作键。各值含 `status`、`content`、`source_note_ids` |
| `contract_values.json` | 案例实际要求的结果值。初始占位和空字段只适合部分结果。每项关键结果提供真实笔记引用或直接锚点，并沿用案例类型 |
| `uncertainty.json` | 数组，每项写 `kind`、`detail`，说明缺项、原因、已尝试工具和人可接管位置 |
| `table_bindings.json` | 见证据手册。关键表格结论对应的行列与契约位置 |
| `figure_bindings.json` | 编号图结论对应的契约位置、完整横纵轴和文本或视觉来源 |

笔记条目可使用 `reported`、`not_reported`、`uncertain`、`conflict`。等待确认的卡片字段可用 `unresolved`。合成输出示意为 `{"case_id":"demo","answer":"研究问题及其出处摘要","status":"partial","run_status":"partial"}`，真实输出还包含来源、结构化笔记、运行记录和不确定性。

```sh
python "$S/scripts/audit.py" checkpoint --batch /app/work/batch-01.json
# 审核完且契约和任务均完成后
python "$S/scripts/audit.py" finish --complete
```

退出码 2 表示检查未通过或完整交付条件未达到。检查 `acceptance.json`、`validation_report.json` 和 `uncertainty`。程序会保留部分结果。机械检查只验证契约结构和可定位证据，任务内容、范围、统计含义和限制仍须复核。扫描页优先用 OCR 或真实渲染，按实际情况标识无法读取的页。着手处理图表前先写早期检查点。不同 PDF 分目录抽取，保持源文件与页码对应。

`checkpoint` 将当前答案和笔记组装为部分结果。`--batch` 接收一个小 JSON 对象，可含 `notes`、`contract`、`paper_card`、`uncertainty`、`table_bindings`、`figure_bindings`；笔记按 `note_id` 更新，绑定按 `claim_id` 更新，契约和卡片合并顶层字段。批次先做事务性预检：标量表格指针、完整变量/行表达式、单位以及笔记的 outline 归属必须一致；唯一的 outline 目标会自动修正，无法唯一修正则整批拒绝且旧成果不变。每批只写 2–4 条笔记，避免临时生成大型构建脚本。`check` 存在错误或未解决告警时返回 2。`finish --complete` 只有契约、笔记和告警全部通过才成功。不要串行执行三个相同检查。

prepare 和 checkpoint 生成的都是可恢复快照，不结束运行、也不冻结计时。PDF 抽取成功后会自动生成一条带精确引句的 `preliminary_evidence` 并立即组装 partial 输出；它是首份真实证据，不是完成答案。finish 才写入 `finished_at` 和最终 `elapsed_seconds`。中断后再次 checkpoint 会沿用原始 `started_at`，已有笔记和契约字段不会丢失。

`find` 默认最多返回 6 页，可用 `--limit` 调整（最多 20）。`read` 默认共享 12000 字符上限，最大 24000；返回 `next_offset` 时用同一页和 `--offset` 续读。截断不表示该页没有更多证据。章节索引及渲染按需调用，工具缺失时记录一次并降级，不安装无关依赖或更改权限。
