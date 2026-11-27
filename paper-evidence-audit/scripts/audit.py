"""Prepare a reading case and check claims before publishing local artifacts."""

import argparse
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
import unicodedata
import contextlib
import io

import notes_tool as core


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def folded(value):
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return " ".join(text.split())


MATH_TRANSLATION = str.maketrans({
    "Ω": "omega", "Ω": "omega", "ω": "omega", "σ": "sigma", "Σ": "sigma",
    "Λ": "lambda", "λ": "lambda", "τ": "tau", "θ": "theta",
    "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
    "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
})


def variable_key(value):
    """Canonicalize a complete scientific variable without accepting prefixes."""
    text = unicodedata.normalize("NFKC", str(value)).translate(MATH_TRANSLATION).casefold()
    text = re.sub(r"\[[^\]]*\]\s*$", "", text)  # trailing unit, not part of a variable
    return re.sub(r"[^a-z0-9]+", "", text)


def same_variable(variable, row):
    left, right = variable_key(variable), variable_key(row)
    return bool(left and left == right)


def requested_table_variables(work):
    """Extract explicit scientific symbols requested for a table comparison."""
    context_path = work / "case_context.json"
    if not context_path.is_file():
        return [], 0
    context = read(context_path)
    task_path = Path(str(context.get("task_path", "")))
    if not task_path.is_file():
        return [], 0
    task = task_path.read_text(encoding="utf-8-sig")
    if not re.search(r"table|表格|结果表|主表", task, flags=re.IGNORECASE):
        return [], 0
    comparison = re.search(r"(?:对比|比较|\bcompare\b)\s*([^。；;\n]+)", task, re.IGNORECASE)
    if not comparison:
        return [], 0
    clause = re.split(r"，|\b(?:with units|including|and report)\b", comparison[1], maxsplit=1,
                      flags=re.IGNORECASE)[0]
    pattern = r"(?<![A-Za-z0-9])(?:[A-Za-zΑ-ωΩΩ][A-Za-zΑ-ωΩΩ₀-₉ₐ-ₜ⁰-⁹0-9_^.]*)"
    explicit = []
    for token in re.findall(pattern, clause):
        # Require mathematical notation, excluding prose acronyms such as PDF.
        if not re.search(r"[₀-₉ₐ-ₜ⁰-⁹0-9]", token):
            continue
        key = variable_key(token)
        if key and all(key != variable_key(item) for item in explicit):
            explicit.append(token)
    minimum = 2 if re.search(r"两列|2\s*列|\btwo\s+(?:different\s+)?columns\b", task, re.IGNORECASE) else 1
    return explicit, minimum


def locate(work, terms, limit):
    pages, _ = core.read_jsonl(work / "pages.jsonl")
    terms = [folded(term) for term in terms if term.strip()]
    matches = []
    for row in pages:
        text = row["raw_text"]
        searchable = folded(text)
        hits = [term for term in terms if term in searchable]
        if hits:
            snippets = []
            for line in text.splitlines():
                normalized = folded(line)
                positions = [normalized.find(term) for term in hits if term in normalized]
                if positions:
                    at = min(positions)
                    start = max(0, at - 100)
                    snippets.append(normalized[start:start + 240])
            matches.append({"pdf_page": row["pdf_page"], "terms": hits,
                            "matches": snippets[:8]})
    return sorted(matches, key=lambda hit: (-len(hit["terms"]), hit["pdf_page"]))[:limit]


