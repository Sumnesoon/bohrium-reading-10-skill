#!/usr/bin/env python3
"""Executable regression checks for the Skill; fixtures are explicitly synthetic."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

import notes_tool as nt
import parser_adapter as pa


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-root", default=str(Path.cwd() / "validation-work"))
    args = parser.parse_args()
    root = Path(args.work_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    checks: list[str] = []
    failures: list[str] = []

    def check(name: str, condition: bool) -> None:
        checks.append(name)
        if not condition:
            failures.append(name)

    with tempfile.TemporaryDirectory(prefix="self-test-", dir=root) as temporary:
        work = Path(temporary) / "work"
        work.mkdir()
        parser_path = work / "host.jsonl"
        pdf_path = work / "synthetic.pdf"
        records = [
            {"page": 1, "kind": "document_title", "text": "Synthetic Paper", "resourceId": "title", "figurePaths": []},
            {"page": 1, "kind": "text", "text": "ABSTRACT\nAn evidence-based synthetic abstract.", "resourceId": "abstract", "figurePaths": []},
            {"page": 1, "kind": "figure", "text": "9 ACKNOWLEDGMENTS", "resourceId": "figure", "figurePaths": ["figure.png"]},
            {"page": 2, "kind": "text", "text": "1 INTRODUCTION\nA documented research question.", "resourceId": "intro", "figurePaths": []},
            {"page": 2, "kind": "text", "text": "2 METHODS\nA documented method.", "resourceId": "method", "figurePaths": []},
            {"page": 2, "kind": "text", "text": "3 RESULTS\nMeasured 42 units.", "resourceId": "result", "figurePaths": []},
            {"page": 3, "kind": "text", "text": "4 CONCLUSION\nA bounded conclusion.", "resourceId": "conclusion", "figurePaths": []},
            {"page": 3, "kind": "text", "text": "Acknowledgments\nA disclosed limitation.", "resourceId": "ack", "figurePaths": []},
            {"page": 3, "kind": "text", "text": "REFERENCES", "resourceId": "refs", "figurePaths": []},
            {"page": 4, "kind": "text", "text": "摘要", "resourceId": "zh-abstract", "figurePaths": []},
            {"page": 4, "kind": "text", "text": "引言", "resourceId": "zh-intro", "figurePaths": []},
            {"page": 4, "kind": "text", "text": "结果", "resourceId": "zh-results", "figurePaths": []},
            {"page": 4, "kind": "text", "text": "参考文献", "resourceId": "zh-refs", "figurePaths": []},
        ]
        parser_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")
        report = pa.extract_with_host_parser(parser_path, pdf_path, work)
        check("Host extraction succeeds", report["ok"] and report["page_count"] == 4)
        sections = nt.read_json(work / "sections.json", {})["sections"]
        titles = {section["title"] for section in sections}
        check("Figure number is not a section", "9 ACKNOWLEDGMENTS" not in titles)
        check("Chinese headings detected", {"摘要", "引言", "结果", "参考文献"} <= titles)
        check("Combined named heading detected", "Acknowledgments" in titles)
        blocks, _ = nt.read_jsonl(work / "blocks.jsonl")
        sections_by_id = {section["section_id"]: section for section in sections}
        block_by_id = {block["block_id"]: block for block in blocks}
        notes = []
        for index, section in enumerate(sections):
            sid = section["section_id"]
            block = block_by_id.get(section.get("heading_block_id"))
            if block is None:
                block = blocks[0]
            notes.append({
                "note_id": f"note_{index:04d}", "section_id": sid, "field": "section_summary",
                "content": "Synthetic evidence statement with no numerical claim.",
                "status": "reported",
                "anchors": [{"pdf_page": block["pdf_page"], "section_id": sid,
                             "block_id": block["block_id"], "quote": block["raw_text"], "source": "host_parser"}],
            })
        result_note = next(note for note in notes if note["section_id"] == next(s for s in sections_by_id.values() if s["title"] == "3 RESULTS")["section_id"])
        result_note["content"] = "Measured result is 42 units."
        nt.write_text_atomic(work / "notes.jsonl", "".join(json.dumps(note, ensure_ascii=False) + "\n" for note in notes))
        all_note_ids = [note["note_id"] for note in notes]
        nt.write_json_atomic(work / "paper_card.json", {
            field: {"status": "reported", "content": "Synthetic nonempty field", "source_note_ids": [all_note_ids[0]]}
            for field in ("research_question", "data", "method", "results", "conclusion", "limitations")
        })
        case = {
            "case_id": "synthetic-self-test",
            "output_contract": {
                "type": "object",
                "properties": {
                    "answer": {"type": "string"},
                    "key_results": {"type": "array", "items": {"type": "object", "properties": {
                        "metric": {"type": "string"}, "value": {"type": "string"}, "setting": {"type": "string"}},
                        "required": ["metric", "value", "setting"]}},
                    "evidence": {"type": "array"}, "limitations": {"type": "string"},
                },
                "required": ["answer", "key_results", "evidence", "limitations"],
            },
        }
        values = {
            "answer": "The synthetic answer reports 42 units.",
            "key_results": [{"metric": "synthetic", "value": "42", "setting": "self-test", "evidence_note_ids": [result_note["note_id"]]}],
            "evidence": [{"note_id": result_note["note_id"], **result_note["anchors"][0]}],
            "limitations": "Reported limitation is represented in an actual note.",
        }
        nt.write_json_atomic(work / "contract_values.json", values)
        nt.write_json_atomic(work / "uncertainty.json", [])
        nt.write_json_atomic(nt.state_path(work), {"started_at": nt.utc_now(), "status": "partial"})
        baseline = nt.validate_work(work, case)
        if baseline["warning_details"]:
            print(json.dumps({"baseline_warning_details": baseline["warning_details"]}, ensure_ascii=False), file=sys.stderr)
        check("Valid synthetic case has no errors", not baseline["errors"])
        check("Valid synthetic case has no unresolved warnings", not baseline["warnings"])

        nt.write_json_atomic(work / "contract_values.json", {"answer": "", "key_results": [], "evidence": "", "limitations": ""})
        empty = nt.validate_work(work, case)
        check("Empty contract fails closed", not empty["ok"] and len(empty["errors"]) >= 4)

        nt.write_json_atomic(work / "contract_values.json", values)
        unbound = json.loads(json.dumps(values))
        del unbound["key_results"][0]["evidence_note_ids"]
        unbound["key_results"][0].pop("evidence", None)
        nt.write_json_atomic(work / "contract_values.json", unbound)
        no_reference = nt.validate_work(work, case)
        check("Unbound key_result is rejected", any("must bind evidence" in error for error in no_reference["errors"]))

        nt.write_json_atomic(work / "contract_values.json", values)
        tampered_sections = json.loads(json.dumps({"sections": sections}))
        tampered_sections["sections"][1]["segmentation_evidence"]["quote"] = "fabricated heading evidence"
        nt.write_json_atomic(work / "sections.json", tampered_sections)
        bad_section = nt.validate_work(work, case)
        check("Mismatched section evidence is rejected", any("segmentation_evidence.quote" in error for error in bad_section["errors"]))
        extracted_sections = nt.read_json(work / "sections.json", {"sections": sections})
        # Restore from the real adapter instead of retaining any tampered artifacts.
        pa.extract_with_host_parser(parser_path, pdf_path, work)

        original_notes, _ = nt.read_jsonl(work / "notes.jsonl")
        bad_notes = json.loads(json.dumps(original_notes))
        bad_note = next(note for note in bad_notes if note["note_id"] == result_note["note_id"])
        bad_note["anchors"][0]["quote"] = "Fabricated visual quote"
        bad_note["anchors"][0]["source"] = "rendered_page"
        nt.write_text_atomic(work / "notes.jsonl", "".join(json.dumps(note, ensure_ascii=False) + "\n" for note in bad_notes))
        bad_visual = nt.validate_work(work, case)
        check("Rendered claim without PNG and visual review is rejected", any("lacks valid rendered_page" in error for error in bad_visual["errors"]))
        nt.write_text_atomic(work / "notes.jsonl", "".join(json.dumps(note, ensure_ascii=False) + "\n" for note in original_notes))

        ocr_notes = json.loads(json.dumps(original_notes))
        ocr_note = next(note for note in ocr_notes if note["note_id"] == result_note["note_id"])
        ocr_note["anchors"][0]["quote"] = "Synthetic OCR recovered text"
        ocr_note["anchors"][0]["source"] = "ocr"
        nt.write_text_atomic(work / "notes.jsonl", "".join(json.dumps(note, ensure_ascii=False) + "\n" for note in ocr_notes))
        ocr_text = work / "ocr" / "page_0002.txt"
        nt.write_text_atomic(ocr_text, "Synthetic OCR recovered text\n")
        nt.write_json_atomic(work / "ocr_report.json", {"fixture_kind": "synthetic_adapter_test", "ocr_pages": [{"pdf_page": 2, "text": str(ocr_text), "source": "ocr"}]})
        ocr_valid = nt.validate_work(work, case)
        matching = [row for row in ocr_valid["machine_anchor_checks"] if row["id"].startswith(result_note["note_id"])]
        check("OCR quote uses OCR text provenance", matching and all(row["ocr_match"] for row in matching))
        nt.write_text_atomic(work / "notes.jsonl", "".join(json.dumps(note, ensure_ascii=False) + "\n" for note in original_notes))
        (work / "ocr_report.json").unlink(missing_ok=True)

        check("Scientific notation normalization", "0.005" in nt.numeric_values("5 · 10−3"))
        check("Chinese unit normalization", nt.numeric_values("880 万") == nt.numeric_values("8.8M"))
        check("Exponent does not create a standalone negative warning", "-6" not in nt.numeric_values("10^-6"))
        check("Unit suffix keeps the full integer (100GB)", nt.numeric_values("100GB memory") == {"100"})
        check("Unit suffix does not truncate 32GB", nt.numeric_values("32GB") == {"32"})
        check("Hardware model token is not a bare number (V100)", "100" not in nt.numeric_values("V100 GPUs"))
        check("Hyphenated qualifier stays a plain number (8-bit)", nt.numeric_values("8-bit quantization") == {"8"})
        check("Million suffix still scales (9M)", nt.numeric_values("9M passages") == {"9000000"})
        check("English numeral resolves a reported quantity", nt.word_numeral_matches("10", "we use ten retrieved documents"))
        check("English numeral resolves beam size", nt.word_numeral_matches("4", "beam size of four"))
        check("Capitalised English numeral resolves annotator count", nt.word_numeral_matches("2", "Two annotators label the data"))
        check("Derived difference is explained, not warned", "difference" in (nt.derived_numeric_hint("1.1", {"57.9", "56.8"}) or ""))
        check("Status semantics catches not_reported with reported evidence",
              bool(nt.status_semantics_findings("n1", "not_reported", "附录 E 承认未处理该子任务", None)))
        check("Status semantics accepts a bounded reported limitation",
              not nt.status_semantics_findings("n2", "reported", "作者未给出量化失效边界，但在 Broader Impact 报告了偏见风险", None))
        check("Status semantics rejects a one-sided conflict claim",
              bool(nt.status_semantics_findings("n3", "conflict", "两处数值不一致", [{"pdf_page": 1}])))

        request_rows = [{"stage": "section_notes", "request_index": index, "source": "ledger"} for index in range(65)]
        nt.write_text_atomic(work / "request_ledger.jsonl", "".join(json.dumps(row) + "\n" for row in request_rows))
        over_budget = nt.validate_work(work, case)
        check("65 logged requests break the 64-request budget", any("request budget exceeded" in error for error in over_budget["errors"]))
        check("Request summary reports the ledger source", nt.request_summary(work)["request_source"] == "ledger")
        (work / "request_ledger.jsonl").unlink()

        ingest_work = Path(temporary) / "ingest-work"
        ingest_work.mkdir()
        ingest_pdf = Path(temporary) / "ingest.pdf"
        ingest_pdf.touch()
        ingest_records = Path(temporary) / "platform-records.jsonl"
        nt.write_text_atomic(ingest_records, "".join(json.dumps(row) + "\n" for row in [
            {"page": 1, "kind": "heading", "text": "1 INTRODUCTION"},
            {"page": 1, "kind": "paragraph", "text": "Platform text supplied by a real tool."},
            {"page": 2, "kind": "heading", "text": "2 METHODS"},
            {"page": 2, "kind": "paragraph", "text": "A documented method."},
        ]))
        nt.command_ingest(argparse.Namespace(
            pdf=str(ingest_pdf), records=str(ingest_records), source="synthetic_platform_tool", work=str(ingest_work),
        ))
        ingest_sections = nt.read_json(ingest_work / "sections.json", {"sections": []})["sections"]
        check("Ingest builds sections from platform records", len(ingest_sections) == 3)
        check("Ingest reports its real source", nt.read_json(ingest_work / "extract_report.json").get("source") == "synthetic_platform_tool")

        case_path = work / "case.json"
        nt.write_json_atomic(case_path, case)
        nt.write_json_atomic(nt.state_path(work), {"started_at": nt.utc_now(), "status": "partial"})
        assembled_output = Path(temporary) / "assembled"
        nt.command_assemble(argparse.Namespace(
            case=str(case_path), work=str(work), output=str(assembled_output), lang="zh",
            request_success=True, run_status="auto", status_reason="",
        ))
        assembled = nt.read_json(assembled_output / "result.json", {})
        check("Assemble writes four-state run_status", assembled.get("run_status") == "success")
        check("Assemble keeps platform status compatible", assembled.get("status") == "success")

        nt.write_json_atomic(nt.state_path(work), {"started_at": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=901)).isoformat(), "status": "partial"})
        expired = nt.validate_work(work, case)
        check("Budget overrun forces failure", any("budget exceeded" in error for error in expired["errors"]))

    result = {"ok": not failures, "check_count": len(checks), "failures": failures, "fixture": "synthetic; not a scientific result or official case"}
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if not failures else 1)


if __name__ == "__main__":
    main()
