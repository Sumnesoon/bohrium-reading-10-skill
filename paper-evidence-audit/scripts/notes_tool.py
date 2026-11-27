#!/usr/bin/env python3
"""Deterministic helpers for section-aligned, evidence-linked research notes.

The agent reads papers and writes the semantic content. This tool only probes the
runtime, extracts actual PDF text, checks evidence and assembles honest outputs.
It never invents a paper result or a required answer.

Python 3.10+; PyMuPDF is optional and used when available. No network access.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.metadata
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

sys.dont_write_bytecode = True
from parser_adapter import (
    discover_host_parser, extract_with_host_parser, letter_heading_shape, accept_letter_chain,
    named_heading_title,
)
import render_backends

VERSION = "1.5.0"
VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor"
if VENDOR_DIR.is_dir() and str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))
LIGATURES = str.maketrans({
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
    "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st",
})
NUMBER_RE = re.compile(r"(?<![\w.^+\-])[-+]?\d+(?:,\d{3})*(?:\.\d+)?(?:[eE][-+]?\d+)?")
SCIENTIFIC_NUMBER_RE = re.compile(
    r"(?P<coeff>\d+(?:\.\d+)?)\s*(?:[·×xX]\s*)?10\s*(?:\^\s*)?(?P<exponent>[+\-−]?\d+)",
    re.IGNORECASE,
)
UNIT_NUMBER_RE = re.compile(
    r"(?<![\w.^+\-])(?P<number>[-+]?\d+(?:,\d{3})*(?:\.\d+)?)"
    r"(?:[ \t]*(?P<unit>万|亿|(?i:million|billion|thousand)(?![A-Za-z])|[Kk](?![A-Za-z])|[MB](?![A-Za-z])))?"
    r"[ \t]*(?P<tail>[A-Za-z%‰]*)",
)
REVIEW_LOCATION_PREFIXES = ("page", "pages", "p.", "pp.", "section", "figure", "table", "equation", "appendix", "页", "第", "表", "图", "式（", "式(")
SUSPECT_GLYPH_TOKENS = {
    "Tis": "This", "Te": "The", "Tese": "These", "Tose": "Those",
    "Troughput": "Throughput", "Qery": "Query", "Qality": "Quality",
    "Qantization": "Quantization", "Qantized": "Quantized", "Qestion": "Question", "Qerie": "Query",
}
AXIS_LABELS = {
    "document", "documents", "query", "queries", "score", "scores", "similarity", "rank",
    "relevance", "latency", "time", "mrr", "map", "flops", "throughput", "precision", "recall",
}
NONHEADING_KINDS = {"figure", "caption", "table", "equation", "header", "footer", "document_title"}
HEADING_PREFIX_PATTERN = r"(?:\d+(?:\.\d+){0,4}[.)]?|[IVXLCDM]{1,8}[.)]|[A-Z][.)])"
HEADING_NUMBER_RE = re.compile(r"^" + HEADING_PREFIX_PATTERN + r"\s+\S+")
HEADING_PREFIX_RE = re.compile(r"^" + HEADING_PREFIX_PATTERN + r"\s+")
KNOWN_HEADING_RE = re.compile(
    r"^(?:abstract|introduction|background|related work|methods?|methodology|"
    r"experiments?|results?|discussion|conclusions?|limitations?|references|"
    r"acknowledg(?:e)?ments?|appendix|appendices|supplementary materials?|"
    r"broader impact|ethical considerations|author contributions|funding|"
    r"data availability|conflict of interest)\s*$",
    re.IGNORECASE,
)
CN_KNOWN_HEADING_RE = re.compile(
    r"^(?:(?:第[一二三四五六七八九十百零〇0-9]+[章节篇部])|(?:[一二三四五六七八九十]+[、．.]))?\s*"
    r"(?:摘要|引言|绪论|背景|相关工作|方法|方法学|实验|结果|讨论|结论|局限性|参考文献|致谢|附录|补充材料)\s*[：:]?$"
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_json_atomic(path: Path, value: Any) -> None:
    write_text_atomic(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> tuple[list[Any], list[str]]:
    if not path.exists():
        return [], []
    rows, errors = [], []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}:{line_no}: {exc.msg}")
    return rows, errors


def normalize_text(text: str) -> str:
    """Published, deterministic normalization used by both notes and validator."""
    text = unicodedata.normalize("NFKC", text or "").translate(LIGATURES)
    text = text.replace("\u00ad", "")
    text = re.sub(r"(?<=[A-Za-z])[-\u2010\u2011]\s*\n\s*(?=[a-z])", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def layout_tolerant_quote_match(quote: str, page_text: str) -> bool:
    """Match a unique ordered quote split by two-column text interleaving."""
    stopwords = {"a", "an", "the", "of", "to", "in", "on", "and", "or", "from", "with"}
    needle = [token for token in re.findall(r"[^\W_]+", normalize_text(quote).casefold())
              if token not in stopwords]
    haystack = re.findall(r"[^\W_]+", normalize_text(page_text).casefold())
    if len(needle) < 3 or len(haystack) < len(needle):
        return False
    spans = []
    for start in (index for index, token in enumerate(haystack) if token == needle[0]):
        position, ok = start, True
        for token in needle[1:]:
            stop = min(len(haystack), position + 220)
            try:
                position = haystack.index(token, position + 1, stop)
            except ValueError:
                ok = False
                break
        if ok:
            spans.append(position - start + 1)
    if not spans:
        return False
    spans.sort()
    best = spans[0]
    # A page-level anchor may have more than one same-page occurrence.  It is
    # still locatable when at least one complete ordered chain is compact; a
    # block-level anchor remains stricter and is checked against its block.
    return best <= 40


def add_change(work: Path, event: str, detail: str, files: Iterable[str] = ()) -> None:
    append_jsonl(work / "changes.jsonl", {
        "timestamp": utc_now(), "event": event, "detail": detail, "files": list(files)
    })


def fail(message: str, code: int = 2) -> None:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(code)


def discover_case_contract(case: dict[str, Any]) -> Any:
    for key in ("output_contract", "outputContract", "output_schema", "outputSchema"):
        if key in case:
            return case[key]
    for key in ("task", "contract", "execution"):
        nested = case.get(key)
        if isinstance(nested, dict):
            found = discover_case_contract(nested)
            if found is not None:
                return found
    return None


def contract_properties(contract: Any) -> dict[str, Any]:
    if not isinstance(contract, dict):
        return {}
    if isinstance(contract.get("properties"), dict):
        properties = dict(contract["properties"])
        for required in contract.get("required", []):
            properties.setdefault(str(required), {})
        return properties
    if isinstance(contract.get("fields"), dict):
        return contract["fields"]
    if isinstance(contract.get("fields"), list):
        return {
            str(item.get("name", item.get("id"))) if isinstance(item, dict) else str(item): item if isinstance(item, dict) else {}
            for item in contract["fields"]
        }
    if isinstance(contract.get("required"), list) and not contract.get("properties"):
        return {str(item): {} for item in contract["required"]}
    metadata = {"title", "description", "$schema", "$id", "type", "version", "additionalProperties"}
    return {k: v for k, v in contract.items() if k not in metadata}


def placeholder(schema: Any) -> Any:
    if not isinstance(schema, dict):
        return None
    if "default" in schema:
        return schema["default"]
    if "const" in schema:
        return schema["const"]
    if isinstance(schema.get("enum"), list) and schema["enum"]:
        # Selecting an arbitrary enum value would fabricate a result.
        return None
    kind = schema.get("type")
    if kind == "object" or isinstance(schema.get("properties"), dict):
        return contract_skeleton(schema)
    if kind == "array":
        return []
    if kind == "boolean":
        return None
    return None


def contract_skeleton(contract: Any) -> dict[str, Any]:
    required_names = {str(item) for item in contract.get("required", [])} if isinstance(contract, dict) else set()
    return {
        key: placeholder(value)
        for key, value in contract_properties(contract).items()
        if key in required_names
    }


def deep_merge(base: Any, supplied: Any) -> Any:
    if isinstance(base, dict) and isinstance(supplied, dict):
        merged = dict(base)
        for key, value in supplied.items():
            merged[key] = deep_merge(base.get(key), value) if key in base else value
        return merged
    return supplied if supplied is not None else base


def state_path(work: Path) -> Path:
    return work / "run_state.json"


def update_state(work: Path, **updates: Any) -> dict[str, Any]:
    state = read_json(state_path(work), {})
    state.update(updates)
    state["updated_at"] = utc_now()
    write_json_atomic(state_path(work), state)
    return state


def fitz_module():
    module_name = "pymupdf" if importlib.util.find_spec("pymupdf") else "fitz" if importlib.util.find_spec("fitz") else None
    if module_name is None:
        return None, None
    try:
        module = __import__(module_name)
        return module, module_name
    except ImportError:
        return None, None


def fitz_version() -> str | None:
    try:
        return importlib.metadata.version("PyMuPDF")
    except importlib.metadata.PackageNotFoundError:
        return None


def pypdf2_module():
    """Return the bundled (or globally installed) PyPDF2 reader module."""
    try:
        module = __import__("PyPDF2")
        return module, getattr(module, "__version__", None)
    except Exception:
        return None, None


def elapsed_seconds(work: Path) -> float | None:
    """Wall-clock budget for one run.

    Once ``assemble`` finishes it freezes ``finished_at`` and ``elapsed_seconds`` in
    ``run_state.json``.  Later audits (re-running ``validate`` on a finished work
    directory) therefore keep reporting the original run duration instead of the
    idle time since then.  A run that has not been assembled yet is measured live.
    """

    state = read_json(state_path(work), {})
    frozen = state.get("elapsed_seconds")
    if isinstance(frozen, (int, float)) and not isinstance(frozen, bool):
        return max(0.0, float(frozen))
    started_at = state.get("started_at")
    if not started_at:
        return None
    end_at = state.get("finished_at")
    if not end_at:
        # Legacy runs written before ``finished_at`` existed: for an assembled
        # ``success`` run, updated_at still approximates the end of that run.
        end_at = state.get("updated_at") if state.get("status") == "success" else None
    try:
        started = dt.datetime.fromisoformat(started_at)
        finished = dt.datetime.fromisoformat(end_at) if end_at else dt.datetime.now(dt.timezone.utc)
        return max(0.0, (finished - started).total_seconds())
    except (TypeError, ValueError):
        return None


REQUEST_LIMIT = 64
DEADLINE_WARN_SECONDS = 700


def record_timing(work: Path, stage: str, seconds: float, **extra: Any) -> None:
    append_jsonl(work / "timings.jsonl", {
        "stage": stage, "seconds": round(float(seconds), 3), "timestamp": utc_now(), **extra,
    })


def stage_timings(work: Path) -> list[dict[str, Any]]:
    rows, _ = read_jsonl(work / "timings.jsonl")
    return [row for row in rows if isinstance(row, dict)]


def request_summary(work: Path) -> dict[str, Any]:
    rows, errors = read_jsonl(work / "request_ledger.jsonl")
    entries = [row for row in rows if isinstance(row, dict) and row.get("request_index") is not None]
    if not entries:
        return {
            "requests_used": None, "requests_remaining": None, "request_source": "unknown",
            "request_limit": REQUEST_LIMIT, "ledger_errors": errors,
            "detail": "No native usage ledger and no logged request batches; the count stays unknown instead of zero.",
        }
    source = "native" if all(row.get("source") == "native" for row in entries) else "ledger"
    return {
        "requests_used": len(entries), "requests_remaining": max(0, REQUEST_LIMIT - len(entries)),
        "request_source": source, "request_limit": REQUEST_LIMIT,
        "last_stage": entries[-1].get("stage"), "ledger_errors": errors,
    }


def command_init(args: argparse.Namespace) -> None:
    started = time.perf_counter()
    case_path = Path(args.case).resolve()
    input_dir = Path(args.input_dir).resolve()
    work = Path(args.work).resolve()
    output = Path(args.output).resolve()
    if not case_path.is_file():
        fail(f"case file not found: {case_path}")
    case = read_json(case_path)
    if not isinstance(case, dict):
        fail("case.json must contain a JSON object")
    work.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    case_id = case.get("case_id", case.get("id"))
    contract = discover_case_contract(case)
    tools = {
        "python": sys.version.split()[0],
        "pymupdf_available": fitz_module()[0] is not None,
        "pymupdf_import": "pymupdf" if importlib.util.find_spec("pymupdf") else "fitz" if importlib.util.find_spec("fitz") else None,
        "pymupdf_version": fitz_version(),
        "pypdf2_bundled": (VENDOR_DIR / "PyPDF2" / "__init__.py").is_file(),
        "pypdf2_version": pypdf2_module()[1],
        "host_parser_discovery": ".parser/<pdf-stem>.jsonl at input, ancestor, or working directory",
        "pdfplumber": importlib.util.find_spec("pdfplumber") is not None,
        **{cmd: shutil.which(cmd) for cmd in ("pdftotext", "pdftoppm", "tesseract", "pdf-text-tool", "ocr-tool", "pdf-render-tool")},
        "rapidocr": importlib.util.find_spec("rapidocr_onnxruntime") is not None
                    or importlib.util.find_spec("rapidocr") is not None,
        "ocr_engines": sorted(available_ocr_engines()),
        "ocr_optional": "OCR is optional: text PDFs use the text layer/Host parser, scanned pages can use render + rendered_page anchors with visual_review; no reviewer-side installation is required.",
    }
    discovered_pdfs = [str(path) for path in input_dir.rglob("*.pdf") if path.is_file()] if input_dir.exists() else []
    supplied_paths = [Path(value).resolve() for value in args.pdf]
    missing_pdfs = [str(path) for path in supplied_paths if not path.is_file()]
    if missing_pdfs:
        fail(f"explicit PDF files not found: {missing_pdfs}")
    supplied_pdfs = [str(path) for path in supplied_paths]
    pdfs = sorted(set(discovered_pdfs + supplied_pdfs))
    task_path = Path(args.task).resolve() if args.task else input_dir / "task.md"
    write_json_atomic(work / "environment.json", {
        "tool_version": VERSION, "detected_at": utc_now(), "tools": tools,
        "note": "Detection records local capabilities only; it is not a claim about an unseen runtime.",
    })
    write_json_atomic(work / "case_context.json", {
        "case_id": case_id, "case_path": str(case_path),
        "task_path": str(task_path) if task_path.is_file() else None,
        "pdf_files": pdfs, "output_contract": contract,
    })
    if not (work / "notes.jsonl").exists():
        write_text_atomic(work / "notes.jsonl", "")
    if not (work / "uncertainty.json").exists():
        write_json_atomic(work / "uncertainty.json", [])
    if not (work / "contract_values.json").exists():
        write_json_atomic(work / "contract_values.json", contract_skeleton(contract))
    if not (work / "paper_card.json").exists():
        write_json_atomic(work / "paper_card.json", {
            field: {"status": "unresolved", "content": None, "source_note_ids": []}
            for field in ("research_question", "data", "method", "results", "conclusion", "limitations")
        })
    if case_id is None:
        uncertainty = read_json(work / "uncertainty.json", [])
        uncertainty.append({"kind": "missing_case_id", "detail": "case.json has no case_id or id."})
        write_json_atomic(work / "uncertainty.json", uncertainty)
    state = {
        "version": VERSION, "case_id": case_id, "status": "partial",
        "started_at": utc_now(), "updated_at": utc_now(),
        "work": str(work), "output": str(output),
        "contract_present": contract is not None,
        "pdf_count": len(pdfs), "requested_success": False,
    }
    write_json_atomic(state_path(work), state)
    if not (work / "changes.jsonl").exists():
        add_change(work, "initialized", "Created an honest partial result before processing.")
    result = contract_skeleton(contract)
    result.update({
        "case_id": case_id, "status": "partial", "sources": [],
        "uncertainty": read_json(work / "uncertainty.json", []),
        "change_records": read_jsonl(work / "changes.jsonl")[0],
    })
    write_json_atomic(output / "result.json", result)
    record_timing(work, "init", time.perf_counter() - started, pdf_count=len(pdfs))
    print(json.dumps({"ok": True, "case_id": case_id, "pdfs": pdfs, "tools": tools}, ensure_ascii=False))


def block_text(block: dict[str, Any]) -> tuple[str, float, bool]:
    parts, sizes, bold_chars, total_chars = [], [], 0, 0
    for line in block.get("lines", []):
        line_text = "".join(span.get("text", "") for span in line.get("spans", []))
        if line_text:
            parts.append(line_text)
        for span in line.get("spans", []):
            text = span.get("text", "")
            size = float(span.get("size", 0) or 0)
            if size:
                sizes.append(size)
            count = len(text.strip())
            total_chars += count
            if int(span.get("flags", 0)) & 16:
                bold_chars += count
    return "\n".join(parts), (max(sizes) if sizes else 0.0), (bold_chars > total_chars / 2 if total_chars else False)


def merged_gaps(values: list[tuple[float, float]], low: float, high: float) -> list[tuple[float, float]]:
    intervals = sorted((max(low, a), min(high, b)) for a, b in values if b > low and a < high)
    if not intervals:
        return []
    merged = [list(intervals[0])]
    for a, b in intervals[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    gaps, cursor = [], low
    for a, b in merged:
        if a > cursor:
            gaps.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < high:
        gaps.append((cursor, high))
    return gaps


def xy_cut(blocks: list[dict[str, Any]], depth: int = 0) -> list[dict[str, Any]]:
    """Conservative recursive XY-cut; unsplittable blocks use geometric order."""
    if len(blocks) <= 1 or depth > 10:
        return sorted(blocks, key=lambda b: (round(b["bbox"][1], 1), b["bbox"][0]))
    x0, x1 = min(b["bbox"][0] for b in blocks), max(b["bbox"][2] for b in blocks)
    gaps = merged_gaps([(b["bbox"][0], b["bbox"][2]) for b in blocks], x0, x1)
    if gaps:
        a, b = max(gaps, key=lambda pair: pair[1] - pair[0])
        if b - a >= 10.0:
            cut = (a + b) / 2
            left = [item for item in blocks if (item["bbox"][0] + item["bbox"][2]) / 2 < cut]
            right = [item for item in blocks if item not in left]
            if left and right:
                return xy_cut(left, depth + 1) + xy_cut(right, depth + 1)
    y0, y1 = min(b["bbox"][1] for b in blocks), max(b["bbox"][3] for b in blocks)
    gaps = merged_gaps([(b["bbox"][1], b["bbox"][3]) for b in blocks], y0, y1)
    if gaps:
        a, b = max(gaps, key=lambda pair: pair[1] - pair[0])
        if b - a >= 7.0:
            cut = (a + b) / 2
            top = [item for item in blocks if (item["bbox"][1] + item["bbox"][3]) / 2 < cut]
            bottom = [item for item in blocks if item not in top]
            if top and bottom:
                return xy_cut(top, depth + 1) + xy_cut(bottom, depth + 1)
    return sorted(blocks, key=lambda item: (item["bbox"][1], item["bbox"][0]))


def combined_heading(text: str) -> tuple[str, bool]:
    """Return an exact heading fragment while preserving the original full block."""
    first_line = text.splitlines()[0].strip() if text.splitlines() else ""
    if not re.match(r"^\d+(?:\.\d+)+\s+", first_line):
        return "", False
    match = re.match(r"^(\d+(?:\.\d+)+\s+[^.。\n]{1,140}?[.。])\s+\S", first_line)
    if match:
        return match.group(1), True
    if len(text.splitlines()) > 1 and any(line.strip() for line in text.splitlines()[1:]):
        return first_line, True
    return "", False


def heading_strength(block: dict[str, Any], body_size: float) -> tuple[int, str] | None:
    raw_text = block["raw_text"]
    text = normalize_text(raw_text)
    if block.get("kind") in NONHEADING_KINDS:
        return None
    if not text or len(text) > 200 or text.endswith((".", "。", ";", "；")):
        # A merged heading may end with body punctuation; other long blocks do not qualify.
        combined, _ = combined_heading(raw_text)
        if not combined:
            return None
    combined, has_body = combined_heading(raw_text)
    if combined:
        text = normalize_text(combined)
    if not text or not re.search(r"[A-Za-z\u3400-\u9fff]", text):
        return None
    if text.rstrip(".。").lower() in AXIS_LABELS:
        return None
    if HEADING_NUMBER_RE.match(text):
        remainder = HEADING_PREFIX_RE.sub("", text, count=1).strip()
        tokens = remainder.split()
        numeric_tokens = sum(1 for token in tokens if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", token))
        if (
            (block.get("max_font_size", 0.0) > 0 and block["max_font_size"] < body_size * 0.82)
            or not re.search(r"[A-Za-z\u3400-\u9fff]", remainder)
            or remainder.startswith(("=", "+", "-", "−", "–", "—", "∥", "|", "≈", "≡", "∂", "∇", "∑", "∫", "√"))
            or len(re.findall(r"[A-Za-z\u3400-\u9fff]", remainder)) < 2
            or (tokens and numeric_tokens / len(tokens) > 0.35)
        ):
            return None
        return 4, "numbered_heading_with_body" if has_body else "numbered_heading"
    if (KNOWN_HEADING_RE.match(text) or CN_KNOWN_HEADING_RE.match(text)) and block.get("max_font_size", body_size) >= body_size * 0.90:
        return 3, "unnumbered_named_heading"
    # Large text on page 1 is usually the paper title or author block, not a section.
    if block["pdf_page"] == 1:
        return None
    if block.get("max_font_size", 0.0) >= body_size * 1.18:
        return 3, "font_size_heading"
    if (
        block["bold"] and len(text) <= 100 and len(text.split()) <= 14
        and block.get("max_font_size", 0.0) >= body_size * 1.04
    ):
        return 2, "bold_heading"
    return None


def extract_with_fitz(pdf_path: Path, work: Path) -> dict[str, Any]:
    fitz, module_name = fitz_module()
    if fitz is None:
        return {"ok": False, "error": "Neither pymupdf nor legacy fitz is importable."}

    pages_dir = work / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        return {"ok": False, "error": f"PDF open failed: {type(exc).__name__}: {exc}"}
    if doc.needs_pass:
        doc.close()
        return {"ok": False, "error": "PDF is encrypted and requires a password.", "status": "unavailable"}
    if doc.page_count <= 0:
        doc.close()
        return {"ok": False, "error": "PDF has no readable pages; it may be empty or truncated.", "status": "unavailable"}
    page_rows, all_blocks = [], []
    body_sizes: dict[float, int] = {}
    for page_index in range(doc.page_count):
        page = doc[page_index]
        page_dict = page.get_text("dict")
        raw_blocks = []
        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            text, max_size, bold = block_text(block)
            if not normalize_text(text):
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    key = round(float(span.get("size", 0) or 0), 1)
                    if key:
                        body_sizes[key] = body_sizes.get(key, 0) + len(span.get("text", "").strip())
            raw_blocks.append({
                "pdf_page": page_index + 1, "bbox": [round(float(x), 3) for x in block.get("bbox", (0, 0, 0, 0))],
                "raw_text": text, "max_font_size": max_size, "bold": bold,
            })
        # Page numbers can straddle the column gutter and must not block XY-cut.
        deferred = [
            item for item in raw_blocks
            if re.fullmatch(r"(?:page\s*)?\d{1,4}", normalize_text(item["raw_text"]), re.IGNORECASE)
            and (item["bbox"][3] <= page.rect.height * 0.10 or item["bbox"][1] >= page.rect.height * 0.90)
        ]
        layout_blocks = [item for item in raw_blocks if item not in deferred]
        ordered = xy_cut(layout_blocks) + deferred
        for order, item in enumerate(ordered):
            item.update({
                "block_id": f"p{page_index + 1:04d}_b{order:03d}",
                "reading_order": order, "normalized_text": normalize_text(item["raw_text"]),
                "role": "candidate_body",
            })
        all_blocks.extend(ordered)
        image_area = 0.0
        try:
            page_area = max(float(page.rect.width * page.rect.height), 1.0)
            image_area = sum(max(0.0, (im["bbox"][2] - im["bbox"][0]) * (im["bbox"][3] - im["bbox"][1])) for im in page.get_image_info()) / page_area
        except Exception:
            pass
        page_rows.append({
            "pdf_page": page_index + 1, "width": float(page.rect.width), "height": float(page.rect.height),
            "printed_page": None, "raw_char_count": len("".join(item["raw_text"] for item in ordered)),
            "image_area_ratio_estimate": round(image_area, 4), "block_ids": [b["block_id"] for b in ordered],
        })
    body_size = max(body_sizes.items(), key=lambda item: item[1])[0] if body_sizes else 10.0
    # Repeated running heads/footers are marked, never silently deleted.
    occurrences: dict[str, set[int]] = {}
    for item in all_blocks:
        row = page_rows[item["pdf_page"] - 1]
        y0, y1 = item["bbox"][1], item["bbox"][3]
        if y1 <= row["height"] * 0.10 or y0 >= row["height"] * 0.90:
            key = re.sub(r"\d+", "#", normalize_text(item["raw_text"])).lower()
            occurrences.setdefault(key, set()).add(item["pdf_page"])
    repeated = {key for key, pages in occurrences.items() if len(pages) >= max(2, round(doc.page_count * 0.3))}
    for item in all_blocks:
        row = page_rows[item["pdf_page"] - 1]
        y0, y1 = item["bbox"][1], item["bbox"][3]
        key = re.sub(r"\d+", "#", normalize_text(item["raw_text"])).lower()
        if (y1 <= row["height"] * 0.10 or y0 >= row["height"] * 0.90) and key in repeated:
            item["role"] = "running_header_or_footer"
    candidates = []
    for item in all_blocks:
        finding = heading_strength(item, body_size)
        if finding and item["role"] != "running_header_or_footer":
            candidates.append((item, finding))
    # Appendix letters: accepted only as a consecutive A, B, C ... chain.
    candidate_ids = {entry[0]["block_id"] for entry in candidates}
    letter_items: list[tuple[str, str]] = []
    letter_payload: dict[str, tuple[dict[str, Any], tuple[str, str, str]]] = {}
    for item in all_blocks:
        if item["role"] == "running_header_or_footer" or item["block_id"] in candidate_ids:
            continue
        shape = letter_heading_shape(item["raw_text"])
        if shape is not None:
            letter_items.append((shape[0], item["block_id"]))
            letter_payload[item["block_id"]] = (item, shape)
    references_before = any(
        normalize_text(entry[0]["raw_text"]).casefold().startswith(("references", "bibliography", "参考文献"))
        for entry in candidates
    )
    accepted_letters = accept_letter_chain(letter_items, references_before=references_before)
    if accepted_letters:
        indexes = {item["block_id"]: index for index, item in enumerate(all_blocks)}
        for block_id in accepted_letters:
            item, _shape = letter_payload[block_id]
            candidates.append((item, (1, "letter_appendix_heading")))
        candidates.sort(key=lambda entry: indexes[entry[0]["block_id"]])
    sections = []
    if candidates:
        for index, (item, finding) in enumerate(candidates):
            level = 1
            match = re.match(r"^(\d+(?:\.\d+)*)", normalize_text(item["raw_text"]))
            if match:
                level = match.group(1).count(".") + 1
            fragment, is_combined = combined_heading(item["raw_text"])
            sections.append({
                "section_id": f"sec_{index + 1:04d}", "title": normalize_text(fragment) if is_combined else normalize_text(item["raw_text"]),
                "level": level, "pdf_page_start": item["pdf_page"],
                "pdf_page_end": candidates[index + 1][0]["pdf_page"] if index + 1 < len(candidates) else doc.page_count,
                "heading_block_id": item["block_id"],
                "segmentation_evidence": {
                    "source": finding[1], "font_size": item["max_font_size"], "body_font_size": body_size,
                    "quote": fragment if is_combined else item["raw_text"], "bbox": item["bbox"],
                    "combined_heading_body": is_combined, "needs_semantic_review": True,
                },
            })
        if not sections or sections[0]["pdf_page_start"] > 1 or all_blocks and sections[0]["heading_block_id"] != all_blocks[0]["block_id"]:
            sections.insert(0, {
                "section_id": "sec_0000", "title": "Front matter (before first detected heading)", "level": 0,
                "pdf_page_start": 1, "pdf_page_end": sections[0]["pdf_page_start"] if sections else doc.page_count,
                "heading_block_id": None,
                "segmentation_evidence": {"source": "implicit_front_matter", "needs_semantic_review": True},
            })
    section_by_heading = {s["heading_block_id"]: s["section_id"] for s in sections if s.get("heading_block_id")}
    combined_heading_ids = {s["heading_block_id"] for s in sections if s.get("segmentation_evidence", {}).get("combined_heading_body")}
    current_section = sections[0]["section_id"] if sections and sections[0]["section_id"] == "sec_0000" else "sec_unassigned"
    for item in all_blocks:
        if item["block_id"] in section_by_heading:
            current_section = section_by_heading[item["block_id"]]
            item["role"] = "heading_and_body" if item["block_id"] in combined_heading_ids else "heading"
        item["section_id"] = current_section
    # End a section on the last page actually assigned to it, not automatically on
    # the next heading's first page (which would include the following chapter).
    for section in sections:
        assigned_pages = [b["pdf_page"] for b in all_blocks if b["section_id"] == section["section_id"]]
        if assigned_pages:
            section["pdf_page_start"] = min(assigned_pages) if section["section_id"] == "sec_0000" else section["pdf_page_start"]
            section["pdf_page_end"] = max(assigned_pages)
    for row in page_rows:
        texts = [b["raw_text"] for b in all_blocks if b["pdf_page"] == row["pdf_page"] and b["role"] != "running_header_or_footer"]
        text_path = pages_dir / f"page_{row['pdf_page']:04d}.txt"
        write_text_atomic(text_path, "\n\n".join(texts) + ("\n" if texts else ""))
        row["text_path"] = str(text_path.relative_to(work)).replace("\\", "/")
        row["raw_text"] = "\n".join(texts)
        row["normalized_text"] = normalize_text("\n".join(texts))
        row["needs_ocr_review"] = row["raw_char_count"] < 20 and row["image_area_ratio_estimate"] > 0.3
    write_text_atomic(work / "pages.jsonl", "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in page_rows))
    write_text_atomic(work / "blocks.jsonl", "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in all_blocks))
    write_json_atomic(work / "sections.json", {
        "source_pdf": str(pdf_path), "method": "PyMuPDF blocks, conservative XY-cut and reviewed heading candidates",
        "body_font_size_estimate": body_size, "sections": sections,
        "warnings": [] if sections else ["No reliable heading candidates found; manual section construction is required."],
    })
    doc.close()
    return {
        "ok": True, "backend": "pymupdf", "import_name": module_name, "version": fitz_version(), "page_count": len(page_rows),
        "block_count": len(all_blocks), "section_count": len(sections),
        "pages_needing_ocr_review": [r["pdf_page"] for r in page_rows if r["needs_ocr_review"]],
    }


def text_integrity_findings(page_rows: list[dict[str, Any]]) -> dict[str, Any]:
    flagged_pages: list[int] = []
    for row in page_rows:
        suspects = {
            token: len(re.findall(rf"(?<![A-Za-z]){re.escape(token)}(?![A-Za-z])", row.get("raw_text", "")))
            for token in SUSPECT_GLYPH_TOKENS
        }
        suspects = {token: count for token, count in suspects.items() if count}
        row["suspect_glyph_tokens"] = suspects
        row["text_layer_integrity_review"] = bool(suspects)
        if suspects:
            flagged_pages.append(row["pdf_page"])
    return {"pages_needing_text_integrity_review": flagged_pages}


def augment_extraction_quality(report: dict[str, Any], work: Path) -> None:
    if not report.get("ok"):
        return
    pages, errors = read_jsonl(work / "pages.jsonl")
    if errors:
        report.setdefault("warnings", []).extend(errors)
        return
    report.update(text_integrity_findings(pages))
    write_text_atomic(work / "pages.jsonl", "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in pages))
    integrity_pages = report.get("pages_needing_text_integrity_review", [])
    if integrity_pages:
        report.setdefault("warnings", []).append(
            "Suspected glyph mapping omissions were detected. Raw text was not rewritten; render and compare these pages before trusting affected words."
        )


def _pypdf2_page_lines(page: Any) -> list[dict[str, Any]]:
    """Reconstruct lines with font size and position from PyPDF2's visitor callback.

    PyPDF2 is a pure-Python fallback, not the preferred layout engine.  The line
    geometry here is deliberately conservative: it keeps heading candidates and
    merges body lines into paragraph-like blocks, while marking every section as
    needing semantic review.
    """

    fragments: list[dict[str, Any]] = []

    def visitor(text: str, cm: Any, tm: Any, font_dict: Any, font_size: Any) -> None:
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
        if not cleaned:
            return
        try:
            x = float(tm[4])
            y = float(tm[5])
            size = abs(float(font_size or 0))
        except (TypeError, ValueError, IndexError):
            return
        fragments.append({
            "x": x, "y": y, "size": size,
            "font": str((font_dict or {}).get("/BaseFont", "")),
            "text": cleaned,
        })

    try:
        page.extract_text(visitor_text=visitor)
    except Exception:
        fragments = []
    if not fragments:
        raw = ""
        try:
            raw = page.extract_text() or ""
        except Exception:
            raw = ""
        return [
            {"y": 0.0, "size": 10.0, "font": "", "text": line.strip()}
            for line in raw.splitlines() if line.strip()
        ]

    fragments.sort(key=lambda item: (-item["y"], item["x"]))
    lines: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for fragment in fragments:
        if current and abs(fragment["y"] - current[-1]["y"]) > 2.0:
            lines.append(current)
            current = []
        current.append(fragment)
    if current:
        lines.append(current)

    rows: list[dict[str, Any]] = []
    for line in lines:
        line.sort(key=lambda item: item["x"])
        text = " ".join(item["text"] for item in line)
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"\s+([,.;:!?%)\]}])", r"\1", text)
        text = re.sub(r"\(\s+", "(", text)
        rows.append({
            "y": line[0]["y"], "size": max(item["size"] for item in line),
            "font": " ".join(item["font"] for item in line), "text": text,
        })
    return rows


def _pypdf2_page_blocks(page: Any) -> list[dict[str, str]]:
    rows = _pypdf2_page_lines(page)
    if not rows:
        return []

    weights: dict[float, int] = {}
    for row in rows:
        key = round(float(row["size"]), 1)
        weights[key] = weights.get(key, 0) + len(row["text"])
    body_size = max(weights.items(), key=lambda item: item[1])[0] if weights else 10.0
    try:
        page_height = float(page.mediabox.height)
    except Exception:
        page_height = 0.0

    cleaned_rows: list[dict[str, Any]] = []
    for row in rows:
        text = row["text"]
        if not text:
            continue
        if not re.search(r"[A-Za-z\u3400-\u9fff]", text):
            continue
        if re.fullmatch(r"(?:page\s*)?\d{1,4}", text, re.IGNORECASE):
            continue
        if row["size"] < body_size * 0.65 and len(text) < 30:
            continue
        if "arxiv:" in text.lower() and row["size"] > body_size * 1.4:
            continue
        # Page numbers are often merged into the right edge of a text line.
        if page_height and (row["y"] < page_height * 0.10 or row["y"] > page_height * 0.90):
            text = re.sub(r"\s+(?:Page\s*)?\d{1,4}$", "", text, flags=re.IGNORECASE).strip()
        if not text:
            continue
        row["text"] = text
        cleaned_rows.append(row)

    def is_heading(row: dict[str, Any]) -> bool:
        text = row["text"].strip()
        if len(text) > 180 or re.search(r"[\x00-\x08\x0b-\x1f]", text):
            return False
        if KNOWN_HEADING_RE.fullmatch(text) or CN_KNOWN_HEADING_RE.fullmatch(text):
            return True
        # Numeric axes and table rows often start with a number followed by text.
        # Use typography in addition to the title shape on this lossy backend.
        if not re.search(r"bold|demi|cmbx|lm(?:roman|sans)\S*-?b(?:old)?\b", row["font"], re.I):
            return False
        numbered = re.match(r"^([1-9]\d*(?:\.[1-9]\d*)*)\.?\s+(.+)$", text)
        if numbered:
            number, rest = numbered.groups()
            if not rest[:1].isupper():
                return False
            if float(number.split(".")[0]) > 30:
                return False
            if len(rest.split()) < 2 and not (
                KNOWN_HEADING_RE.fullmatch(rest)
                or re.fullmatch(r"[A-Z][a-z]{4,}", rest)
            ):
                return False
            if rest.rstrip().endswith(("-", "–", "—")):
                return False
            return True
        if re.fullmatch(r"[A-Z]\s+[A-Z][A-Za-z0-9 ,:;()'\-]{2,90}", text):
            tokens = text.split()
            if len(tokens) >= 2 and not all(len(token) <= 1 for token in tokens):
                return True
        # Deliberately do not promote font size/bold alone to a heading here:
        # PyPDF2 cannot separate figure labels from section titles reliably.
        return False

    def heading_text(text: str) -> str:
        # A PyPDF2 line can merge a heading with the first body sentence or a
        # figure label because the fallback has no XY-cut.  Keep the heading
        # prefix only when a clear sentence boundary follows a plausible title.
        if len(text.split()) > 9:
            for marker in (
                " Section ", " Figure ", " Table ", " The ", " This ", " These ",
                " We ", " In ", " For ", " Our ", " RAG-Seq ", " 50.0 ",
            ):
                index = text.find(marker)
                if index > 0 and len(text[:index].split()) >= 3:
                    text = text[:index].strip()
                    break
        text = re.sub(r"\s+\d{1,4}$", "", text).strip()
        return text

    blocks: list[dict[str, str]] = []
    buffer: list[str] = []
    previous_y: float | None = None
    for row in cleaned_rows:
        if is_heading(row):
            if buffer:
                blocks.append({"kind": "paragraph", "text": " ".join(buffer)})
                buffer = []
            text = heading_text(row["text"])
            if text:
                blocks.append({"kind": "heading", "text": text})
            previous_y = row["y"]
            continue
        if buffer and previous_y is not None and (previous_y - row["y"]) > body_size * 1.65:
            blocks.append({"kind": "paragraph", "text": " ".join(buffer)})
            buffer = []
        buffer.append(row["text"])
        previous_y = row["y"]
    if buffer:
        blocks.append({"kind": "paragraph", "text": " ".join(buffer)})
    return blocks


def extract_with_pypdf2(pdf_path: Path, work: Path) -> dict[str, Any]:
    module, version = pypdf2_module()
    if module is None:
        return {"ok": False, "error": "PyPDF2 is not importable (bundled vendor/PyPDF2 missing)."}
    try:
        reader = module.PdfReader(str(pdf_path))
    except Exception as exc:
        return {"ok": False, "error": f"PyPDF2 open failed: {type(exc).__name__}: {exc}"}
    try:
        if reader.is_encrypted and not reader.decrypt(""):
            return {"ok": False, "error": "PDF is encrypted and PyPDF2 cannot decrypt it without a password."}
        page_count = len(reader.pages)
    except Exception as exc:
        return {"ok": False, "error": f"PyPDF2 page access failed: {type(exc).__name__}: {exc}"}
    if page_count <= 0:
        return {"ok": False, "error": "PDF has no readable pages; it may be empty or truncated.", "status": "unavailable"}

    records: list[dict[str, Any]] = []
    page_dimensions: dict[int, tuple[float, float]] = {}
    page_errors: list[dict[str, Any]] = []
    for page_number, page in enumerate(reader.pages, 1):
        try:
            page_dimensions[page_number] = (float(page.mediabox.width), float(page.mediabox.height))
        except Exception:
            page_dimensions[page_number] = (0.0, 0.0)
        try:
            blocks = _pypdf2_page_blocks(page)
        except Exception as exc:
            blocks = []
            page_errors.append({"page": page_number, "error": f"{type(exc).__name__}: {exc}"})
        if not blocks:
            records.append({"page": page_number, "kind": "text", "text": ""})
        else:
            records.extend({
                "page": page_number, "kind": block["kind"], "text": block["text"],
            } for block in blocks)

    records_path = work / "pypdf2_records.jsonl"
    write_text_atomic(
        records_path,
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
    )
    report = extract_with_host_parser(records_path, pdf_path, work)
    pages, _ = read_jsonl(work / "pages.jsonl")
    for page in pages:
        width, height = page_dimensions.get(int(page.get("pdf_page", 0)), (0.0, 0.0))
        page["width"], page["height"] = width, height
    write_text_atomic(work / "pages.jsonl", "".join(json.dumps(page, ensure_ascii=False) + "\n" for page in pages))
    report.update({
        "backend": "pypdf2",
        "import_name": "PyPDF2",
        "version": version,
        "page_count": page_count,
        "page_errors": page_errors,
        "records": str(records_path),
    })
    report.setdefault("warnings", []).append(
        "PyPDF2 is a pure-Python fallback: it has no reliable column/XY-cut or font-weight model; "
        "heading boundaries are heuristic and must be reviewed."
    )
    return report


def extract_with_pdftotext(pdf_path: Path, work: Path) -> dict[str, Any]:
    executable = shutil.which("pdftotext")
    if not executable:
        return {"ok": False, "error": "pdftotext is unavailable."}
    try:
        completed = subprocess.run(
            [executable, "-raw", str(pdf_path), "-"], capture_output=True, timeout=120, check=False
        )
    except Exception as exc:
        return {"ok": False, "error": f"pdftotext failed: {type(exc).__name__}: {exc}"}
    if completed.returncode != 0:
        error_text = completed.stderr.decode("utf-8", errors="replace").strip()
        return {"ok": False, "error": f"pdftotext exited {completed.returncode}: {error_text}", "status": "unavailable"}
    text = completed.stdout.decode("utf-8", errors="replace")
    page_texts = text.split("\f")
    if page_texts and not page_texts[-1].strip():
        page_texts.pop()
    records: list[dict[str, Any]] = []
    for page_number, page_text in enumerate(page_texts, 1):
        block_number = 0
        buffer: list[str] = []

        def emit(text: str) -> None:
            nonlocal block_number
            if text.strip():
                records.append({
                    "page": page_number, "kind": "text", "text": text.strip("\r\n"),
                    "resourceId": f"pdftotext-p{page_number:04d}-b{block_number:04d}", "figurePaths": [],
                })
                block_number += 1

        lines = page_text.splitlines()
        index = 0
        while index < len(lines):
            stripped = lines[index].strip()
            single = bool(
                stripped and len(stripped) <= 160 and not stripped.endswith((".", "。", ";", "；"))
                and (HEADING_NUMBER_RE.match(stripped) or KNOWN_HEADING_RE.fullmatch(stripped) or CN_KNOWN_HEADING_RE.fullmatch(stripped))
                and not stripped.lower().startswith(("http://", "https://", "www."))
            )
            paired = False
            if re.fullmatch(r"[1-9]\d{0,2}", stripped) and index + 1 < len(lines):
                next_line = lines[index + 1].strip()
                paired = bool(
                    next_line and len(next_line) <= 160 and not next_line.endswith((".", "。", ";", "；"))
                    and re.search(r"[A-Za-z\u3400-\u9fff]", next_line)
                    and not next_line.lower().startswith(("http://", "https://", "www."))
                )
            if single or paired:
                emit("\n".join(buffer))
                buffer = []
                emit(lines[index] + (("\n" + lines[index + 1]) if paired else ""))
                index += 2 if paired else 1
                continue
            if not stripped:
                emit("\n".join(buffer))
                buffer = []
            else:
                buffer.append(lines[index])
            index += 1
        emit("\n".join(buffer))
    if not records:
        return {"ok": False, "error": "pdftotext produced no nonempty text blocks; OCR or Host parsing is required.", "status": "unavailable"}
    adapter_path = work / "pdftotext_records.jsonl"
    write_text_atomic(adapter_path, "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records))
    report = extract_with_host_parser(adapter_path, pdf_path, work)
    report.update({"backend": "pdftotext", "command": [executable, "-raw", str(pdf_path), "-"], "adapter_records": str(adapter_path)})
    report.setdefault("warnings", []).append("pdftotext -raw has no coordinates or font evidence and may still merge headings with body; inspect section coverage and reading order explicitly.")
    sections_doc = read_json(work / "sections.json", {})
    sections_doc["method"] = "pdftotext -raw records passed through the conservative shared section adapter"
    write_json_atomic(work / "sections.json", sections_doc)
    return report


def prime_extraction_checkpoint(work: Path) -> bool:
    """Save one real, exact preliminary evidence note immediately after extraction.

    This is deliberately semantic-light: it preserves a useful paper passage and
    source location without pretending the task has already been answered.
    """
    notes, _ = read_jsonl(work / "notes.jsonl")
    if notes:
        return False
    pages, _ = read_jsonl(work / "pages.jsonl")
    sections_doc = read_json(work / "sections.json", {"sections": []})
    sections = sections_doc.get("sections", [])
    if not pages or not sections:
        return False
    page_by_id = {row.get("pdf_page"): row for row in pages if isinstance(row, dict)}
    preferred = [row for row in sections if normalize_text(row.get("title", "")).casefold() == "abstract"]
    preferred += [row for row in sections if row not in preferred and row.get("section_id") != "sec_0000"]
    preferred += [row for row in sections if row not in preferred]
    chosen = None
    for section in preferred:
        start, end = section.get("pdf_page_start"), section.get("pdf_page_end")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        for page_number in range(start, min(end, start + 2) + 1):
            raw = str(page_by_id.get(page_number, {}).get("raw_text", ""))
            candidates = re.split(r"(?<=[.!?。！？])\s+|\n+", raw)
            metadata = re.compile(
                r"(?:https?\s*:|doi\s*\.|copyright|©|\bc\s+eso\b|"
                r"\ba\s*&\s*a\b|astronomy\s*&\s*astrophysics)", re.IGNORECASE)
            candidates = [item.strip() for item in candidates
                          if 80 <= len(item.strip()) <= 700
                          and not item.strip().lower().startswith("table ")
                          and not metadata.search(item)]
            if candidates:
                cues = re.compile(
                    r"\b(?:we|this (?:paper|study|work)|results?|find|show|"
                    r"measure|constrain|estimate|demonstrate|present)\b", re.IGNORECASE)
                candidates.sort(key=lambda item: (bool(cues.search(item)), len(item)), reverse=True)
                chosen = (section, page_number, candidates[0])
                break
        if chosen:
            break
    if not chosen:
        return False
    section, page_number, quote = chosen
    note = {
        "note_id": "note_0001", "section_id": section["section_id"],
        "field": "preliminary_evidence", "status": "uncertain",
        "content": "自动抽取的首条可回链论文证据（尚未完成任务语义分类）：" + quote,
        "anchors": [{"pdf_page": page_number, "quote": quote, "source": "text_layer"}],
    }
    write_text_atomic(work / "notes.jsonl", json.dumps(note, ensure_ascii=False) + "\n")
    add_change(work, "primed_evidence_checkpoint",
               "Saved one exact preliminary paper passage immediately after extraction.",
               ["notes.jsonl"])
    return True


def publish_primed_checkpoint(work: Path) -> None:
    if not prime_extraction_checkpoint(work):
        return
    context = read_json(work / "case_context.json", {})
    state = read_json(state_path(work), {})
    case_path = Path(str(context.get("case_path", "")))
    output = Path(str(state.get("output", "")))
    if not case_path.is_file() or not str(output):
        return
    try:
        command_assemble(argparse.Namespace(
            case=str(case_path), work=str(work), output=str(output),
            run_status="partial", request_success=False,
            status_reason="PDF extraction complete; one exact preliminary evidence note was saved for early recovery.",
            lang="zh",
        ))
    except SystemExit as exc:
        if exc.code != 2:
            raise
    live = read_json(state_path(work), {})
    live.pop("finished_at", None)
    live.pop("elapsed_seconds", None)
    live["status"] = "partial"
    write_json_atomic(state_path(work), live)


def command_extract(args: argparse.Namespace) -> None:
    work = Path(args.work).resolve()
    pdf_path = Path(args.pdf).resolve()
    if not pdf_path.is_file():
        fail(f"PDF not found: {pdf_path}")
    started = time.perf_counter()
    requested = args.backend
    parser_path = discover_host_parser(pdf_path, args.parser_jsonl)
    request_signature = {
        "pdf": str(pdf_path),
        "pdf_size": pdf_path.stat().st_size,
        "pdf_mtime_ns": pdf_path.stat().st_mtime_ns,
        "backend": requested,
        "parser": str(parser_path) if parser_path and requested in {"auto", "host"} else None,
        "skill_version": VERSION,
    }
    if request_signature["parser"] and parser_path.is_file():
        request_signature["parser_mtime_ns"] = parser_path.stat().st_mtime_ns
    if not args.force:
        cached = [work / name for name in ("pages.jsonl", "blocks.jsonl", "sections.json")]
        if all(path.is_file() for path in cached):
            newest_cache = min(path.stat().st_mtime for path in cached)
            report = read_json(work / "extract_report.json", {}) or {}
            if (newest_cache >= pdf_path.stat().st_mtime and report.get("ok")
                    and report.get("request_signature") == request_signature):
                report.update({"cached": True, "pdf": str(pdf_path),
                               "note": "Reused the existing pages/blocks/sections because they are newer than the PDF; pass --force to re-extract."})
                write_json_atomic(work / "extract_report.json", report)
                add_change(work, "extract_cache_hit", "Reused existing extraction artifacts.", ["extract_report.json"])
                record_timing(work, "extract", time.perf_counter() - started, cached=True)
                print(json.dumps(report, ensure_ascii=False))
                return
    attempts: list[dict[str, Any]] = []
    backends = [requested] if requested != "auto" else ["host", "pymupdf", "pypdf2", "pdftotext"]
    report: dict[str, Any] = {"ok": False, "error": "No PDF text backend was attempted."}
    for backend in backends:
        if backend == "host":
            if parser_path is None:
                attempts.append({"backend": "host", "ok": False, "error": "No .parser/<pdf-stem>.jsonl was discovered."})
                continue
            try:
                report = extract_with_host_parser(parser_path, pdf_path, work)
            except Exception as exc:
                report = {"ok": False, "error": f"Host parser failed: {type(exc).__name__}: {exc}"}
        elif backend == "pymupdf":
            report = extract_with_fitz(pdf_path, work)
        elif backend == "pypdf2":
            report = extract_with_pypdf2(pdf_path, work)
        elif backend == "pdftotext":
            report = extract_with_pdftotext(pdf_path, work)
        else:
            fail(f"unknown backend: {backend}")
        attempts.append({"backend": backend, "ok": report.get("ok"), "error": report.get("error")})
        if report.get("ok"):
            break
        if requested != "auto":
            break
    report["backend_attempts"] = attempts
    report["pdf"] = str(pdf_path)
    report["request_signature"] = request_signature
    report["timestamp"] = utc_now()
    augment_extraction_quality(report, work)
    write_json_atomic(work / "extract_report.json", report)
    add_change(work, "extracted_pdf" if report.get("ok") else "extraction_failed", json.dumps(report, ensure_ascii=False), ["extract_report.json"])
    record_timing(work, "extract", time.perf_counter() - started,
                  backend=report.get("backend"), ok=bool(report.get("ok")))
    if report.get("ok"):
        publish_primed_checkpoint(work)
    print(json.dumps(report, ensure_ascii=False))
    if not report.get("ok"):
        raise SystemExit(2)


def command_ingest(args: argparse.Namespace) -> None:
    """Ingest already-extracted text/OCR records from a platform tool or Host.

    The record schema is the same conservative interface used by the Host adapter:
    ``{"page": positive int, "kind": "text", "text": str}``.  This is the bridge for
    platform PDF/OCR tools with unknown CLIs: the agent writes their real output
    into this schema, then this command builds pages/blocks/sections without
    pretending that a missing local parser succeeded.
    """

    started = time.perf_counter()
    work = Path(args.work).resolve()
    pdf_path = Path(args.pdf).resolve()
    records_path = Path(args.records).resolve()
    if not pdf_path.is_file():
        fail(f"PDF not found: {pdf_path}")
    if not records_path.is_file():
        fail(f"records file not found: {records_path}")
    records, errors = read_jsonl(records_path)
    if errors:
        fail("invalid records JSONL: " + "; ".join(errors[:3]))
    normalized: list[dict[str, Any]] = []
    for line_number, record in enumerate(records, 1):
        if not isinstance(record, dict):
            fail(f"records line {line_number} must be a JSON object")
        page = record.get("page", record.get("page_number"))
        text = record.get("text")
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            fail(f"records line {line_number}: page must be a positive integer")
        if not isinstance(text, str):
            fail(f"records line {line_number}: text must be a string")
        kind = record.get("kind", "text")
        if not isinstance(kind, str) or not kind:
            fail(f"records line {line_number}: kind must be a nonempty string")
        if kind.lower() == "ocr":
            kind = "text"
        figure_paths = record.get("figurePaths", [])
        if not isinstance(figure_paths, list) or not all(isinstance(item, str) for item in figure_paths):
            fail(f"records line {line_number}: figurePaths must be strings")
        normalized.append({
            "page": page, "kind": kind, "text": text,
            "resourceId": record.get("resourceId"), "figurePaths": list(figure_paths),
        })
    if not normalized:
        fail("records file contains no records")
    work.mkdir(parents=True, exist_ok=True)
    ingest_path = work / "ingest_records.jsonl"
    write_text_atomic(
        ingest_path,
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in normalized),
    )
    report = extract_with_host_parser(ingest_path, pdf_path, work)
    report.update({
        "backend": "ingest",
        "source": args.source,
        "records": str(ingest_path),
        "input_records": str(records_path),
    })
    write_json_atomic(work / "extract_report.json", report)
    add_change(work, "ingested_external_text", f"Ingested {len(normalized)} records from {args.source}.", ["ingest_records.jsonl", "extract_report.json"])
    record_timing(work, "ingest", time.perf_counter() - started, source=args.source, ok=bool(report.get("ok")))
    print(json.dumps(report, ensure_ascii=False))
    if not report.get("ok"):
        raise SystemExit(2)


def empty_content(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return not value
    return False


def contract_errors(value: Any, schema: Any, path: str) -> list[str]:
    errors: list[str] = []
    if empty_content(value):
        return [f"output_contract empty: {path}"]
    if not isinstance(schema, dict):
        return errors
    if "const" in schema and value != schema["const"]:
        errors.append(f"output_contract const mismatch: {path}")
    if isinstance(schema.get("enum"), list) and value not in schema["enum"]:
        errors.append(f"output_contract enum mismatch: {path}")
    kind = schema.get("type")
    type_checks = {
        "string": lambda item: isinstance(item, str),
        "array": lambda item: isinstance(item, list),
        "object": lambda item: isinstance(item, dict),
        "boolean": lambda item: isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
    }
    expected_kinds = kind if isinstance(kind, list) else [kind]
    known_checks = [type_checks[item] for item in expected_kinds
                    if isinstance(item, str) and item in type_checks]
    if known_checks and not any(check(value) for check in known_checks):
        errors.append(f"output_contract type mismatch: {path}; expected {kind}, got {type(value).__name__}")
        return errors
    if isinstance(value, list):
        if len(value) < int(schema.get("minItems", 0)):
            errors.append(f"output_contract too few items: {path}")
        for index, item in enumerate(value):
            errors.extend(contract_errors(item, schema.get("items", {}), f"{path}[{index}]"))
    if isinstance(value, dict):
        properties = contract_properties(schema)
        required_names = {str(item) for item in schema.get("required", [])}
        for name in sorted(required_names):
            if name not in value:
                errors.append(f"output_contract missing: {path}.{name}")
        for name, subschema in properties.items():
            if name in value:
                errors.extend(contract_errors(value[name], subschema, f"{path}.{name}"))
    return errors


def review_decisions(work: Path) -> dict[tuple[str, str], dict[str, Any]]:
    rows = read_json(work / "review_decisions.json", [])
    decisions: dict[tuple[str, str], dict[str, Any]] = {}
    if not isinstance(rows, list):
        return decisions
    for row in rows:
        if isinstance(row, dict) and row.get("decision") == "reviewed" and row.get("reviewer") and row.get("rationale"):
            decisions[(str(row.get("target")), str(row.get("code")))] = row
    return decisions


def decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return str(int(normalized))
    return format(normalized, "f")


NUMBER_WORDS = {
    0: {"zero"}, 1: {"one", "single"}, 2: {"two", "both", "pair"}, 3: {"three"},
    4: {"four"}, 5: {"five"}, 6: {"six"}, 7: {"seven"}, 8: {"eight"}, 9: {"nine"},
    10: {"ten"}, 11: {"eleven"}, 12: {"twelve", "dozen"}, 13: {"thirteen"},
    14: {"fourteen"}, 15: {"fifteen"}, 16: {"sixteen"}, 17: {"seventeen"},
    18: {"eighteen"}, 19: {"nineteen"}, 20: {"twenty"}, 30: {"thirty"},
    40: {"forty"}, 50: {"fifty"}, 60: {"sixty"}, 70: {"seventy"}, 80: {"eighty"},
    90: {"ninety"}, 100: {"hundred"},
}


def word_numeral_matches(value: str, text: str) -> bool:
    """True when an English number word in the text spells the same value.

    Only used to resolve a "not found" claim; it never rewrites the source text.
    """

    try:
        number = Decimal(value)
    except InvalidOperation:
        return False
    if number != number.to_integral():
        return False
    spellings = NUMBER_WORDS.get(int(number))
    if not spellings:
        return False
    folded = normalize_text(text).casefold()
    return any(re.search(rf"(?<![a-z]){word}(?![a-z])", folded) for word in spellings)


def derived_numeric_hint(value: str, evidence_values: set[str]) -> str | None:
    """Explain how a computed value could be derived from two evidence values."""

    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    numeric = sorted(evidence_values, key=lambda item: (len(item), item))
    for left in numeric:
        for right in numeric:
            if left == right:
                continue
            try:
                a, b = Decimal(left), Decimal(right)
            except InvalidOperation:
                continue
            for relation, result in (("difference", abs(a - b)), ("ratio", (a / b) if b else None)):
                if result is None:
                    continue
                if abs(result - number) <= Decimal("0.02") * max(abs(number), Decimal(1)):
                    return f"derived {relation} of evidence values {left} and {right}"
    return None


def numeric_values(text: str) -> set[str]:
    values: set[str] = set()
    for match in SCIENTIFIC_NUMBER_RE.finditer(text):
        try:
            coefficient = Decimal(match.group("coeff"))
            exponent = int(match.group("exponent").replace("−", "-"))
            # A broken PDF text layer can concatenate an exponent and adjacent
            # table cells. Bound expansion before constructing a decimal string.
            if abs(exponent) > 1000:
                continue
            values.add(decimal_text(coefficient * (Decimal(10) ** exponent)))
        except (InvalidOperation, ValueError):
            pass
    for match in UNIT_NUMBER_RE.finditer(text):
        prefix = text[max(0, match.start() - 16):match.start()].strip().lower()
        if prefix.endswith(REVIEW_LOCATION_PREFIXES):
            continue
        try:
            value = Decimal(match.group("number").replace(",", ""))
            unit = (match.group("unit") or "").lower()
            multiplier = {"k": Decimal(1000), "m": Decimal(1000000), "b": Decimal(1000000000),
                          "million": Decimal(1000000), "billion": Decimal(1000000000),
                          "thousand": Decimal(1000),
                          "万": Decimal(10000), "亿": Decimal(100000000)}.get(unit, Decimal(1))
            values.add(decimal_text(value * multiplier))
        except InvalidOperation:
            pass
    return values


def cross_section_numeric_hints(pages_by_id: dict[int, dict[str, Any]]) -> list[str]:
    observations: dict[tuple[str, str], set[tuple[str, int]]] = {}
    pattern = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(M|million|万|亿)\s+([A-Za-z][A-Za-z_-]{2,})", re.IGNORECASE)
    for page, row in sorted(pages_by_id.items()):
        for match in pattern.finditer(row.get("raw_text", "")):
            key = (match.group(2).casefold(), match.group(3).casefold())
            observations.setdefault(key, set()).add((match.group(1), page))
    hints = []
    for (unit, entity), values in sorted(observations.items()):
        distinct = {value for value, _ in values}
        if len(distinct) > 1:
            detail = sorted(values, key=lambda item: item[1])
            hints.append(f"Potential cross-section quantity conflict for {entity} in {unit}: {detail}")
    return hints


def is_reference_section(title: str) -> bool:
    return normalize_text(title).casefold().startswith(("references", "bibliography", "参考文献", "works cited"))


def block_order_key(block: dict[str, Any]) -> tuple[int, int]:
    order = block.get("source_order")
    if not isinstance(order, int):
        order = block.get("reading_order", 0)
    return int(block.get("pdf_page", 0)), int(order)


def strip_location_fields(value: Any) -> Any:
    """Drop page/coordinate fields before numeric provenance checks.

    ``pdf_page``/``printed_page``/``row`` numbers locate evidence; they are not
    claims that must appear verbatim in the source text.
    """

    location_keys = {"pdf_page", "printed_page", "page", "pages", "row", "column", "reading_order",
                     "source_order", "block_id", "section_id", "note_id"}
    if isinstance(value, dict):
        return {key: strip_location_fields(item) for key, item in value.items() if key not in location_keys}
    if isinstance(value, list):
        return [strip_location_fields(item) for item in value]
    return value


def section_heading_candidates(
    blocks: list[dict[str, Any]], sections: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Independent heading candidates used to check the section tree for omissions."""

    ordered = sorted(blocks, key=block_order_key)
    letter_items: list[tuple[str, str]] = []
    payload: dict[str, dict[str, Any]] = {}
    named: list[dict[str, Any]] = []
    for block in ordered:
        raw = block.get("raw_text", "")
        shape = letter_heading_shape(raw)
        if shape is not None:
            letter_items.append((shape[0], block["block_id"]))
            payload[block["block_id"]] = {"block_id": block["block_id"], "pdf_page": block.get("pdf_page"),
                                          "title": normalize_text(shape[2]), "source": "letter_appendix_heading"}
            continue
        title = named_heading_title(raw)
        if title:
            named.append({"block_id": block["block_id"], "pdf_page": block.get("pdf_page"),
                          "title": title, "source": "named_heading"})
    references_in_tree = any(is_reference_section(section.get("title", "")) for section in sections)
    accepted = accept_letter_chain(letter_items, references_before=references_in_tree)
    return [payload[block_id] for block_id in accepted] + named