def outline_candidates(work):
    document = read(work / "sections.json")
    pages, _ = core.read_jsonl(work / "pages.jsonl")
    by_page = {row["pdf_page"]: row["raw_text"] for row in pages}
    for bookmark in core.pdf_outline_titles(document):
        page = bookmark["pdf_page"]
        title = folded(bookmark["title"])
        candidates = []
        words = re.findall(r"[^\W_]+", title)
        separator = r"[\s\x00-\x1f‐‑–—-]+"
        # Some fallback pages merge a heading with the adjacent column.
        # Require a visible section number of the bookmark's depth.
        title_pattern = None
        if len(words) >= 3:
            number = r"[1-9]\d*" + r"(?:\.\d+)" * (bookmark["level"] - 1)
            title_pattern = re.compile(rf"(?<![\w.]){number}\.?\s+"
                                       + separator.join(re.escape(word) for word in words)
                                       + r"(?!\w)", re.IGNORECASE)
        for neighbor in range(max(1, page - 2), page + 3):
            for line in by_page.get(neighbor, "").splitlines():
                line = line.strip()
                without_number = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", folded(line))
                if without_number == title:
                    candidates.append({"pdf_page": neighbor, "quote": line, "match": "whole_line"})
                elif title_pattern:
                    match = title_pattern.search(line)
                    if match:
                        candidates.append({"pdf_page": neighbor, "quote": match.group(),
                                           "match": "numbered_text_needs_visual_review"})
        yield {"bookmark_title": bookmark["title"], "bookmark_page_hint": page,
               "level": bookmark["level"], "matching_text_lines": candidates}


def source_line(label, cells):
    return folded(" ".join([label, *cells]))


def table_value(binding, pages):
    """Validate a whole ordered row and header against one identified source block."""
    required = {"claim_id", "pdf_page", "table", "parameter_header", "columns", "variable",
                "row", "cells", "column", "value", "unit"}
    if not isinstance(binding, dict) or not required <= binding.keys():
        raise ValueError("table evidence is missing required fields")
    for field in required - {"pdf_page", "columns", "cells"}:
        if not isinstance(binding[field], str) or not binding[field].strip():
            raise ValueError(f"{field} must be a nonempty string")
    columns, cells = binding["columns"], binding["cells"]
    if not isinstance(columns, list) or not isinstance(cells, list) or not columns or len(columns) != len(cells):
        raise ValueError("table header and row must have the same nonzero number of data columns")
    if any(not isinstance(value, str) or not value.strip() for value in columns + cells):
        raise ValueError("columns and cells require nonempty transcriptions")
    normalized_columns = [folded(column) for column in columns]
    if len(set(normalized_columns)) != len(columns) or folded(binding["column"]) not in normalized_columns:
        raise ValueError("target column is absent or ambiguous")
    column_index = normalized_columns.index(folded(binding["column"]))
    if folded(cells[column_index]) != folded(binding["value"]):
        raise ValueError(f"value does not match the cell in column {binding['column']!r}")
    if not same_variable(binding["variable"], binding["row"]):
        raise ValueError("target variable does not exactly match the bound row expression")

    page = binding["pdf_page"]
    if type(page) is not int or page not in pages:
        raise ValueError("invalid PDF page")
    if binding.get("source") == "rendered_page":
        return "visual_review_required"
    raw = pages[page]
    marker = folded(binding["table"])
    if not re.match(r"^(?:table|表)\s*(?:[a-z]\.)?\d+\b", marker):
        raise ValueError("table label must identify a numbered table")
    header = source_line(binding["parameter_header"], columns)
    row = source_line(binding["row"], cells)
    # Preserve row identity and ordering. A number anywhere on the page is insufficient.
    matches = [item for item in re.finditer(r"(?:\btable|表)\s*(?:[a-z]\.)?\d+\b", folded(raw))]
    text = folded(raw)
    for index, match in enumerate(matches):
        if re.sub(r"\s+", "", match.group()) != re.sub(r"\s+", "", marker):
            continue
        stop = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        segment = text[match.end():stop]
        header_at = segment.find(header)
        if header_at >= 0 and segment.find(row, header_at + len(header)) >= 0:
            return "ordered_text_match"
    raise ValueError("ordered header and row could not be located under the table label; use a rendered-page review")


def figure_value(binding, candidate, pages, work):
    required = {"claim_id", "pdf_page", "figure", "x_axis", "y_axis", "source"}
    if not isinstance(binding, dict) or not required <= binding.keys():
        raise ValueError("figure evidence is missing required fields")
    for field in required - {"pdf_page"}:
        if not isinstance(binding[field], str) or not binding[field].strip():
            raise ValueError(f"{field} must be a nonempty string")
    page = binding["pdf_page"]
    if type(page) is not int or page not in pages:
        raise ValueError("invalid PDF page")
    if not re.match(r"^(?:fig(?:ure)?\.?|图)\s*[a-z]?\d+\b", folded(binding["figure"])):
        raise ValueError("figure label must identify a numbered figure")
    claim = resolve_pointer(candidate, binding["claim_id"])
    claim_text = json.dumps(claim, ensure_ascii=False)
    for axis in ("x_axis", "y_axis"):
        if variable_key(binding[axis]) not in variable_key(claim_text):
            raise ValueError(f"contract claim does not preserve {axis}")
    if same_variable(binding["x_axis"], binding["y_axis"]):
        raise ValueError("x_axis and y_axis must be distinct")
    if binding["source"] == "rendered_page":
        review = binding.get("visual_review")
        if (not (work / "rendered" / f"page_{page:04d}.png").is_file()
                or not isinstance(review, dict) or review.get("reviewed") is not True
                or not review.get("reviewer") or not review.get("rationale")):
            raise ValueError("rendered figure binding requires a real page and recorded visual review")
        return "visually_reviewed_axes"
    if binding["source"] not in {"text_layer", "ocr"}:
        raise ValueError("source must be text_layer, ocr, or rendered_page")
    quotes = binding.get("axis_quotes")
    if not isinstance(quotes, dict):
        raise ValueError("text/OCR figure binding requires axis_quotes")
    page_text = folded(pages[page])
    for axis in ("x_axis", "y_axis"):
        quote = quotes.get(axis)
        if not isinstance(quote, str) or not quote.strip() or folded(quote) not in page_text:
            raise ValueError(f"{axis} quote is not found on the declared page")
        if not same_variable(binding[axis], quote):
            raise ValueError(f"{axis} does not exactly match its quoted axis expression")
    return "text_axes_only_no_visual_trend"


def resolve_pointer(document, pointer):
    if not pointer.startswith("/"):
        raise ValueError("claim_id must be a JSON Pointer into contract_values.json")
    try:
        for token in pointer[1:].split("/"):
            key = token.replace("~1", "/").replace("~0", "~")
            if isinstance(document, list):
                if not key.isdecimal():
                    raise ValueError("invalid array index")
                document = document[int(key)]
            else:
                document = document[key]
    except (IndexError, KeyError, TypeError) as exc:
        raise ValueError(f"claim_id does not resolve: {pointer}") from exc
    return document