def pdf_outline_titles(sections_doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Read bookmark titles and 1-based destinations from the source PDF."""

    pdf_path = sections_doc.get("source_pdf")
    if not isinstance(pdf_path, str):
        return []
    path = Path(pdf_path)
    if not path.is_file():
        return []
    fitz, _ = fitz_module()
    try:
        if fitz is not None:
            with fitz.open(path) as document:
                if not document.needs_pass:
                    return [
                        {"block_id": None, "pdf_page": page, "title": normalize_text(title),
                         "source": "pdf_outline", "level": level}
                        for level, title, page in document.get_toc(simple=True)[:80]
                        if page > 0 and normalize_text(title)
                    ]
    except Exception:
        pass
    module, _ = pypdf2_module()
    if module is None:
        return []

    def walk(items, level=1):
        for item in items:
            if isinstance(item, list):
                yield from walk(item, level + 1)
            else:
                yield level, item

    try:
        reader = module.PdfReader(str(path))
        entries = []
        for level, item in walk(reader.outline):
            if len(entries) >= 80:
                break
            try:
                page = reader.get_destination_page_number(item)
                title = normalize_text(str(item.get("/Title", "")))
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            if page >= 0 and title:
                entries.append({"block_id": None, "pdf_page": page + 1, "title": title,
                                "source": "pdf_outline", "level": level})
        return entries
    except Exception:
        return []


def section_completeness_findings(
    sections: list[dict[str, Any]], blocks: list[dict[str, Any]], sections_doc: dict[str, Any],
    scope_pages: set[int] | None = None,
) -> list[tuple[str, str]]:
    """(target, message) pairs for real heading candidates that never entered the tree."""

    tree_titles = {normalize_text(section.get("title", "")).rstrip(".。:：").casefold() for section in sections}
    unnumbered_titles = {re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", title) for title in tree_titles}
    tree_blocks = {section.get("heading_block_id") for section in sections if section.get("heading_block_id")}
    findings: list[tuple[str, str]] = []
    seen_titles: set[str] = set()
    for candidate in section_heading_candidates(blocks, sections) + pdf_outline_titles(sections_doc):
        page = candidate.get("pdf_page")
        if scope_pages and isinstance(page, int) and not any(abs(page - item) <= 1 for item in scope_pages):
            continue
        title = normalize_text(candidate["title"]).rstrip(".。:：")
        if not title or title.casefold() in tree_titles or candidate.get("block_id") in tree_blocks:
            continue
        if candidate["source"] == "pdf_outline" and title.casefold() in unnumbered_titles:
            continue
        if title.casefold() in seen_titles:
            continue
        seen_titles.add(title.casefold())
        target = candidate.get("block_id") or f"outline_p{candidate.get('pdf_page')}"
        findings.append((target, f"heading candidate '{title}' (page {candidate.get('pdf_page')}, {candidate['source']}) is absent from sections.json; split or add the section and record the review"))
    return findings


ABSENCE_CLAIM_RE = re.compile(
    r"(?:附录|Appendix)\s*(?:[A-Z]|[0-9]|[一二三四五六七八九十])"
    r"|(?:作者(?:明确)?(?:承认|报告|指出|记录)|authors?\s+(?:acknowledge|report|note|admit))"
    r"|(?:未处理|未覆盖|失效模式|检索坍塌|坍塌|failure mode|breaks down)"
    r"|(?:broader impact|Broader Impact)",
    re.IGNORECASE,
)
NO_CLAIM_RE = re.compile(
    r"(?:未报告|没有报告|未做报告|not reported|not stated|absent from the paper)",
    re.IGNORECASE,
)


def status_semantics_findings(target: str, status: str, content: Any, anchors: list[Any] | None) -> list[str]:
    """Flag status/content contradictions; the validator cannot judge truth, only coherence."""

    text = json.dumps(content, ensure_ascii=False) if not isinstance(content, str) else content
    findings: list[str] = []
    if status == "not_reported" and ABSENCE_CLAIM_RE.search(text):
        findings.append(f"{target}: status is not_reported but the content itself cites reported limitations/appendix evidence; use reported or remove that passage")
    if status in {"reported", "conflict", "uncertain"} and NO_CLAIM_RE.search(text):
        findings.append(f"{target}: status is {status} but the content asserts the item is not reported; reconcile the status with the content")
    if status == "conflict" and anchors is not None and len([a for a in anchors if isinstance(a, dict)]) < 2:
        findings.append(f"{target}: conflict status needs at least two anchors that show the disagreeing statements")
    return findings


def validate_work(work: Path, case: dict[str, Any] | None) -> dict[str, Any]:
    errors, warnings, machine = [], [], []
    informational: list[str] = []
    resolved_warnings, warning_details = [], []
    decisions = review_decisions(work)
    sections_doc = read_json(work / "sections.json", {"sections": []})
    sections = sections_doc.get("sections", [])
    notes, note_errors = read_jsonl(work / "notes.jsonl")
    pages, page_errors = read_jsonl(work / "pages.jsonl")
    blocks, block_errors = read_jsonl(work / "blocks.jsonl")
    errors.extend(note_errors + page_errors + block_errors)
    pages_by_id = {row["pdf_page"]: row for row in pages if isinstance(row, dict) and isinstance(row.get("pdf_page"), int)}
    blocks_by_id = {row["block_id"]: row for row in blocks if isinstance(row, dict) and row.get("block_id")}
    section_ids = {section["section_id"] for section in sections}
    if len(section_ids) != len(sections):
        errors.append("sections.json contains duplicate section_id values")
    ocr_pages: dict[int, list[str]] = {}
    for row in read_json(work / "ocr_report.json", {}).get("ocr_pages", []):
        path = Path(row.get("text", ""))
        if isinstance(row.get("pdf_page"), int) and path.is_file():
            ocr_pages.setdefault(row["pdf_page"], []).append(path.read_text(encoding="utf-8", errors="replace"))
    rendered_pages: dict[int, list[str]] = {}
    for row in read_json(work / "render_report.json", {}).get("rendered", []):
        path = Path(row.get("path", ""))
        if isinstance(row.get("pdf_page"), int) and path.is_file():
            rendered_pages.setdefault(row["pdf_page"], []).append(str(path))

    def warn(code: str, target: str, message: str) -> None:
        detail = {"code": code, "target": target, "message": message}
        decision = decisions.get((target, code))
        if decision:
            resolved_warnings.append({**detail, "review": decision})
        else:
            warnings.append(message)
            warning_details.append(detail)

    seen_ids: set[str] = set()
    valid_anchors = accepted_anchor_count = visual_anchor_count = 0
    total_anchors = contract_anchor_count = 0
    covered_sections: set[str] = set()
    required_note_keys = {"note_id", "section_id", "field", "content", "status", "anchors"}
    if not sections:
        errors.append("sections.json contains no reviewable section tree")
    if not notes:
        errors.append("notes.jsonl contains no actual notes")

    # Section provenance is independently checkable, unlike title-only agreement.
    for section in sections:
        label = section.get("section_id", "<unknown-section>")
        start, end = section.get("pdf_page_start"), section.get("pdf_page_end")
        if not isinstance(start, int) or start not in pages_by_id or not isinstance(end, int) or end < start or end not in pages_by_id:
            errors.append(f"{label}: invalid pdf_page_start/end {start}/{end}")
        block_id = section.get("heading_block_id")
        evidence = section.get("segmentation_evidence", {})
        if block_id is None:
            if evidence.get("source") != "implicit_front_matter":
                errors.append(f"{label}: missing heading_block_id without implicit_front_matter evidence")
            continue
        heading_block = blocks_by_id.get(block_id)
        if heading_block is None:
            errors.append(f"{label}: heading block {block_id!r} does not exist")
            continue
        if heading_block.get("pdf_page") != start:
            errors.append(f"{label}: heading block page does not match pdf_page_start")
        quote = evidence.get("quote")
        if not isinstance(quote, str) or not quote.strip() or normalize_text(quote) not in normalize_text(heading_block.get("raw_text", "")):
            errors.append(f"{label}: segmentation_evidence.quote does not match its heading block")
        title = normalize_text(section.get("title", "")).rstrip(".。:：")
        correction = evidence.get("title_correction")
        if isinstance(correction, dict) and correction.get("reviewer") and correction.get("rationale"):
            title_ok = title == normalize_text(correction.get("to", "")).rstrip(".。:：") and normalize_text(correction.get("from", "")) in normalize_text(quote or "")
        else:
            title_ok = title in normalize_text(heading_block.get("raw_text", ""))
        if not title_ok:
            errors.append(f"{label}: section title is not supported by its heading block or reviewed title_correction")
    for block in blocks:
        sid = block.get("section_id")
        if sid in section_ids and block.get("pdf_page") in pages_by_id:
            section = next(item for item in sections if item["section_id"] == sid)
            if not (section.get("pdf_page_start", 1) <= block["pdf_page"] <= section.get("pdf_page_end", 0)):
                errors.append(f"{block.get('block_id')}: assigned page lies outside section {sid} bounds")
    # Check headings only around pages used by this task. This preserves the omission
    # guard without turning a focused answer into a full-paper restructuring exercise.
    task_pages = {
        anchor.get("pdf_page") for note in notes if isinstance(note, dict)
        for anchor in note.get("anchors", []) if isinstance(anchor, dict)
        and isinstance(anchor.get("pdf_page"), int)
    }
    for target, message in section_completeness_findings(sections, blocks, sections_doc, task_pages):
        warn("section_completeness", target, message)
    for section in sections:
        start, end = section.get("pdf_page_start"), section.get("pdf_page_end")
        if is_reference_section(section.get("title", "")) and isinstance(start, int) and isinstance(end, int) and end - start > 3:
            informational.append(
                f"{section['section_id']}: reference list spans pages {start}-{end}; long bibliographies are expected, "
                "but appendix headings inside this range would be a real omission")
        if is_reference_section(section.get("title", "")):
            swallowed = []
            for block in blocks:
                if block.get("section_id") != section["section_id"]:
                    continue
                if block.get("block_id") == section.get("heading_block_id"):
                    continue
                raw = block.get("raw_text", "")
                if letter_heading_shape(raw) or named_heading_title(raw):
                    swallowed.append(block.get("block_id"))
            if swallowed:
                warn("references_contains_heading", section["section_id"],
                     f"{section['section_id']}: reference section still owns heading-like blocks {swallowed}")

    def validate_anchor(anchor: Any, label: str, note_section: str | None = None, contract_anchor: bool = False) -> dict[str, Any] | None:
        nonlocal valid_anchors, accepted_anchor_count, visual_anchor_count, total_anchors, contract_anchor_count
        total_anchors += 1
        if contract_anchor:
            contract_anchor_count += 1
        if not isinstance(anchor, dict):
            errors.append(f"{label}: anchor must be an object")
            return None
        page, quote = anchor.get("pdf_page"), anchor.get("quote")
        if not isinstance(page, int) or page not in pages_by_id:
            errors.append(f"{label}: invalid pdf_page {page!r}")
            return None
        if not isinstance(quote, str) or not quote.strip():
            errors.append(f"{label}: evidence needs a nonempty quote")
            return None
        if note_section and anchor.get("section_id") and anchor.get("section_id") != note_section and not anchor.get("cross_section_reason"):
            errors.append(f"{label}: cross-section anchor requires cross_section_reason explaining the actual placement")
        if anchor.get("block_id"):
            block = blocks_by_id.get(anchor["block_id"])
            if block is None or block.get("pdf_page") != page:
                errors.append(f"{label}: block_id does not exist on the declared page")
            elif anchor.get("source") not in {"ocr", "rendered_page"}:
                if normalize_text(quote) not in normalize_text(block.get("raw_text", "")):
                    errors.append(f"{label}: quote was not found in the declared block; correct block_id or omit it for a page-level anchor")
        bbox = anchor.get("bbox")
        if bbox is not None and (not isinstance(bbox, list) or len(bbox) != 4 or not all(isinstance(x, (int, float)) for x in bbox)):
            errors.append(f"{label}: bbox must be four numeric coordinates or omitted")
        raw_page = pages_by_id[page].get("raw_text", pages_by_id[page].get("normalized_text", ""))
        exact = quote in raw_page
        normalized = normalize_text(quote) in normalize_text(raw_page)
        layout_match = False if normalized else layout_tolerant_quote_match(quote, raw_page)
        matched_backends = ["page_text"] if normalized else (["page_text_layout_order"] if layout_match else [])
        ocr_normalized = any(normalize_text(quote) in normalize_text(text) for text in ocr_pages.get(page, []))
        if ocr_normalized:
            matched_backends.append("ocr")
        visual = anchor.get("visual_review")
        visual_accepted = bool(
            anchor.get("source") == "rendered_page" and rendered_pages.get(page)
            and isinstance(visual, dict) and visual.get("reviewed") is True
            and visual.get("reviewer") and visual.get("rationale")
        )
        if visual_accepted and not normalized and not ocr_normalized:
            accepted_anchor_count += 1
            visual_anchor_count += 1
            matched_backends.append("rendered_visual_review")
        elif normalized or layout_match or ocr_normalized:
            valid_anchors += 1
            accepted_anchor_count += 1
        else:
            errors.append(f"{label}: quote was not found in page/OCR text and lacks valid rendered_page visual_review evidence")
        check = {
            "id": label, "pdf_page": page, "source": anchor.get("source", "text_layer"),
            "exact_match": exact, "normalized_match": normalized, "layout_order_match": layout_match,
            "ocr_match": ocr_normalized,
            "visual_review_accepted": visual_accepted, "matched_backends": matched_backends,
        }
        machine.append(check)
        if pages_by_id[page].get("suspect_glyph_tokens"):
            warn("text_layer_integrity", label, f"{label}: declared page has suspected glyph omissions; visual verification is required for affected words")
        if anchor.get("table") and (anchor.get("row") in (None, "") or anchor.get("column") in (None, "")):
            warn("table_granularity", label, f"{label}: table anchor should identify row and column semantically")
        return check

    duplicate_contents: dict[str, str] = {}
    derived_values_all: list[str] = []
    top_outline = sorted(
        (item for item in pdf_outline_titles(sections_doc) if item.get("level") == 1),
        key=lambda item: item.get("pdf_page", 0),
    )

    def headings_at(page: int) -> list[dict[str, Any]]:
        # PDF outline destinations in common journal PDFs point at the page before
        # the printed heading. At a shared boundary page either adjacent chapter
        # can legitimately own evidence, so retain both candidates there.
        starts = [(item.get("pdf_page", 0) + 1, item) for item in top_outline]
        current_index = None
        for index, (start, _) in enumerate(starts):
            if start > page:
                break
            current_index = index
        if current_index is None:
            return []
        candidates = [starts[current_index][1]]
        if page == starts[current_index][0] and current_index:
            candidates.append(starts[current_index - 1][1])
        return candidates

    def bare_title(value: Any) -> str:
        return re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", normalize_text(str(value))).rstrip(".。:：").casefold()

    for index, note in enumerate(notes):
        label = note.get("note_id", f"notes[{index}]") if isinstance(note, dict) else f"notes[{index}]"
        if not isinstance(note, dict):
            errors.append(f"{label}: note must be an object")
            continue
        missing = required_note_keys - note.keys()
        if missing:
            errors.append(f"{label}: missing keys {sorted(missing)}")
            continue
        if note["note_id"] in seen_ids:
            errors.append(f"{label}: duplicate note_id")
        seen_ids.add(note["note_id"])
        if empty_content(note["content"]):
            errors.append(f"{label}: content must not be empty")
        if note["section_id"] not in section_ids:
            errors.append(f"{label}: unknown section_id {note['section_id']}")
        else:
            covered_sections.add(note["section_id"])
            assigned = next(item for item in sections if item["section_id"] == note["section_id"])
            anchor_pages = [
                anchor.get("pdf_page") for anchor in note.get("anchors", [])
                if isinstance(anchor, dict) and isinstance(anchor.get("pdf_page"), int)
            ]
            if anchor_pages and top_outline and bare_title(assigned.get("title")) not in {
                "abstract", "front matter (before first detected heading)", "references"
            }:
                candidates = [heading for page in anchor_pages for heading in headings_at(page)]
                if candidates and all(
                    bare_title(heading.get("title")) != bare_title(assigned.get("title"))
                    for heading in candidates
                ):
                    expected_titles = list(dict.fromkeys(str(item.get("title")) for item in candidates))
                    message = (
                        f"{label}: evidence pages belong under outline heading(s) "
                        f"{expected_titles!r}, not assigned section {assigned.get('title')!r}; "
                        "repair the task-relevant section before delivery"
                    )
                    errors.append(message)
                    warning_details.append({"code": "note_section_context_blocking", "target": label,
                                            "message": message})
        signature = normalize_text(json.dumps(note["content"], ensure_ascii=False, sort_keys=True))
        duplicate_key = f"{note['section_id']}|{note['field']}|{signature}"
        if signature and duplicate_key in duplicate_contents:
            errors.append(f"{label}: duplicate field/content in section also used by {duplicate_contents[duplicate_key]}")
        else:
            duplicate_contents[duplicate_key] = label
        if note["status"] not in {"reported", "not_reported", "uncertain", "conflict"}:
            errors.append(f"{label}: invalid note status {note['status']!r}")
        if note["status"] in {"reported", "conflict", "uncertain"} and not note["anchors"]:
            errors.append(f"{label}: {note['status']} note needs at least one anchor")
        if note["status"] == "not_reported" and not note.get("searched_scope"):
            errors.append(f"{label}: not_reported requires searched_scope")
        if note["status"] == "not_reported" and note["anchors"]:
            warn("absence_anchor", label, f"{label}: not_reported should explain the searched scope rather than imply a fabricated absence")
        for anchor_index, anchor in enumerate(note.get("anchors", [])):
            validate_anchor(anchor, f"{label}.anchors[{anchor_index}]", note["section_id"])
        for message in status_semantics_findings(label, note["status"], note["content"], note.get("anchors", [])):
            warn("status_semantics", label, message)
        content_text = json.dumps(note["content"], ensure_ascii=False)
        content_values = numeric_values(content_text)
        quote_union = "\n".join(str(a.get("quote", "")) for a in note.get("anchors", []) if isinstance(a, dict))
        page_union = "\n".join(
            pages_by_id[a["pdf_page"]].get("raw_text", pages_by_id[a["pdf_page"]].get("normalized_text", ""))
            for a in note.get("anchors", [])
            if isinstance(a, dict) and isinstance(a.get("pdf_page"), int) and a["pdf_page"] in pages_by_id
        )
        quote_values, page_values = numeric_values(quote_union), numeric_values(page_union)
        if note.get("derived") is not True:
            for number in sorted(content_values - quote_values):
                if word_numeral_matches(number, quote_union) or word_numeral_matches(number, page_union):
                    machine.append({"id": f"{label}.numeric:{number}", "english_numeral_match": True})
                    continue
                if number in page_values:
                    warn("numeric_provenance", label, f"{label}: value {number} occurs on a declared page but not in selected quotes; semantic confirmation required")
                    continue
                hint = derived_numeric_hint(number, quote_values | page_values)
                if hint:
                    derived_values_all.append(f"{label}: value {number} is a {hint}")
                    continue
                warn("numeric_provenance", label, f"{label}: value {number} not found in combined evidence after unit/scientific normalization")
    def is_exempt_section(section: dict[str, Any]) -> bool:
        title = normalize_text(section.get("title", "")).casefold()
        if is_reference_section(title):
            return True
        if title.startswith(("acknowledg", "致谢", "funding", "front matter")):
            return True
        if section.get("segmentation_evidence", {}).get("source") == "implicit_front_matter":
            return True
        # A structural parent is covered when one of its own subsections has notes.
        start, end, level = section.get("pdf_page_start"), section.get("pdf_page_end"), int(section.get("level", 1))
        for other in sections:
            if other["section_id"] in covered_sections and int(other.get("level", 1)) > level:
                if isinstance(start, int) and isinstance(other.get("pdf_page_start"), int) and start <= other["pdf_page_start"] <= (end or start):
                    return True
        return False

    for section in sections:
        title = section["title"].lower()
        if section["section_id"] not in covered_sections and not is_exempt_section(section):
            warn("section_coverage", section["section_id"], f"{section['section_id']}: no notes; record a substantive note or a reviewed reason for exclusion")

    card = read_json(work / "paper_card.json", {})
    required_fields = ("research_question", "data", "method", "results", "conclusion", "limitations")
    for field in required_fields:
        item = card.get(field) if isinstance(card, dict) else None
        if not isinstance(item, dict) or "status" not in item or "content" not in item:
            errors.append(f"paper_card.{field}: missing status/content object")
            continue
        if item["status"] not in {"reported", "not_reported", "uncertain", "conflict", "unresolved"}:
            errors.append(f"paper_card.{field}: invalid status {item['status']!r}")
        if item["status"] == "unresolved":
            errors.append(f"paper_card.{field}: unresolved; the agent must report content or a bounded not_reported finding")
        if item["status"] in {"reported", "uncertain", "conflict"} and empty_content(item.get("content")):
            errors.append(f"paper_card.{field}: declared {item['status']} but content is empty")
        if item["status"] == "not_reported" and not item.get("searched_scope"):
            errors.append(f"paper_card.{field}: not_reported requires searched_scope")
        if item["status"] in {"reported", "conflict", "uncertain"} and not item.get("source_note_ids"):
            errors.append(f"paper_card.{field}: evidence-bearing field requires source_note_ids")
        for note_id in item.get("source_note_ids", []):
            if note_id not in seen_ids:
                errors.append(f"paper_card.{field}: unknown source note {note_id}")
        for message in status_semantics_findings(f"paper_card.{field}", item["status"], item.get("content"), None):
            warn("status_semantics", f"paper_card.{field}", message)

    contract = discover_case_contract(case or {})
    if contract is None:
        errors.append("case has no discoverable output_contract")
    if case and case.get("case_id", case.get("id")) is None:
        errors.append("case has no case_id or id")
    uncertainty = read_json(work / "uncertainty.json", [])
    if not isinstance(uncertainty, list):
        errors.append("uncertainty.json must contain a JSON array")

    # Anti-self-deception guard: a run may not silently imply visual verification it could not perform.
    capabilities = read_json(work / "capabilities.json", None)
    if isinstance(capabilities, dict):
        render_report = read_json(work / "render_report.json", {})
        rendered_ok = bool(isinstance(render_report, dict) and render_report.get("ok"))
        declared = {str(item.get("kind", "")) for item in uncertainty if isinstance(item, dict)}
        if not capabilities.get("can_render") and not rendered_ok:
            if not declared & {"no_render_backend", "no_visual_review", "capability_gap", "visual_review_unavailable"}:
                warn("capability_gap_undeclared", "capabilities.json",
                     "no render backend is available and no page was rendered, but uncertainty.json does not "
                     "declare the visual-verification gap; record it instead of implying figures were checked")
        visual_claims = 0
        for note in notes:
            if not isinstance(note, dict):
                continue
            for anchor in note.get("anchors", []):
                if isinstance(anchor, dict) and anchor.get("visual_review"):
                    visual_claims += 1
        if visual_claims and not capabilities.get("can_render") and not capabilities.get("vision_model_available"):
            warn("visual_review_unverifiable", "capabilities.json",
                 f"{visual_claims} anchor(s) claim visual_review while the environment reports no render backend "
                 "and no vision model; a visual claim must be backed by a real rendered page or OCR text")

    supplied = read_json(work / "contract_values.json", {})
    merged_contract = deep_merge(contract_skeleton(contract), supplied)
    errors.extend(contract_errors(merged_contract, contract, "result"))

    def note_references(value: Any) -> set[str]:
        return {match.group(0) for match in re.finditer(r"note_\d{4,}", json.dumps(value, ensure_ascii=False))}

    def direct_anchors(value: Any) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        if isinstance(value, dict):
            if "pdf_page" in value and "quote" in value:
                found.append(value)
            for item in value.values():
                found.extend(direct_anchors(item))
        elif isinstance(value, list):
            for item in value:
                found.extend(direct_anchors(item))
        return found

    notes_by_id = {str(note.get("note_id")): note for note in notes
                   if isinstance(note, dict) and note.get("note_id")}

    def note_provenance_text(note: dict[str, Any]) -> str:
        parts = [json.dumps(note.get("content", ""), ensure_ascii=False)]
        for anchor in note.get("anchors", []):
            if not isinstance(anchor, dict):
                continue
            parts.append(str(anchor.get("quote", "")))
        return "\n".join(parts)

    contract_quotes: list[str] = []

    def check_contract_anchors(anchors: list[dict[str, Any]], label: str) -> list[str]:
        quotes = []
        for index, anchor in enumerate(anchors):
            error_count = len(errors)
            check = validate_anchor(anchor, f"{label}.anchors[{index}]", contract_anchor=True)
            if check and check["matched_backends"] and len(errors) == error_count:
                quotes.append(anchor["quote"])
        contract_quotes.extend(quotes)
        return quotes

    if isinstance(merged_contract, dict):
        key_results = merged_contract.get("key_results")
        if isinstance(key_results, list):
            for index, item in enumerate(key_results):
                references = note_references(item)
                unknown = references - seen_ids
                if unknown:
                    errors.append(f"key_results[{index}]: unknown note references {sorted(unknown)}")
                anchors = direct_anchors(item)
                anchor_quotes = check_contract_anchors(anchors, f"key_results[{index}]")
                if not references and not anchors:
                    errors.append(f"key_results[{index}]: must bind evidence via note_id/source_note_ids or an explicit quote/page anchor")
                if references or anchors:
                    source_parts = [note_provenance_text(notes_by_id[reference])
                                    for reference in sorted(references) if reference in notes_by_id]
                    source_parts.extend(anchor_quotes)
                    item_values = numeric_values(json.dumps(strip_location_fields(item), ensure_ascii=False))
                    source_values = numeric_values("\n".join(source_parts))
                    for value in sorted(item_values - source_values):
                        if derived_numeric_hint(value, source_values):
                            continue
                        warn("key_result_numeric_provenance", f"key_results[{index}]",
                             f"key_results[{index}]: value {value} not found in its cited notes or anchor quotes")
        evidence = merged_contract.get("evidence")
        if "evidence" in merged_contract:
            references = note_references(evidence)
            unknown = references - seen_ids
            if unknown:
                errors.append(f"evidence: unknown note references {sorted(unknown)}")
            anchors = direct_anchors(evidence)
            check_contract_anchors(anchors, "evidence")
            if not references and not anchors:
                errors.append("evidence: must reference actual notes or contain explicit quote/page anchors")
        contract_text = json.dumps(strip_location_fields(merged_contract), ensure_ascii=False)
        all_quotes = "\n".join(str(a.get("quote", "")) for note in notes if isinstance(note, dict) for a in note.get("anchors", []) if isinstance(a, dict))
        all_pages = "\n".join(row.get("raw_text", "") for row in pages)
        missing_values = (numeric_values(contract_text) - numeric_values(all_quotes)
                          - numeric_values("\n".join(contract_quotes)) - numeric_values(all_pages))
        for value in sorted(missing_values):
            warn("contract_numeric_provenance", "contract_values", f"contract: value {value} not found in notes or page provenance")
    for hint in cross_section_numeric_hints(pages_by_id):
        informational.append(hint)
    elapsed = elapsed_seconds(work)
    if elapsed is not None and elapsed > 900:
        errors.append(f"execution budget exceeded: elapsed_seconds={elapsed:.3f} > 900")
    requests = request_summary(work)
    if requests["requests_used"] is not None and requests["requests_used"] > REQUEST_LIMIT:
        errors.append(f"request budget exceeded: {requests['requests_used']} logged model requests > {REQUEST_LIMIT}")
    deadline_warning = elapsed is not None and elapsed >= DEADLINE_WARN_SECONDS and elapsed <= 900
    if deadline_warning:
        print(json.dumps({"deadline_warning": True, "elapsed_seconds": elapsed,
                          "advice": "Keep the remaining budget for assembly; drop optional notes and keep a partial result."},
                         ensure_ascii=False), file=sys.stderr)
    report = {
        "tool_version": VERSION, "checked_at": utc_now(), "ok": not errors,
        "counts": {
            "sections": len(sections), "notes": len(notes), "anchors": total_anchors,
            "contract_anchors": contract_anchor_count, "valid_anchors_under_normalization": valid_anchors,
            "accepted_anchors": accepted_anchor_count, "visual_only_anchors": visual_anchor_count,
            "covered_sections": len(covered_sections),
        },
        "anchor_validity_rate": (accepted_anchor_count / total_anchors if total_anchors else None),
        "text_anchor_match_rate": (valid_anchors / total_anchors if total_anchors else None),
        "errors": errors, "warnings": warnings, "warning_details": warning_details,
        "resolved_warnings": resolved_warnings,
        "unresolved_review_warning_count": len(warnings),
        "elapsed_seconds": elapsed, "machine_anchor_checks": machine,
        "requests": requests, "stage_timings": stage_timings(work),
        "derived_values_not_required_in_quotes": derived_values_all,
        "deadline_warning": deadline_warning,
        "informational": informational,
        "semantic_review_required": [
            "Whether each quote supports its note or contract claim.", "Whether numbers, units, objects and conditions match.",
            "Whether inferred argument roles and section transitions are warranted.", "Whether section headings and levels reflect the paper.",
        ],
    }
    write_json_atomic(work / "validation_report.json", report)
    return report


NOTE_LABELS = {
    "zh": {
        "title": "结构化研究笔记", "case": "案例 ID", "status": "状态", "scope": "范围",
        "scope_text": "内容全部来自实际输入材料；未解决的问题保持标注。",
        "review": "复核", "review_text": "机器检查不能替代语义复核。",
        "card": "论文卡片", "contract": "案例契约输出", "sections": "章节对齐笔记",
        "pages": "页码", "role": "论证位置", "transition": "章节承接", "evidence": "证据", "source": "来源",
        "content": "内容",
        "fields": {
            "research_question": "研究问题", "data": "数据", "method": "方法",
            "results": "主要结果", "conclusion": "结论", "limitations": "局限",
        },
        "printed": "印刷页码", "block": "块", "figure": "图", "table": "表",
        "status_text": {"reported": "原文已报告", "not_reported": "原文未报告", "uncertain": "不确定", "conflict": "材料冲突", "unresolved": "待确认"},
    },
    "en": {
        "title": "Structured research notes", "case": "Case ID", "status": "Status", "scope": "Scope",
        "scope_text": "Generated from actual supplied material; unresolved items remain marked.",
        "review": "Review", "review_text": "Machine checks do not replace semantic verification.",
        "card": "Paper card", "contract": "Case contract output", "sections": "Section-aligned notes",
        "pages": "Pages", "role": "Argument role", "transition": "Section transition", "evidence": "Evidence", "source": "Source",
        "content": "Content",
        "fields": {key: key for key in ("research_question", "data", "method", "results", "conclusion", "limitations")},
        "printed": "printed page", "block": "block", "figure": "figure", "table": "table",
        "status_text": {},
    },
}


def markdown_notes(work: Path, lang: str = "zh") -> str:
    label = NOTE_LABELS.get(lang, NOTE_LABELS["zh"])
    sections = read_json(work / "sections.json", {"sections": []}).get("sections", [])
    notes, _ = read_jsonl(work / "notes.jsonl")
    card = read_json(work / "paper_card.json", {})
    contract_values = read_json(work / "contract_values.json", {})
    state = read_json(state_path(work), {})
    lines = [
        f"# {label['title']}", "",
        f"- {label['case']}: `{state.get('case_id')}`",
        f"- {label['status']}: `{state.get('status', 'partial')}`",
        f"- {label['scope']}: {label['scope_text']}",
        f"- {label['review']}: {label['review_text']}", "",
        f"## {label['card']}", "",
    ]
    for field in ("research_question", "data", "method", "results", "conclusion", "limitations"):
        item = card.get(field, {})
        lines.append(f"### {label['fields'].get(field, field)}")
        lines.append("")
        status = item.get("status", "unresolved")
        status_text = label["status_text"].get(status, status)
        lines.append(f"- {label['status']}: `{status}`" + (f"（{status_text}）" if lang == "zh" and status_text != status else ""))
        lines.append(f"- {label['content']}: {item.get('content')}")
        lines.append(f"- {label['source']}: {item.get('source_note_ids', [])}")
        lines.append("")
    lines.extend([f"## {label['contract']}", "", "```json", json.dumps(contract_values, ensure_ascii=False, indent=2), "```", ""])
    by_section: dict[str, list[dict[str, Any]]] = {}
    for note in notes:
        by_section.setdefault(note.get("section_id", "sec_unassigned"), []).append(note)
    lines.extend([f"## {label['sections']}", ""])
    for section in sections:
        lines.append(f"### {section['section_id']} · {section['title']}")
        lines.append("")
        lines.append(f"{label['pages']}: {section.get('pdf_page_start')}–{section.get('pdf_page_end')}")
        lines.append("")
        for note in by_section.get(section["section_id"], []):
            lines.append(f"#### {note['note_id']} · {note['field']}")
            if note.get("argument_role"):
                lines.append(f"- {label['role']}: {note['argument_role']}")
            if note.get("section_transition"):
                lines.append(f"- {label['transition']}: {note['section_transition']}")
            status = note["status"]
            status_text = label["status_text"].get(status, status)
            lines.append(f"- {label['status']}: `{status}`" + (f"（{status_text}）" if lang == "zh" and status_text != status else ""))
            lines.append(f"- {label['content']}: {note['content']}")
            for anchor in note.get("anchors", []):
                printed = anchor.get("printed_page")
                location = f"PDF {label['pages'].rstrip('s')} {anchor.get('pdf_page')}"
                if printed not in (None, ""):
                    location += f"; {label['printed']} {printed}"
                if anchor.get("block_id"):
                    location += f"; {label['block']} {anchor['block_id']}"
                if anchor.get("figure") or anchor.get("table"):
                    location += f"; {label['figure']} {anchor.get('figure')}; {label['table']} {anchor.get('table')}"
                lines.append(f"- {label['evidence']}: {location}")
                lines.append(f"  > {anchor.get('quote', '')}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def command_validate(args: argparse.Namespace) -> None:
    work = Path(args.work).resolve()
    case_path = Path(args.case).resolve()
    if not case_path.is_file():
        fail(f"case file not found: {case_path}")
    case = read_json(case_path)
    started = time.perf_counter()
    report = validate_work(work, case)
    record_timing(work, "validate", time.perf_counter() - started,
                  notes=report["counts"].get("notes"), errors=len(report["errors"]))
    print(json.dumps({"ok": report["ok"], "errors": len(report["errors"]), "warnings": len(report["warnings"]), "counts": report["counts"]}, ensure_ascii=False))
    if not report["ok"]:
        raise SystemExit(2)


def command_assemble(args: argparse.Namespace) -> None:
    work, output = Path(args.work).resolve(), Path(args.output).resolve()
    case_path = Path(args.case).resolve()
    if not case_path.is_file():
        fail(f"case file not found: {case_path}")
    case = read_json(case_path)
    state = read_json(state_path(work), {})
    state["requested_success"] = bool(args.request_success)
    write_json_atomic(state_path(work), state)
    started = time.perf_counter()
    report = validate_work(work, case)
    contract = discover_case_contract(case)
    supplied = read_json(work / "contract_values.json", {})
    result = deep_merge(contract_skeleton(contract), supplied)
    notes, _ = read_jsonl(work / "notes.jsonl")
    sections = read_json(work / "sections.json", {"sections": []}).get("sections", [])
    sources = []
    for note in notes:
        for anchor in note.get("anchors", []):
            sources.append({"note_id": note["note_id"], **anchor})
    uncertainty = read_json(work / "uncertainty.json", [])
    if report["errors"]:
        uncertainty = uncertainty + [{"kind": "validation_errors", "detail": report["errors"]}]
    success = bool(args.request_success and report["ok"] and not report["warnings"] and contract is not None and case.get("case_id", case.get("id")) is not None)
    status = "success" if success else "partial"
    requested_run_status = getattr(args, "run_status", "auto")
    if requested_run_status == "success" and not success:
        requested_run_status = "partial"
    if requested_run_status == "auto":
        if success:
            run_status = "success"
        elif not notes and (not report.get("ok") or not report.get("counts", {}).get("notes")):
            run_status = "unable"
        else:
            run_status = "partial"
    else:
        run_status = requested_run_status
    status_reason = getattr(args, "status_reason", "") or {
        "success": "All machine checks passed with no unresolved warnings.",
        "unable": "No usable paper text or notes were produced; see extract_report.json, validation_report.json and uncertainty.json for the human handoff point.",
        "refused": "The requested material or task is outside this Skill's declared scope; no result was fabricated.",
        "partial": "Partial result: machine checks or unresolved warnings remain; see validation_report.json and uncertainty.json.",
    }.get(run_status, "")
    if run_status == "refused" and not getattr(args, "status_reason", ""):
        fail("--run-status refused requires --status-reason explaining the boundary and handoff")
    update_state(
        work, status=status, run_status=run_status, status_reason=status_reason,
        requested_success=bool(args.request_success),
        finished_at=utc_now(), elapsed_seconds=report.get("elapsed_seconds"),
    )
    output.mkdir(parents=True, exist_ok=True)
    add_change(work, "assembled_output", f"Assembled result.json with status={status}, run_status={run_status}.", ["result.json", "notes.md", "validation_report.json"])
    requests = report.get("requests", request_summary(work))
    request_count: Any = requests.get("requests_used")
    if request_count is None:
        request_count = "not_measured"
    result.update({
        "case_id": case.get("case_id", case.get("id")), "status": status,
        "run_status": run_status, "status_reason": status_reason,
        "elapsed_seconds": report.get("elapsed_seconds"), "request_count": request_count,
        "request_audit": requests,
        "structured_notes": notes, "paper_card": read_json(work / "paper_card.json", {}),
        "section_tree": sections, "sources": sources, "uncertainty": uncertainty,
        "change_records": read_jsonl(work / "changes.jsonl")[0],
        "verification": {
            "machine_checks_passed": report["ok"], "error_count": len(report["errors"]),
            "warning_count": len(report["warnings"]), "anchor_validity_rate": report["anchor_validity_rate"],
            "stage_timings": report.get("stage_timings", []),
            "semantic_review_still_required": True,
        },
    })
    write_json_atomic(output / "result.json", result)
    write_text_atomic(output / "notes.md", markdown_notes(work, args.lang))
    write_json_atomic(output / "validation_report.json", report)
    record_timing(work, "assemble", time.perf_counter() - started, status=status)
    print(json.dumps({"ok": True, "status": status, "run_status": run_status, "status_reason": status_reason, "validation_ok": report["ok"], "errors": report["errors"], "unresolved_warnings": report["warnings"]}, ensure_ascii=False))
    if args.request_success and not success:
        raise SystemExit(2)


def nested_section_count(items: Any) -> int:
    if not isinstance(items, list):
        return 0
    total = 0
    for item in items:
        if isinstance(item, dict):
            total += 1 + nested_section_count(item.get("children", []))
    return total


def has_content(value: Any) -> bool:
    if value in (None, "", [], {}):
        return False
    if isinstance(value, dict) and "content" in value:
        return has_content(value["content"])
    return True


def comparison_metrics(result_path: Path) -> dict[str, Any]:
    result = read_json(result_path)
    verification = result.get("verification", {}) if isinstance(result, dict) else {}
    card = result.get("paper_card", {}) if isinstance(result, dict) else {}
    return {
        "case_id": result.get("case_id"),
        "status": result.get("status"),
        "note_count": len(result.get("structured_notes", [])),
        "section_count_including_children": nested_section_count(result.get("section_tree", [])),
        "source_record_count": len(result.get("sources", [])),
        "evidence_record_count": len(result.get("evidence", [])),
        "card_fields_with_content": sum(1 for item in card.values() if has_content(item)),
        "card_fields_explicitly_reported": sum(1 for item in card.values() if isinstance(item, dict) and item.get("status") == "reported"),
        "card_fields_explicitly_not_reported": sum(1 for item in card.values() if isinstance(item, dict) and item.get("status") == "not_reported"),
        "machine_checks_passed": verification.get("machine_checks_passed", "not_measured"),
        "anchor_validity_rate": verification.get("anchor_validity_rate", "not_measured"),
        "machine_error_count": verification.get("error_count", "not_measured"),
        "machine_warning_count": verification.get("warning_count", "not_measured"),
        "request_count": result.get("request_count", "not_measured"),
        "elapsed_seconds": result.get("elapsed_seconds", "not_measured"),
    }


def command_compare(args: argparse.Namespace) -> None:
    baseline_path, skill_path = Path(args.baseline_result).resolve(), Path(args.skill_result).resolve()
    baseline, skilled = comparison_metrics(baseline_path), comparison_metrics(skill_path)
    same_case = baseline["case_id"] is not None and baseline["case_id"] == skilled["case_id"]
    report = {
        "baseline": baseline, "skill": skilled,
        "comparability": {
            "same_case_id": same_case,
            "fairness_not_proven_by_this_file": "Same paper, task, parsing conditions and model/tool budget must be established by the run protocol.",
            "official_platform_result": False,
        },
        "differences_skill_minus_baseline": {
            key: (skilled[key] - baseline[key])
            for key in ("note_count", "section_count_including_children", "source_record_count", "evidence_record_count", "card_fields_with_content")
            if isinstance(skilled[key], (int, float)) and isinstance(baseline[key], (int, float))
        },
        "interpretation": "Descriptive development comparison only. Note and evidence schemas can differ across agents, so counts are not automatically quality gains. Semantic correctness, content coverage and statistical significance are not established.",
    }
    write_json_atomic(Path(args.out).resolve(), report)
    print(json.dumps(report, ensure_ascii=False))
    if not same_case:
        raise SystemExit(2)


def parse_pages(value: str, page_count: int) -> list[int]:
    pages: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
            pages.update(range(start, end + 1))
        else:
            pages.add(int(part))
    invalid = sorted(page for page in pages if page < 1 or page > page_count)
    if invalid:
        raise ValueError(f"页码超出范围: {invalid}; document has {page_count} pages")
    return sorted(pages)


def open_renderable_pdf(pdf_path: Path):
    fitz, _ = fitz_module()
    if fitz is None:
        return None
    doc = fitz.open(pdf_path)
    if doc.needs_pass:
        doc.close()
        raise RuntimeError("PDF is encrypted and requires a password.")
    return doc


def pdf_page_count(pdf_path: Path) -> int:
    """Page count through the capability layer; never a hard dependency on PyMuPDF."""

    count, backend = render_backends.page_count(Path(pdf_path))
    if count:
        return int(count)
    raise RuntimeError(
        f"could not determine the PDF page count (last backend attempted: {backend}); "
        "the bundled PyPDF2 reader, Windows.Data.Pdf, PyMuPDF and pdfinfo all failed"
    )


def _legacy_pdf_page_count(pdf_path: Path) -> int:
    doc = open_renderable_pdf(pdf_path)
    if doc is not None:
        count = doc.page_count
        doc.close()
        return count
    executable = shutil.which("pdfinfo")
    if not executable:
        raise RuntimeError("Neither PyMuPDF nor pdfinfo is available to determine PDF page count.")
    completed = subprocess.run([executable, str(pdf_path)], capture_output=True, timeout=60, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.decode("utf-8", errors="replace").strip())
    match = re.search(r"^Pages:\s*(\d+)", completed.stdout.decode("utf-8", errors="replace"), re.MULTILINE)
    if not match:
        raise RuntimeError("pdfinfo did not report a page count.")
    return int(match.group(1))


def render_page_png(pdf_path: Path, page_number: int, dpi: int, target: Path) -> Path:
    """Rasterise one page through the probed backend chain (see render_backends.py)."""

    target.mkdir(parents=True, exist_ok=True)
    result = render_backends.render_pages(pdf_path, [page_number], target, dpi=dpi)
    if not result.get("ok"):
        attempts = "; ".join(
            f"{item['backend']}: {item['error']}" for item in result.get("backend_attempts", [])
        )
        raise RuntimeError(f"no usable PDF render backend ({attempts})")
    return Path(result["rendered"][0]["path"])


def available_ocr_engines() -> dict[str, str]:
    """OCR engines already usable in this environment; nothing has to be installed by a reviewer."""

    engines: dict[str, str] = {}
    probes = render_backends.probe_ocr()
    if probes.get("tesseract", {}).get("available"):
        engines["tesseract"] = probes["tesseract"]["detail"]
    if probes.get("rapidocr", {}).get("available"):
        engines["rapidocr"] = probes["rapidocr"]["detail"]
    if probes.get("winrt_ocr", {}).get("available"):
        engines["winrt_ocr"] = probes["winrt_ocr"]["detail"]
    return engines


def _rapidocr_text(recognizer: Any, image_path: Path) -> str:
    output = recognizer(str(image_path))
    if isinstance(output, tuple):
        lines = [str(item[1]) for item in (output[0] or []) if len(item) > 1 and str(item[1]).strip()]
    else:
        lines = [str(text) for text in (getattr(output, "txts", None) or []) if str(text).strip()]
    return "\n".join(lines) + ("\n" if lines else "")


def make_page_text_reader(engine: str, lang: str, timeout: int):
    """Return a callable that turns one rendered page image into text for the chosen engine."""

    if engine == "tesseract":
        executable = shutil.which("tesseract")
        if not executable:
            raise RuntimeError("tesseract requested but not found on PATH")

        def read_tesseract(image_path: Path) -> str:
            completed = subprocess.run(
                [executable, str(image_path), "stdout", "-l", lang],
                capture_output=True, encoding="utf-8", errors="replace", timeout=timeout, check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(f"tesseract failed: {completed.stderr.strip()}")
            return completed.stdout

        return read_tesseract

    if engine == "rapidocr":
        module_name = "rapidocr_onnxruntime" if importlib.util.find_spec("rapidocr_onnxruntime") else "rapidocr"
        if module_name == "rapidocr" and importlib.util.find_spec("rapidocr") is None:
            raise RuntimeError("rapidocr requested but no rapidocr package is importable")
        module = importlib.import_module(module_name)
        recognizer = module.RapidOCR()
        return lambda image_path: _rapidocr_text(recognizer, image_path)

    if engine == "winrt_ocr":
        # Windows.Media.Ocr uses BCP-47 tags (en-US), not tesseract's (eng).
        bcp47 = {"eng": "en-US", "en": "en-US", "chi_sim": "zh-Hans-CN", "chi": "zh-Hans-CN",
                 "zh": "zh-Hans-CN"}.get(lang, lang if "-" in lang else "en-US")

        def read_winrt(image_path: Path) -> str:
            result = render_backends.ocr_image(image_path, "winrt_ocr", lang=bcp47, timeout=timeout)
            return result.get("text", "") + ("\n" if result.get("text") else "")

        return read_winrt

    raise RuntimeError(f"unsupported OCR engine: {engine}")


def command_render(args: argparse.Namespace) -> None:
    work = Path(args.work).resolve()
    pdf_path = Path(args.pdf).resolve()
    report: dict[str, Any] = {"pdf": str(pdf_path), "timestamp": utc_now(), "rendered": []}
    try:
        pages = parse_pages(args.pages, pdf_page_count(pdf_path))
        target = work / "rendered"
        result = render_backends.render_pages(
            pdf_path, pages, target, dpi=args.dpi, backend=getattr(args, "backend", "auto"))
        report["backend"] = result.get("backend")
        report["backend_attempts"] = result.get("backend_attempts", [])
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "render failed"))
        for item in result.get("rendered", []):
            report["rendered"].append({
                "pdf_page": item["pdf_page"], "path": item["path"], "dpi": args.dpi,
                "width": item.get("width"), "height": item.get("height"),
                "bytes": item.get("bytes"), "ink_ratio": item.get("ink_ratio"),
                "suspect_blank": item.get("suspect_blank"), "concerns": item.get("concerns", []),
                "note": "Rendered from the actual PDF; visual content still requires a named reviewer or OCR.",
            })
        blank = [item["pdf_page"] for item in report["rendered"] if item.get("suspect_blank")]
        report["suspect_blank_pages"] = blank
        report["ok"] = True
        if blank:
            report["warning"] = f"pages {blank} look blank or undersized; do not treat them as evidence"
    except Exception as exc:
        report.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    write_json_atomic(work / "render_report.json", report)
    add_change(work, "rendered_pages" if report.get("ok") else "render_failed", json.dumps(report, ensure_ascii=False), ["render_report.json"])
    print(json.dumps(report, ensure_ascii=False))
    if not report.get("ok"):
        raise SystemExit(2)


def command_doctor(args: argparse.Namespace) -> None:
    """Report the real capability matrix so missing evidence is never mistaken for verified evidence."""

    matrix = render_backends.doctor()
    text_backends = {
        "host_parser": "discovered as .parser/<pdf-stem>.jsonl",
        "pymupdf": "python module pymupdf or fitz",
        "bundled_pypdf2": "vendor/PyPDF2 (always present)",
        "pdftotext": shutil.which("pdftotext") or "not on PATH",
    }
    matrix["text_extraction"] = text_backends
    if getattr(args, "work", None):
        work = Path(args.work).resolve()
        write_json_atomic(work / "capabilities.json", matrix)
        matrix["written_to"] = str(work / "capabilities.json")
    print(json.dumps(matrix, ensure_ascii=False))
    if not matrix["can_render"] and not matrix["can_ocr"]:
        raise SystemExit(2)


def command_ocr(args: argparse.Namespace) -> None:
    work = Path(args.work).resolve()
    pdf_path = Path(args.pdf).resolve()
    engines = available_ocr_engines()
    requested = getattr(args, "engine", "auto")
    if requested == "auto":
        engine = next((name for name in ("tesseract", "rapidocr", "winrt_ocr") if name in engines), None)
    else:
        engine = requested if requested in engines else None
    report: dict[str, Any] = {
        "pdf": str(pdf_path), "timestamp": utc_now(), "ocr_pages": [],
        "engine": engine or requested, "available_engines": sorted(engines),
    }
    if engine is None:
        report.update({
            "ok": False,
            "needs_ocr_review": True,
            "fallback": "render+visual_review",
            "error": (
                f"No OCR engine available (requested={requested}). OCR is optional: PDFs with a text layer, "
                "the Host parser, or `render` + `rendered_page` anchors with a visual_review record all work "
                "without installing anything. Nothing must be configured for review."
            ),
        })
        write_json_atomic(work / "ocr_report.json", report)
        add_change(work, "ocr_unavailable", report["error"], ["ocr_report.json"])
        print(json.dumps(report, ensure_ascii=False))
        raise SystemExit(2)
    try:
        pages = parse_pages(args.pages, pdf_page_count(pdf_path))
        target = work / "ocr"
        target.mkdir(parents=True, exist_ok=True)
        reader = make_page_text_reader(engine, args.lang, args.timeout)
        for page_number in pages:
            image_path = render_page_png(pdf_path, page_number, args.dpi, target)
            text_path = target / f"page_{page_number:04d}.txt"
            text = reader(image_path)
            write_text_atomic(text_path, text)
            report["ocr_pages"].append({
                "pdf_page": page_number, "image": str(image_path), "text": str(text_path),
                "source": "ocr", "engine": engine, "character_count": len(text),
                "requires_semantic_review": True,
            })
        report["ok"] = True
    except Exception as exc:
        report.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    write_json_atomic(work / "ocr_report.json", report)
    add_change(work, "ocr_completed" if report.get("ok") else "ocr_failed", json.dumps(report, ensure_ascii=False), ["ocr_report.json"])
    print(json.dumps(report, ensure_ascii=False))
    if not report.get("ok"):
        raise SystemExit(2)


def command_record_change(args: argparse.Namespace) -> None:
    work = Path(args.work).resolve()
    add_change(work, args.event, args.detail, args.files)
    print(json.dumps({"ok": True, "event": args.event}, ensure_ascii=False))


def command_review(args: argparse.Namespace) -> None:
    work = Path(args.work).resolve()
    if not args.rationale.strip() or not args.reviewer.strip():
        fail("reviewer and rationale must be nonempty")
    rows = read_json(work / "review_decisions.json", [])
    if not isinstance(rows, list):
        fail("review_decisions.json must contain an array")
    row = {
        "target": args.target, "code": args.code, "decision": "reviewed",
        "reviewer": args.reviewer, "rationale": args.rationale, "reviewed_at": utc_now(),
    }
    replaced = False
    for index, existing in enumerate(rows):
        if isinstance(existing, dict) and existing.get("target") == args.target and existing.get("code") == args.code:
            rows[index] = row
            replaced = True
            break
    if not replaced:
        rows.append(row)
    write_json_atomic(work / "review_decisions.json", rows)
    add_change(work, "semantic_review_decision", json.dumps(row, ensure_ascii=False), ["review_decisions.json"])
    print(json.dumps({"ok": True, "review": row}, ensure_ascii=False))


def load_sections(work: Path) -> dict[str, Any]:
    sections_doc = read_json(work / "sections.json", {"sections": []})
    if not isinstance(sections_doc, dict) or not isinstance(sections_doc.get("sections"), list):
        fail("sections.json is missing a sections array")
    return sections_doc


def load_blocks(work: Path) -> list[dict[str, Any]]:
    blocks, errors = read_jsonl(work / "blocks.jsonl")
    if errors:
        fail(f"blocks.jsonl is not readable: {errors}")
    return blocks


def write_sections_and_blocks(work: Path, sections_doc: dict[str, Any], blocks: list[dict[str, Any]]) -> None:
    write_json_atomic(work / "sections.json", sections_doc)
    write_text_atomic(work / "blocks.jsonl", "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in blocks))


def next_section_id(sections: list[dict[str, Any]]) -> str:
    numbers = []
    for section in sections:
        match = re.fullmatch(r"sec_(\d+)", str(section.get("section_id", "")))
        if match:
            numbers.append(int(match.group(1)))
    return f"sec_{(max(numbers) + 1) if numbers else 0:04d}"


def recompute_bounds(sections: list[dict[str, Any]], blocks: list[dict[str, Any]]) -> None:
    for section in sections:
        pages = [block["pdf_page"] for block in blocks if block.get("section_id") == section["section_id"]]
        if pages:
            section["pdf_page_end"] = max(pages)
            if section.get("heading_block_id") or section.get("section_id") != "sec_0000":
                section["pdf_page_start"] = min(pages)


def block_index(blocks: list[dict[str, Any]], block_id: str) -> int:
    for index, block in enumerate(blocks):
        if block.get("block_id") == block_id:
            return index
    fail(f"block not found: {block_id}")
    return -1


def infer_section_level(title: str) -> int:
    """Derive a heading level from the numbering the paper itself prints.

    Heuristic per the skill's honesty rule: it is recorded as a level_correction with a
    reviewer and rationale, and it never invents a level for an unnumbered heading.
    """

    text = normalize_text(title or "").strip()
    if text.casefold().startswith(("front matter", "前言", "封面")):
        # Content before the first real heading is not a numbered section.
        return 0
    numbered = re.match(r"^([A-Z]|\d+)((?:\.\d+)*)(?=[\s.])", text)
    if numbered:
        depth = len([part for part in numbered.group(2).split(".") if part])
        return 1 + depth
    return 1


def command_sections(args: argparse.Namespace) -> None:
    work = Path(args.work).resolve()
    sections_doc = load_sections(work)
    sections = sections_doc["sections"]
    blocks = load_blocks(work)
    action = args.action
    if action == "list":
        print(json.dumps({"ok": True, "sections": [
            {"section_id": section["section_id"], "level": section.get("level"),
             "pdf_page_start": section.get("pdf_page_start"), "pdf_page_end": section.get("pdf_page_end"),
             "title": section.get("title")} for section in sections]}, ensure_ascii=False, indent=2))
        return
    if not args.reviewer.strip() or not args.rationale.strip():
        fail("reviewer and rationale must be nonempty for any section change")

    if action == "split":
        source = next((section for section in sections if section["section_id"] == args.section), None)
        if source is None:
            fail(f"unknown section: {args.section}")
        index = block_index(blocks, args.at_block)
        block = blocks[index]
        if block.get("section_id") != source["section_id"]:
            fail(f"block {args.at_block} belongs to {block.get('section_id')}, not {args.section}")
        cut_key = block_order_key(block)
        own_blocks = [item for item in blocks if item.get("section_id") == source["section_id"]]
        mode = getattr(args, "mode", "after") or "after"
        if mode == "before":
            moved = [item for item in own_blocks if block_order_key(item) < cut_key]
        else:
            moved = [item for item in own_blocks if block_order_key(item) >= cut_key]
        if len(moved) == len(own_blocks) or not moved:
            fail("split point would move the whole section or nothing; choose another block or use rename")
        heading_block = moved[0] if mode == "before" else block
        new_id = next_section_id(sections)
        original_title = normalize_text(heading_block.get("raw_text", "")).splitlines()[0] if heading_block.get("raw_text") else ""
        new_title = normalize_text(args.title.strip()) if args.title else original_title
        evidence: dict[str, Any] = {
            "source": "reviewed_section_split", "quote": heading_block.get("raw_text", ""),
            "needs_semantic_review": False, "reviewer": args.reviewer, "rationale": args.rationale,
        }
        if args.title and normalize_text(args.title) != original_title:
            evidence["title_correction"] = {"from": original_title, "to": new_title,
                                            "reviewer": args.reviewer, "rationale": args.rationale}
        entry = {
            "section_id": new_id, "title": new_title,
            "level": args.level if args.level else int(source.get("level", 1)),
            "pdf_page_start": heading_block["pdf_page"], "pdf_page_end": heading_block["pdf_page"],
            "heading_block_id": heading_block["block_id"], "segmentation_evidence": evidence,
        }
        position = sections.index(source)
        sections.insert(position if mode == "before" else position + 1, entry)
        for item in moved:
            item["section_id"] = new_id
        if heading_block.get("role") in {"candidate_body", "body"}:
            heading_block["role"] = "heading"
        recompute_bounds(sections, blocks)
        write_sections_and_blocks(work, sections_doc, blocks)
        add_change(work, "section_split",
                   json.dumps({"source_section": args.section, "new_section": new_id, "at_block": args.at_block,
                               "mode": mode, "title": new_title, "reviewer": args.reviewer,
                               "rationale": args.rationale, "moved_blocks": len(moved)}, ensure_ascii=False),
                   ["sections.json", "blocks.jsonl"])
        print(json.dumps({"ok": True, "new_section": new_id, "moved_blocks": len(moved), "sections": len(sections)}, ensure_ascii=False))
        return

    if action == "merge":
        ids = [value.strip() for value in args.sections.split(",") if value.strip()]
        targets = [section for section in sections if section["section_id"] in ids]
        if len(targets) < 2:
            fail("merge needs at least two existing sections")
        first_keys = {}
        for section in targets:
            keys = [block_order_key(item) for item in blocks if item.get("section_id") == section["section_id"]]
            first_keys[section["section_id"]] = min(keys) if keys else (0, 0)
        keep = min(targets, key=lambda section: first_keys[section["section_id"]])
        for section in targets:
            if section["section_id"] == keep["section_id"]:
                continue
            for item in blocks:
                if item.get("section_id") == section["section_id"]:
                    item["section_id"] = keep["section_id"]
            sections.remove(section)
        keep.setdefault("segmentation_evidence", {})["merged_sections"] = {
            "sections": ids, "reviewer": args.reviewer, "rationale": args.rationale,
        }
        # Notes stay attached to the merged content, not to a section id that no longer exists.
        notes_path = work / "notes.jsonl"
        if notes_path.is_file():
            note_rows, _ = read_jsonl(notes_path)
            moved_notes = 0
            for row in note_rows:
                if isinstance(row, dict) and row.get("section_id") in ids and row.get("section_id") != keep["section_id"]:
                    row["section_id"] = keep["section_id"]
                    moved_notes += 1
            if moved_notes:
                write_text_atomic(notes_path, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in note_rows))
        recompute_bounds(sections, blocks)
        write_sections_and_blocks(work, sections_doc, blocks)
        add_change(work, "section_merge",
                   json.dumps({"merged": ids, "kept": keep["section_id"], "reviewer": args.reviewer,
                               "rationale": args.rationale}, ensure_ascii=False),
                   ["sections.json", "blocks.jsonl"])
        print(json.dumps({"ok": True, "kept": keep["section_id"], "sections": len(sections)}, ensure_ascii=False))
        return

    if action == "rename":
        section = next((item for item in sections if item["section_id"] == args.section), None)
        if section is None:
            fail(f"unknown section: {args.section}")
        if not args.title.strip():
            fail("rename needs a nonempty --title")
        old_title = section.get("title", "")
        new_title = normalize_text(args.title)
        section["title"] = new_title
        evidence = section.setdefault("segmentation_evidence", {})
        evidence["title_correction"] = {
            "from": old_title, "to": new_title, "reviewer": args.reviewer, "rationale": args.rationale,
        }
        evidence["needs_semantic_review"] = False
        write_sections_and_blocks(work, sections_doc, blocks)
        add_change(work, "section_rename",
                   json.dumps({"section": args.section, "from": old_title, "to": new_title,
                               "reviewer": args.reviewer, "rationale": args.rationale}, ensure_ascii=False),
                   ["sections.json"])
        print(json.dumps({"ok": True, "section": args.section, "title": new_title}, ensure_ascii=False))
        return

    if action == "levels":
        if not getattr(args, "auto", False) and not getattr(args, "section", None):
            fail("levels needs --auto or --section with --level")
        applied: list[dict[str, Any]] = []
        if getattr(args, "auto", False):
            for section in sections:
                old_level = int(section.get("level", 1))
                new_level = infer_section_level(section.get("title", ""))
                if new_level != old_level:
                    section["level"] = new_level
                    section.setdefault("segmentation_evidence", {})["level_correction"] = {
                        "from": old_level, "to": new_level,
                        "reviewer": args.reviewer, "rationale": args.rationale,
                        "source": "heading_numbering",
                    }
                    applied.append({"section_id": section["section_id"], "from": old_level, "to": new_level})
        else:
            section = next((item for item in sections if item["section_id"] == args.section), None)
            if section is None:
                fail(f"unknown section: {args.section}")
            if getattr(args, "level", None) is None:
                fail("levels --section needs --level")
            old_level = int(section.get("level", 1))
            section["level"] = int(args.level)
            section.setdefault("segmentation_evidence", {})["level_correction"] = {
                "from": old_level, "to": int(args.level),
                "reviewer": args.reviewer, "rationale": args.rationale,
                "source": "explicit_level",
            }
            applied.append({"section_id": section["section_id"], "from": old_level, "to": int(args.level)})
        write_sections_and_blocks(work, sections_doc, blocks)
        add_change(work, "section_levels",
                   json.dumps({"applied": applied, "reviewer": args.reviewer,
                               "rationale": args.rationale}, ensure_ascii=False),
                   ["sections.json"])
        print(json.dumps({"ok": True, "changed": len(applied), "applied": applied}, ensure_ascii=False))
        return

    fail(f"unknown sections action: {action}")


def command_request(args: argparse.Namespace) -> None:
    work = Path(args.work).resolve()
    rows, _ = read_jsonl(work / "request_ledger.jsonl")
    entries = [row for row in rows if isinstance(row, dict) and row.get("request_index") is not None]
    index = len(entries) + 1
    row = {
        "stage": args.stage, "request_index": index, "model": args.model,
        "input_tokens": args.input_tokens, "output_tokens": args.output_tokens,
        "source": args.source, "timestamp": utc_now(), "cumulative": index,
        "note": args.note,
    }
    append_jsonl(work / "request_ledger.jsonl", row)
    summary = request_summary(work)
    print(json.dumps({"ok": True, "logged": row, **summary}, ensure_ascii=False))
    if summary["requests_used"] and summary["requests_used"] > REQUEST_LIMIT:
        print(json.dumps({"error": f"request budget exceeded: {REQUEST_LIMIT}"}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)


def command_status(args: argparse.Namespace) -> None:
    work = Path(args.work).resolve()
    state = read_json(state_path(work), {})
    elapsed = elapsed_seconds(work)
    requests = request_summary(work)
    timings = stage_timings(work)
    by_stage: dict[str, float] = {}
    for row in timings:
        by_stage[row.get("stage", "?")] = round(by_stage.get(row.get("stage", "?"), 0.0) + float(row.get("seconds", 0)), 3)
    report = {
        "status": state.get("status", "partial"), "elapsed_seconds": elapsed,
        "elapsed_budget_seconds": 900, "request_count": requests["requests_used"] if requests["requests_used"] is not None else "not_measured",
        "requests_used": requests["requests_used"], "requests_remaining": requests["requests_remaining"],
        "request_source": requests["request_source"], "request_limit": REQUEST_LIMIT,
        "stage_seconds": by_stage,
        "deadline_warning": elapsed is not None and elapsed >= DEADLINE_WARN_SECONDS,
        "partial_result_required": elapsed is not None and elapsed >= 900,
        "note": "Elapsed time is wall time since init. request_source=ledger means the agent logged each model batch; unknown means no native usage ledger was available.",
    }
    print(json.dumps(report, ensure_ascii=False))
    if report["partial_result_required"]:
        raise SystemExit(2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Probe tools and create an honest partial result")
    init.add_argument("--case", required=True)
    init.add_argument("--task")
    init.add_argument("--pdf", action="append", default=[], help="Optional explicit PDF; may be repeated and can be outside input-dir")
    init.add_argument("--input-dir", required=True)
    init.add_argument("--work", required=True)
    init.add_argument("--output", required=True)
    init.set_defaults(func=command_init)
    extract = sub.add_parser("extract", help="Extract actual PDF pages, blocks and section candidates")
    extract.add_argument("--pdf", required=True)
    extract.add_argument("--work", required=True)
    extract.add_argument("--backend", choices=("auto", "host", "pymupdf", "pypdf2", "pdftotext"), default="auto")
    extract.add_argument("--parser-jsonl", help="Optional Host parser JSONL; otherwise discover .parser/<pdf-stem>.jsonl")
    extract.add_argument("--force", action="store_true", help="Re-extract even if pages/blocks/sections are newer than the PDF")
    extract.set_defaults(func=command_extract)
    ingest = sub.add_parser("ingest", help="Build pages/blocks/sections from real platform text or OCR records")
    ingest.add_argument("--pdf", required=True)
    ingest.add_argument("--records", required=True, help="JSONL with page, kind, text from a platform PDF/OCR tool")
    ingest.add_argument("--source", default="platform_tool", help="Actual tool/source label for the audit trail")
    ingest.add_argument("--work", required=True)
    ingest.set_defaults(func=command_ingest)
    validate = sub.add_parser("validate", help="Validate notes, anchors, card and output contract")
    validate.add_argument("--case", required=True)
    validate.add_argument("--work", required=True)
    validate.set_defaults(func=command_validate)
    assemble = sub.add_parser("assemble", help="Assemble result.json and notes.md without inventing content")
    assemble.add_argument("--case", required=True)
    assemble.add_argument("--work", required=True)
    assemble.add_argument("--output", required=True)
    assemble.add_argument("--request-success", action="store_true")
    assemble.add_argument("--run-status", choices=("auto", "success", "partial", "unable", "refused"), default="auto",
                          help="Four-state run status; success still requires --request-success and clean checks")
    assemble.add_argument("--status-reason", default="", help="Required when --run-status refused; otherwise overrides the default reason")
    assemble.add_argument("--lang", choices=("zh", "en"), default="zh", help="Label language for notes.md; JSON keys stay English")
    assemble.set_defaults(func=command_assemble)
    render = sub.add_parser("render", help="Render selected real PDF pages for visual inspection")
    render.add_argument("--pdf", required=True)
    render.add_argument("--pages", required=True, help="1-based pages/ranges, e.g. 1,3-5")
    render.add_argument("--dpi", type=int, default=160)
    render.add_argument("--backend", default="auto",
                        help="auto probes pymupdf, pypdfium2, pdftoppm, mutool, gs, then the bundled "
                             "Windows.Data.Pdf renderer; pin one only for diagnosis")
    render.add_argument("--work", required=True)
    render.set_defaults(func=command_render)
    ocr = sub.add_parser("ocr", help="OCR selected pages with any available engine; optional and not required for review")
    ocr.add_argument("--pdf", required=True)
    ocr.add_argument("--pages", required=True)
    ocr.add_argument("--engine", choices=("auto", "tesseract", "rapidocr", "winrt_ocr"), default="auto",
                     help="auto prefers an installed tesseract, then rapidocr, then the bundled Windows OCR; "
                          "no engine means exit 2 with the visual fallback recorded")
    ocr.add_argument("--dpi", type=int, default=220)
    ocr.add_argument("--lang", default="eng",
                     help="tesseract style code (eng/chi_sim); mapped to BCP-47 for the Windows OCR engine")
    ocr.add_argument("--timeout", type=int, default=60)
    ocr.add_argument("--work", required=True)
    ocr.set_defaults(func=command_ocr)
    doctor = sub.add_parser("doctor", help="Print the real render/OCR/text capability matrix before claiming evidence")
    doctor.add_argument("--work", help="optional: also write capabilities.json into this work directory")
    doctor.set_defaults(func=command_doctor)
    change = sub.add_parser("record-change", help="Record an actual manual or model-driven processing change")
    change.add_argument("--work", required=True)
    change.add_argument("--event", required=True)
    change.add_argument("--detail", required=True)
    change.add_argument("--files", nargs="*", default=[])
    change.set_defaults(func=command_record_change)
    compare = sub.add_parser("compare", help="Compare two actual result.json files without claiming an official score")
    compare.add_argument("--baseline-result", required=True)
    compare.add_argument("--skill-result", required=True)
    compare.add_argument("--out", required=True)
    compare.set_defaults(func=command_compare)
    review = sub.add_parser("review", help="Record a concrete semantic review decision before success")
    review.add_argument("--work", required=True)
    review.add_argument("--target", required=True, help="Section/note/contract target used by a warning")
    review.add_argument("--code", required=True, help="Warning code, e.g. section_coverage or numeric_provenance")
    review.add_argument("--reviewer", required=True)
    review.add_argument("--rationale", required=True)
    review.set_defaults(func=command_review)
    status = sub.add_parser("status", help="Show actual elapsed wall time and budget risk")
    status.add_argument("--work", required=True)
    status.set_defaults(func=command_status)
    sections_parser = sub.add_parser("sections", help="Audited section-tree edits (split/merge/rename/levels/list)")
    sections_sub = sections_parser.add_subparsers(dest="action", required=True)
    sections_list = sections_sub.add_parser("list", help="Print the current section tree")
    sections_list.add_argument("--work", required=True)
    sections_list.set_defaults(func=command_sections)
    for action_name, action_help in (("split", "Split one section at a concrete block"),
                                     ("merge", "Merge adjacent sections into the earliest one"),
                                     ("rename", "Correct a section title with review evidence"),
                                     ("levels", "Fix heading levels from the paper's own numbering")):
        action_parser = sections_sub.add_parser(action_name, help=action_help)
        action_parser.add_argument("--work", required=True)
        action_parser.add_argument("--reviewer", required=True)
        action_parser.add_argument("--rationale", required=True)
        if action_name == "split":
            action_parser.add_argument("--section", required=True)
            action_parser.add_argument("--at-block", required=True)
            action_parser.add_argument("--title")
            action_parser.add_argument("--level", type=int)
            action_parser.add_argument("--mode", choices=("after", "before"), default="after",
                                       help="after: cut block starts the new section; before: blocks preceding the cut block do")
        elif action_name == "merge":
            action_parser.add_argument("--sections", required=True, help="Comma separated section ids")
        elif action_name == "rename":
            action_parser.add_argument("--section", required=True)
            action_parser.add_argument("--title", required=True)
        else:
            action_parser.add_argument("--auto", action="store_true",
                                       help="Re-derive every level from the printed heading numbering, e.g. A.1 -> 2")
            action_parser.add_argument("--section")
            action_parser.add_argument("--level", type=int)
        action_parser.set_defaults(func=command_sections)
    request = sub.add_parser("request", help="Log one model request batch for budget auditing")
    request.add_argument("--work", required=True)
    request.add_argument("--stage", required=True, help="e.g. section_notes, key_results, review")
    request.add_argument("--model", default="unknown")
    request.add_argument("--input-tokens", type=int)
    request.add_argument("--output-tokens", type=int)
    request.add_argument("--source", choices=("ledger", "native", "estimated"), default="ledger")
    request.add_argument("--note", default="")
    request.set_defaults(func=command_request)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except SystemExit:
        raise
    except Exception as exc:
        fail(f"{type(exc).__name__}: {exc}", 1)


if __name__ == "__main__":
    main()