def inspect_case(work, case, candidate):
    if not isinstance(candidate, dict):
        return {"errors": ["contract_values.json must be a JSON object"], "tables": []}
    schema = core.discover_case_contract(case)
    errors = core.contract_errors(candidate, schema, "result")
    try:
        import jsonschema
    except ImportError:
        engine = "bundled"
        def check_schema(node, value, path):
            if not isinstance(node, dict):
                if isinstance(node, bool):
                    errors.append(f"{path}: schema requires jsonschema for Boolean subschemas")
                return
            advanced = {"$ref", "$dynamicRef", "allOf", "oneOf", "anyOf", "not", "if",
                        "patternProperties", "unevaluatedProperties", "dependentSchemas",
                        "prefixItems", "contains", "pattern", "format", "maximum", "minimum",
                        "maxLength", "minLength", "uniqueItems", "maxItems"}
            needed = sorted(advanced.intersection(node))
            if needed:
                errors.append(f"{path}: schema keywords need jsonschema: {', '.join(needed)}")
            if isinstance(value, dict):
                properties = core.contract_properties(node)
                if node.get("additionalProperties") is False:
                    for key in sorted(value.keys() - properties.keys()):
                        errors.append(f"{path}: unexpected field {key}")
                for key in value.keys() & properties.keys():
                    check_schema(properties[key], value[key], f"{path}.{key}")
            if isinstance(value, list) and "items" in node:
                for index, item in enumerate(value):
                    check_schema(node["items"], item, f"{path}[{index}]")
        check_schema(schema, candidate, "result")
    else:
        engine = "jsonschema+bundled"
        if isinstance(schema, dict):
            validator = jsonschema.validators.validator_for(schema)(schema)
            errors.extend(f"result{error.json_path[1:]}: {error.message}"
                          for error in validator.iter_errors(candidate))
    rows, _ = core.read_jsonl(work / "pages.jsonl")
    pages = {row["pdf_page"]: row["raw_text"] for row in rows}
    binding_path = work / "table_bindings.json"
    tables, figures = [], []
    if binding_path.is_file():
        bindings = read(binding_path)
        if not isinstance(bindings, list):
            errors.append("table_bindings.json must be an array")
        else:
            seen = set()
            for index, binding in enumerate(bindings):
                try:
                    status = table_value(binding, pages)
                    claim = binding["claim_id"]
                    value = resolve_pointer(candidate, claim)
                    if not isinstance(value, (str, int, float)) or folded(value) != folded(binding["value"]):
                        raise ValueError(f"contract value at {claim} differs from the verified cell")
                    if claim in seen:
                        raise ValueError("duplicate claim_id")
                    if status == "visual_review_required":
                        page = binding["pdf_page"]
                        review = binding.get("visual_review")
                        if (not (work / "rendered" / f"page_{page:04d}.png").is_file()
                                or not isinstance(review, dict) or not review.get("reviewer")
                                or not review.get("rationale")):
                            raise ValueError("visual binding requires a real page fallback and recorded review")
                        status = "visually_reviewed_transcription"
                    seen.add(claim)
                    tables.append({**binding, "status": status})
                except ValueError as exc:
                    errors.append(f"table_bindings[{index}]: {exc}")
    serialized = json.dumps(candidate, ensure_ascii=False)
    if re.search(r"(?:\btable\s*\d+|表\s*\d+)", serialized, re.IGNORECASE) and not tables:
        errors.append("quantitative table claims require verified table_bindings.json entries")
    requested_variables, minimum = requested_table_variables(work)
    for variable in requested_variables:
        columns = {folded(row["column"]) for row in tables if same_variable(variable, row["variable"])}
        if len(columns) < minimum:
            errors.append(f"requested table comparison for {variable} requires {minimum} verified column(s) of its exact row")
    figure_path = work / "figure_bindings.json"
    if figure_path.is_file():
        bindings = read(figure_path)
        if not isinstance(bindings, list):
            errors.append("figure_bindings.json must be an array")
        else:
            seen = set()
            for index, binding in enumerate(bindings):
                try:
                    status = figure_value(binding, candidate, pages, work)
                    if binding["claim_id"] in seen:
                        raise ValueError("duplicate claim_id")
                    seen.add(binding["claim_id"])
                    figures.append({**binding, "status": status})
                except ValueError as exc:
                    errors.append(f"figure_bindings[{index}]: {exc}")
    if re.search(r"(?:\bfig(?:ure)?\.?\s*\d+|图\s*\d+)", serialized, re.IGNORECASE) and not figures:
        errors.append("figure claims require verified figure_bindings.json entries with both axes")
    return {"schema_engine": engine, "errors": list(dict.fromkeys(errors)),
            "tables": tables, "figures": figures}


def unfreeze_partial_state(work):
    state = read(work / "run_state.json")
    state.pop("finished_at", None)
    state.pop("elapsed_seconds", None)
    core.write_json_atomic(work / "run_state.json", state)


def _title_key(value):
    return re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", folded(value)).rstrip(".。:：")


def _outline_titles_for_page(sections_doc, page):
    """Return plausible top-level outline owners for a PDF page.

    Journal outline destinations often point one PDF page before the printed
    heading.  On the boundary both adjacent sections are allowed.
    """
    top = sorted((item for item in core.pdf_outline_titles(sections_doc)
                  if item.get("level") == 1), key=lambda item: item.get("pdf_page", 0))
    starts = [(item.get("pdf_page", 0) + 1, item) for item in top]
    current = None
    for index, (start, _) in enumerate(starts):
        if start > page:
            break
        current = index
    if current is None:
        return []
    candidates = [starts[current][1]]
    if page == starts[current][0] and current:
        candidates.append(starts[current - 1][1])
    return candidates


def repair_or_reject_note_sections(work, notes):
    """Auto-repair a unique outline match; reject unresolved wrong placement."""
    sections_doc = read(work / "sections.json")
    sections = sections_doc.get("sections", [])
    by_id = {row.get("section_id"): row for row in sections}
    by_title = {}
    for row in sections:
        by_title.setdefault(_title_key(row.get("title", "")), []).append(row.get("section_id"))
    repaired = []
    for original in notes:
        note = dict(original)
        assigned = by_id.get(note.get("section_id"))
        pages = sorted({anchor.get("pdf_page") for anchor in note.get("anchors", [])
                        if isinstance(anchor, dict) and isinstance(anchor.get("pdf_page"), int)})
        if not assigned or not pages:
            repaired.append(note)
            continue
        assigned_title = _title_key(assigned.get("title", ""))
        if assigned_title in {"abstract", "front matter (before first detected heading)", "references"}:
            repaired.append(note)
            continue
        expected = {_title_key(item.get("title", ""))
                    for page in pages for item in _outline_titles_for_page(sections_doc, page)}
        expected.discard("")
        if expected and assigned_title not in expected:
            matches = list(dict.fromkeys(section_id for title in expected
                                         for section_id in by_title.get(title, [])))
            if len(matches) == 1:
                note["section_id"] = matches[0]
                note["section_auto_repair"] = {
                    "from": original.get("section_id"), "to": matches[0],
                    "basis": "unique top-level PDF outline owner of the evidence page",
                }
            else:
                raise ValueError(
                    f"{note.get('note_id')}: evidence belongs under outline title(s) "
                    f"{sorted(expected)}, not {assigned.get('title')!r}; repair sections.json before checkpoint"
                )
        repaired.append(note)
    return repaired


def validate_table_binding_set(work, candidate, bindings):
    pages_rows, _ = core.read_jsonl(work / "pages.jsonl")
    pages = {row["pdf_page"]: row["raw_text"] for row in pages_rows}
    seen = set()
    for index, binding in enumerate(bindings):
        status = table_value(binding, pages)
        claim = binding["claim_id"]
        if claim in seen:
            raise ValueError(f"table_bindings[{index}]: duplicate claim_id")
        seen.add(claim)
        value = resolve_pointer(candidate, claim)
        if not isinstance(value, (str, int, float)) or folded(value) != folded(binding["value"]):
            raise ValueError(
                f"table_bindings[{index}]: claim_id must resolve to exactly one scalar cell; "
                "split multi-variable or multi-cell claims before checkpoint"
            )
        if status == "visual_review_required":
            page = binding["pdf_page"]
            review = binding.get("visual_review")
            if (not (work / "rendered" / f"page_{page:04d}.png").is_file()
                    or not isinstance(review, dict) or review.get("reviewed") is not True
                    or not review.get("reviewer") or not review.get("rationale")):
                raise ValueError(f"table_bindings[{index}]: rendered row needs a real page and visual review")


def merge_batch(work, batch_path):
    batch = read(batch_path)
    allowed = {"notes", "contract", "paper_card", "uncertainty", "table_bindings", "figure_bindings"}
    if not isinstance(batch, dict) or set(batch) - allowed:
        raise ValueError(f"batch must be an object containing only {sorted(allowed)}")
    notes, _ = core.read_jsonl(work / "notes.jsonl")
    by_id = {row.get("note_id"): row for row in notes if isinstance(row, dict)}
    proposed_notes = batch.get("notes", [])
    if not isinstance(proposed_notes, list):
        raise ValueError("notes must be an array")
    proposed_notes = repair_or_reject_note_sections(work, proposed_notes)
    for note in proposed_notes:
        if not isinstance(note, dict) or not note.get("note_id"):
            raise ValueError("every batched note requires note_id")
        by_id[note["note_id"]] = note
    preview_objects = {}
    for key, filename in (("contract", "contract_values.json"), ("paper_card", "paper_card.json")):
        current = read(work / filename)
        if key in batch:
            patch = batch[key]
            if not isinstance(patch, dict):
                raise ValueError(f"{key} must be an object")
            current.update(patch)
        preview_objects[key] = current
    if "uncertainty" in batch:
        current = read(work / "uncertainty.json")
        additions = batch["uncertainty"]
        if not isinstance(additions, list):
            raise ValueError("uncertainty must be an array")
        unique = {json.dumps(row, ensure_ascii=False, sort_keys=True): row for row in [*current, *additions]}
        preview_uncertainty = list(unique.values())
    else:
        preview_uncertainty = read(work / "uncertainty.json")
    preview_bindings = {}
    for key, filename in (("table_bindings", "table_bindings.json"),
                          ("figure_bindings", "figure_bindings.json")):
        current = read(work / filename) if (work / filename).is_file() else []
        if key in batch:
            additions = batch[key]
            if not isinstance(additions, list):
                raise ValueError(f"{key} must be an array")
            by_claim = {row.get("claim_id"): row for row in current if isinstance(row, dict)}
            for row in additions:
                if not isinstance(row, dict) or not row.get("claim_id"):
                    raise ValueError(f"every {key} entry requires claim_id")
                by_claim[row["claim_id"]] = row
            current = list(by_claim.values())
        preview_bindings[key] = current

    # Transactional preflight: no contract, note or binding is written until the
    # entire proposed batch has passed the scientific binding and section gates.
    validate_table_binding_set(work, preview_objects["contract"], preview_bindings["table_bindings"])

    if "notes" in batch:
        core.write_text_atomic(work / "notes.jsonl", "".join(
            json.dumps(row, ensure_ascii=False) + "\n" for row in by_id.values()))
    for key, filename in (("contract", "contract_values.json"), ("paper_card", "paper_card.json")):
        if key in batch:
            core.write_json_atomic(work / filename, preview_objects[key])
    if "uncertainty" in batch:
        core.write_json_atomic(work / "uncertainty.json", preview_uncertainty)
    for key, filename in (("table_bindings", "table_bindings.json"),
                          ("figure_bindings", "figure_bindings.json")):
        if key in batch:
            core.write_json_atomic(work / filename, preview_bindings[key])


def checkpoint(work, output, case_path, complete=False, finalize=False):
    """Publish a recoverable snapshot even when contract/evidence checks fail."""
    report = inspect_case(work, read(case_path), read(work / "contract_values.json"))
    core.write_json_atomic(work / "acceptance.json", report)
    try:
        # The assembler already validates notes; do not validate the whole PDF twice.
        with contextlib.redirect_stdout(io.StringIO()):
            core.command_assemble(SimpleNamespace(case=str(case_path), work=str(work),
                output=str(output), run_status="auto", request_success=complete and not report["errors"],
                status_reason="", lang="zh"))
    except SystemExit as exc:
        if exc.code != 2:
            raise
    if not finalize:
        unfreeze_partial_state(work)
    validation = read(output / "validation_report.json")
    report["note_errors"] = validation["errors"]
    report["note_warnings"] = validation["warnings"]
    report["counts"] = validation["counts"]
    report["complete"] = read(output / "result.json")["status"] == "success"
    core.write_json_atomic(work / "acceptance.json", report)
    core.write_json_atomic(output / "acceptance.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "index", "find", "read", "check", "checkpoint", "finish"))
    parser.add_argument("--input", type=Path, default=Path("/input"))
    parser.add_argument("--work", type=Path, default=Path("/app/work"))
    parser.add_argument("--output", type=Path, default=Path("/app/output"))
    parser.add_argument("--pdf", action="append", default=[])
    parser.add_argument("--term", action="append", default=[])
    parser.add_argument("--complete", action="store_true")
    parser.add_argument("--page", type=int, action="append", default=[])
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--max-chars", type=int, default=12000)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--values", type=Path, help="JSON object of reviewed top-level contract fields to merge at checkpoint")
    parser.add_argument("--batch", type=Path, help="small JSON batch of notes, contract fields and evidence bindings")
    args = parser.parse_args()
    case_path = args.input / "case.json"
    if args.command == "prepare":
        core.command_init(SimpleNamespace(case=str(case_path), input_dir=str(args.input),
                          task=str(args.input / "task.md"), pdf=args.pdf,
                          work=str(args.work), output=str(args.output)))
        case = read(case_path)
        core.write_json_atomic(args.work / "contract.json", core.discover_case_contract(case))
        core.command_assemble(SimpleNamespace(case=str(case_path), work=str(args.work),
                              output=str(args.output), run_status="partial", request_success=False,
                              status_reason="初始化完成，证据与答案待提取。", lang="zh"))
        unfreeze_partial_state(args.work)
    elif args.command == "index":
        output = list(outline_candidates(args.work))
        core.write_json_atomic(args.work / "outline_candidates.json", output)
        print(json.dumps({"bookmark_candidates": len(output)}, ensure_ascii=False))
    elif args.command == "find":
        if not args.term:
            parser.error("find requires at least one --term")
        print(json.dumps(locate(args.work, args.term, max(1, min(args.limit, 20))), ensure_ascii=False))
    elif args.command == "read":
        if not args.page or args.offset < 0:
            parser.error("read requires --page and nonnegative --offset")
        pages, _ = core.read_jsonl(args.work / "pages.jsonl")
        selected = {row["pdf_page"]: row["raw_text"] for row in pages}
        budget = max(1, min(args.max_chars, 24000))
        result = []
        for page in dict.fromkeys(args.page):
            if page not in selected:
                parser.error(f"unknown page: {page}")
            raw = selected[page]
            excerpt = raw[args.offset:args.offset + budget]
            result.append({"pdf_page": page, "text": excerpt,
                           "next_offset": args.offset + len(excerpt) if args.offset + len(excerpt) < len(raw) else None})
            budget -= len(excerpt)
        print(json.dumps(result, ensure_ascii=False))
    else:
        if args.batch:
            if args.command != "checkpoint":
                parser.error("--batch is only supported by checkpoint")
            try:
                merge_batch(args.work, args.batch)
            except ValueError as exc:
                parser.error(str(exc))
        if args.values:
            if args.command != "checkpoint":
                parser.error("--values is only supported by checkpoint")
            patch = read(args.values)
            if not isinstance(patch, dict):
                parser.error("--values must contain an object")
            values = read(args.work / "contract_values.json")
            values.update(patch)
            core.write_json_atomic(args.work / "contract_values.json", values)
        report = checkpoint(args.work, args.output, case_path, args.complete,
                            finalize=args.command == "finish")
        print(json.dumps({"complete": report["complete"], "errors": len(report["errors"]),
            "note_errors": len(report["note_errors"]), "note_warnings": len(report["note_warnings"]),
            "counts": report["counts"], "details": str(args.output / "acceptance.json")}, ensure_ascii=False))
        if (args.complete and not report["complete"]) or (args.command == "check" and
                (report["errors"] or report["note_errors"] or report["note_warnings"])):
            raise SystemExit(2)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
