"""Adapt Host parser JSONL records to the evidence-linked extraction schemas.

The adapter deliberately does not infer bounding boxes, font properties, printed page
numbers, or image coverage.  Heading detection is conservative and every inferred
section boundary remains subject to semantic review.
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


_NONHEADING_KINDS = {
    "figure",
    "caption",
    "table",
    "equation",
    "header",
    "footer",
    "document_title",
}
_HOST_HEADING_KINDS = {"title", "heading", "section_title"}
_AXIS_LABELS = {
    "x",
    "y",
    "x axis",
    "y axis",
    "x-axis",
    "y-axis",
    "document",
    "documents",
    "query",
    "queries",
    "score",
    "scores",
    "similarity",
    "rank",
    "relevance",
    "latency",
    "time",
    "mrr",
    "map",
    "flops",
    "throughput",
}
_ENGLISH_HEADINGS = {
    "abstract",
    "introduction",
    "related work",
    "background",
    "method",
    "methods",
    "methodology",
    "approach",
    "model",
    "models",
    "experiment",
    "experiments",
    "experimental evaluation",
    "experimental settings",
    "evaluation",
    "results",
    "results and discussion",
    "discussion",
    "conclusion",
    "conclusions",
    "references",
    "bibliography",
    "acknowledgment",
    "acknowledgments",
    "acknowledgement",
    "acknowledgements",
    "appendix",
    "appendices",
    "supplementary material",
    "broader impact",
    "broader impacts",
    "limitations",
    "limitation",
    "ethics statement",
    "funding",
    "author contributions",
    "data availability",
    "code availability",
    "conflict of interest",
    "reproducibility statement",
}
_CHINESE_HEADINGS = (
    "摘要",
    "引言",
    "绪论",
    "相关工作",
    "方法",
    "实验",
    "结果",
    "讨论",
    "结论",
    "参考文献",
    "致谢",
    "附录",
)
_SENTENCE_PUNCTUATION = ".!?。！？:：;；"
_NUMBER_ONLY_RE = re.compile(r"^[1-9]\d?(?:\.\d+)*\.?$")
_NUMBERED_RE = re.compile(
    r"^(?P<num>[1-9]\d?(?:\.\d+)*)(?:[ \t]+|[ \t]*\.[ \t]+)(?P<rest>\S.*)$"
)
_CHINESE_PREFIX_RE = re.compile(
    r"^(?:(?:第[一二三四五六七八九十百零〇0-9]+[章节篇部])|"
    r"(?:[一二三四五六七八九十]+[、．.])|"
    r"(?:\d+(?:\.\d+)*[、．.]?))[ \t]*(?P<name>.+)$"
)
# Letter-prefixed appendix headings such as "A Implementation Details" or "A. Data".
# The prefix is only a candidate: acceptance additionally requires a consecutive
# A, B, C ... chain (or a References/Appendix heading immediately before it).
_LETTER_HEADING_RE = re.compile(r"^(?P<letter>[A-Z])(?:[.)][ \t]*)?[ \t]+(?P<title>\S.*)$")
_SENTENCE_START_WORDS = {
    "a", "an", "the", "we", "our", "this", "these", "those", "it", "its", "in", "on",
    "for", "as", "at", "by", "from", "with", "there", "here", "note", "table", "figure",
    "however", "although", "moreover", "furthermore", "such", "each", "all", "both",
}
_REFERENCES_TITLES = {"references", "bibliography", "参考文献", "works cited"}


@dataclass(frozen=True)
class _Heading:
    title: str
    quote: str
    level: int
    source: str
    combined: bool


def discover_host_parser(pdf_path: Path, explicit: str | None = None) -> Path | None:
    """Locate ``.parser/<pdf stem>.jsonl`` without reading or changing the PDF.

    An explicit path takes precedence and must exist.  Automatic discovery checks
    the PDF's parent, five further ancestors, and finally the current working
    directory.  ``None`` means that automatic discovery found no parser file.
    """

    if explicit is not None:
        candidate = Path(explicit).expanduser().resolve()
        if not candidate.is_file():
            raise FileNotFoundError(f"Explicit Host parser JSONL not found: {candidate}")
        return candidate

    pdf = Path(pdf_path).expanduser().resolve()
    start = pdf.parent
    search_directories = [start, *list(start.parents)[:5], Path.cwd().resolve()]
    seen: set[Path] = set()
    for directory in search_directories:
        if directory in seen:
            continue
        seen.add(directory)
        candidate = directory / ".parser" / f"{pdf.stem}.jsonl"
        if candidate.is_file():
            return candidate.resolve()
    return None


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).replace("\u00ad", "")
    normalized = re.sub(
        r"(?<=[A-Za-z])-[ \t]*\r?\n[ \t]*(?=[A-Za-z])", "", normalized
    )
    return re.sub(r"\s+", " ", normalized).strip()


def _clean_title(quote: str) -> str:
    return _normalize_text(quote).rstrip(_SENTENCE_PUNCTUATION).strip()


def _casefold_title(quote: str) -> str:
    return _clean_title(quote).casefold()


def _is_axis_label(text: str) -> bool:
    normalized = _casefold_title(text)
    return len(normalized) <= 48 and normalized in _AXIS_LABELS


def _is_caption_like(text: str) -> bool:
    return bool(re.match(r"^\s*(?:fig(?:ure)?\.?|table)\s+[A-Z0-9]+\b", text, re.I))


def _plausible_numbered_title(fragment: str) -> bool:
    title = _clean_title(fragment)
    if not title or len(title) > 180 or len(title.split()) > 24:
        return False
    match = _NUMBERED_RE.match(title)
    if not match:
        return False
    remainder = _clean_title(match.group("rest"))
    words = remainder.split()
    if remainder[:1].islower() and (
        len(words) > 6 or (words and words[0].casefold() in {"epoch", "epochs", "sample", "samples", "step", "steps", "second", "seconds", "minute", "minutes", "hour", "hours", "day", "days", "time", "times", "passage", "passages"})
    ):
        return False
    return bool(remainder)


def _numbered_level(fragment: str) -> int:
    match = re.match(r"^\s*([1-9]\d*(?:\.\d+)*)", fragment)
    if not match:
        return 1
    return match.group(1).count(".") + 1


def _named_heading_kind(fragment: str) -> str | None:
    folded = _casefold_title(fragment)
    if folded in _ENGLISH_HEADINGS:
        return "english"
    for name in _CHINESE_HEADINGS:
        if folded == name:
            return "chinese"
        match = _CHINESE_PREFIX_RE.match(_clean_title(fragment))
        if match and match.group("name").strip() == name:
            return "chinese"
    return None


def _first_line(parts: list[str]) -> str:
    return parts[0].rstrip("\r\n").strip() if parts else ""


def _numbered_heading(candidate: str) -> _Heading | None:
    lines = candidate.splitlines(keepends=True)
    first = _first_line(lines)
    match = _NUMBERED_RE.match(first)
    quote = ""
    combined = False

    if match:
        rest = match.group("rest")
        # A same-line period followed by a space separates a heading that has
        # been merged with its body.  Keep the period in the exact quote.
        body_separator = re.search(r"[.!?。！？][ \t]+\S", rest)
        if body_separator:
            separator_start = body_separator.start()
            punctuation_end = separator_start + 1
            absolute_end = match.start("rest") + punctuation_end
            quote = first[:absolute_end]
            combined = True
        else:
            quote = first
            combined = len(lines) > 1 and any(line.strip() for line in lines[1:])
    elif _NUMBER_ONLY_RE.fullmatch(first) and len(lines) > 1:
        second = lines[1].rstrip("\r\n")
        second_semantic = second.strip()
        if (
            not second_semantic or second_semantic.endswith((".", "。", ";", "；"))
            or re.match(r"(?:https?://|www\.)", second_semantic, re.IGNORECASE)
            or len(second_semantic) > 160
        ):
            return None
        # Preserve the actual second-line indentation in the evidence quote,
        # while leaving subsequent lines as body in the unchanged source block.
        quote = lines[0] + second
        combined = any(line.strip() for line in lines[2:])

    if not quote or not _plausible_numbered_title(quote):
        return None
    return _Heading(
        title=_clean_title(quote),
        quote=quote,
        level=_numbered_level(quote),
        source="inline_numbered_heading" if combined else "numbered_heading",
        combined=combined,
    )


def _named_heading(candidate: str) -> _Heading | None:
    kind = _named_heading_kind(candidate)
    if kind is not None:
        return _Heading(
            title=_clean_title(candidate),
            quote=candidate,
            level=1,
            source="unnumbered_named_heading",
            combined=False,
        )

    lines = candidate.splitlines(keepends=True)
    first = _first_line(lines)
    if len(lines) > 1 and any(line.strip() for line in lines[1:]):
        kind = _named_heading_kind(first)
        if kind is not None:
            return _Heading(
                title=_clean_title(first),
                quote=first,
                level=1,
                source="inline_named_heading",
                combined=True,
            )

    # Named headings may also be merged into the first body sentence, for
    # example "Acknowledgments. We thank ..." or "摘要：本文……".
    separator = re.search(r"[.!?。！？:：][ \t]*\S", candidate)
    if separator and separator.start() > 0:
        punctuation_end = separator.start() + 1
        quote = candidate[:punctuation_end]
        kind = _named_heading_kind(quote)
        if kind is not None:
            return _Heading(
                title=_clean_title(quote),
                quote=quote,
                level=1,
                source="inline_named_heading",
                combined=True,
            )
    return None


def _host_kind_heading(candidate: str) -> _Heading | None:
    lines = candidate.splitlines(keepends=True)
    first = _first_line(lines)
    if len(lines) > 1 and any(line.strip() for line in lines[1:]) and first:
        return _Heading(
            title=_clean_title(first),
            quote=first,
            level=1,
            source="host_kind_inline_heading",
            combined=True,
        )
    return _Heading(
        title=_clean_title(candidate),
        quote=candidate,
        level=1,
        source="host_kind_heading",
        combined=False,
    )


def named_heading_title(text: str) -> str | None:
    """Public helper: return the cleaned title when ``text`` is a known named heading."""

    kind = _named_heading_kind(text)
    return _clean_title(text) if kind is not None else None


def letter_heading_shape(raw_text: str) -> tuple[str, str, str] | None:
    """Return (letter, title, exact quote) for an appendix-style letter heading.

    This is a shape-only candidate test.  Acceptance also requires the consecutive
    ``A, B, C ...`` chain enforced by :func:`accept_letter_chain`, so a body sentence
    starting with "A ..." cannot become a section on its own.
    """

    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    first = lines[0]
    if len(first) > 90:
        return None
    match = _LETTER_HEADING_RE.match(first)
    if not match:
        return None
    letter, title = match.group("letter"), match.group("title").strip()
    if not title or title.endswith((".", "。", ";", "；", ",", "，", ":")):
        return None
    if len(title.split()) > 9:
        return None
    if len(re.findall(r"[A-Za-z\u3400-\u9fff]", title)) < 4:
        return None
    if not title[0].isupper():
        return None
    if title.split()[0].casefold() in _SENTENCE_START_WORDS:
        return None
    if title.lower().startswith(("http://", "https://", "www.")):
        return None
    return letter, title, first


def accept_letter_chain(
    candidates: list[tuple[str, Any]], references_before: bool = False, min_chain: int = 3
) -> list[Any]:
    """Accept only a consecutive letter chain, optionally a short one after References."""

    accepted: list[Any] = []
    expected = "A"
    for letter, payload in candidates:
        if letter != expected:
            continue
        accepted.append(payload)
        expected = chr(ord(expected) + 1) if expected != "Z" else "Z"
    threshold = 2 if references_before else min_chain
    return accepted if len(accepted) >= threshold else []


def _detect_heading(kind: str, text: str) -> _Heading | None:
    if kind in _NONHEADING_KINDS or kind not in (_HOST_HEADING_KINDS | {"text"}):
        return None
    candidate = text.strip()
    if not candidate or _is_axis_label(candidate) or _is_caption_like(candidate):
        return None

    heading = _numbered_heading(candidate) or _named_heading(candidate)
    if heading is not None:
        return heading
    if kind in _HOST_HEADING_KINDS:
        heading = _host_kind_heading(candidate)
        if heading.title and not _is_axis_label(heading.quote):
            return heading
    return None


def _load_records(parser_path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with parser_path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid Host parser JSON at {parser_path}:{line_number}: {exc.msg}"
                ) from exc
            if not isinstance(value, dict):
                raise ValueError(
                    f"Host parser record at {parser_path}:{line_number} is not an object"
                )
            page = value.get("page")
            kind = value.get("kind")
            text = value.get("text")
            if isinstance(page, bool) or not isinstance(page, int) or page < 1:
                raise ValueError(
                    f"Invalid page at {parser_path}:{line_number}; expected a positive integer"
                )
            if not isinstance(kind, str) or not kind:
                raise ValueError(
                    f"Invalid kind at {parser_path}:{line_number}; expected a nonempty string"
                )
            if not isinstance(text, str):
                raise ValueError(
                    f"Invalid text at {parser_path}:{line_number}; expected a string"
                )
            figure_paths = value.get("figurePaths", [])
            if not isinstance(figure_paths, list) or not all(
                isinstance(item, str) for item in figure_paths
            ):
                raise ValueError(
                    f"Invalid figurePaths at {parser_path}:{line_number}; expected strings"
                )
            records.append(
                {
                    "page": page,
                    "kind": kind,
                    "text": text,
                    "resourceId": value.get("resourceId"),
                    "figurePaths": list(figure_paths),
                }
            )
    if not records:
        raise ValueError(f"Host parser JSONL contains no records: {parser_path}")
    return records


def _role(kind: str, heading: _Heading | None) -> str:
    if heading is not None:
        return "heading_and_body" if heading.combined else "heading"
    if kind in {"header", "footer"}:
        return "running_header_or_footer"
    if kind in {"figure", "caption", "table", "equation", "document_title"}:
        return kind
    return "candidate_body"


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def extract_with_host_parser(parser_path: Path, pdf_path: Path, work: Path) -> dict:
    """Write page, block, and section schemas from an existing Host JSONL file."""

    parser = Path(parser_path).expanduser().resolve()
    if not parser.is_file():
        raise FileNotFoundError(f"Host parser JSONL not found: {parser}")
    pdf = Path(pdf_path).expanduser().resolve()
    work = Path(work).expanduser().resolve()
    records = _load_records(parser)

    warnings = [
        "Section titles, levels, and boundaries are heuristic; every section needs semantic review.",
        "image_area_ratio_estimate=0.0 means unknown image coverage, not absence of images.",
    ]
    if any(record["resourceId"] is None for record in records):
        warnings.append("Some Host records have no resourceId; null was preserved.")

    heading_map: dict[int, _Heading] = {}
    for source_order, record in enumerate(records):
        heading = _detect_heading(record["kind"], record["text"])
        if heading is not None:
            heading_map[source_order] = heading
    letter_candidates: list[tuple[str, int]] = []
    for source_order, record in enumerate(records):
        if source_order in heading_map:
            continue
        if record["kind"] not in (_HOST_HEADING_KINDS | {"text"}):
            continue
        shape = letter_heading_shape(record["text"])
        if shape is not None:
            letter_candidates.append((shape[0], source_order))
    references_before = any(
        _casefold_title(heading.title) in _REFERENCES_TITLES for heading in heading_map.values()
    )
    accepted_letters = accept_letter_chain(letter_candidates, references_before=references_before)
    for source_order in accepted_letters:
        shape = letter_heading_shape(records[source_order]["text"])
        if shape is None:
            continue
        heading_map[source_order] = _Heading(
            title=_clean_title(shape[2]), quote=shape[2], level=1,
            source="letter_appendix_heading", combined=False,
        )
    if accepted_letters:
        warnings.append(
            "Letter appendix headings were accepted only as a consecutive A, B, C ... chain; "
            "each recovered heading still needs semantic review."
        )

    sections: list[dict[str, Any]] = [
        {
            "section_id": "sec_0000",
            "title": "Front matter (before first detected heading)",
            "level": 0,
            "pdf_page_start": records[0]["page"],
            "pdf_page_end": records[0]["page"],
            "heading_block_id": None,
            "segmentation_evidence": {
                "source": "implicit_front_matter",
                "needs_semantic_review": True,
            },
        }
    ]
    blocks: list[dict[str, Any]] = []
    pages: dict[int, dict[str, Any]] = {}
    page_counters: dict[int, int] = {}
    current_section_index = 0

    for source_order, record in enumerate(records):
        page_number = record["page"]
        kind = record["kind"]
        text = record["text"]
        block_number = page_counters.get(page_number, 0)
        page_counters[page_number] = block_number + 1
        block_id = f"p{page_number:04d}_b{block_number:03d}"
        heading = heading_map.get(source_order)

        if heading is not None:
            if text.find(heading.quote) < 0:
                raise AssertionError("Heading evidence quote is not an exact source fragment")
            sections.append(
                {
                    "section_id": f"sec_{len(sections):04d}",
                    "title": heading.title,
                    "level": heading.level,
                    "pdf_page_start": page_number,
                    "pdf_page_end": page_number,
                    "heading_block_id": block_id,
                    "segmentation_evidence": {
                        "source": heading.source,
                        "quote": heading.quote,
                        "needs_semantic_review": True,
                    },
                }
            )
            current_section_index = len(sections) - 1

        section_id = sections[current_section_index]["section_id"]
        sections[current_section_index]["pdf_page_end"] = page_number
        block = {
            "pdf_page": page_number,
            "kind": kind,
            "raw_text": text,
            "resourceId": record["resourceId"],
            "figurePaths": record["figurePaths"],
            "block_id": block_id,
            "reading_order": block_number,
            "source_order": source_order,
            "normalized_text": _normalize_text(text),
            "role": _role(kind, heading),
            "section_id": section_id,
        }
        blocks.append(block)

        if page_number not in pages:
            pages[page_number] = {
                "pdf_page": page_number,
                "printed_page": None,
                "block_ids": [],
                "_records": [],
            }
        pages[page_number]["block_ids"].append(block_id)
        pages[page_number]["_records"].append(record)

    page_rows: list[dict[str, Any]] = []
    for page in pages.values():
        page_number = page["pdf_page"]
        page_records = page.pop("_records")
        raw_text = "\n".join(record["text"] for record in page_records)
        normalized_text = _normalize_text(raw_text)
        needs_visual_review = any(
            record["kind"] in {"figure", "table"} or bool(record["figurePaths"])
            for record in page_records
        )
        # Host JSONL does not expose scan quality.  Only a page with absolutely
        # no extracted text provides a direct signal that OCR review is needed.
        needs_ocr_review = not any(record["text"].strip() for record in page_records)
        page.update(
            {
                "raw_char_count": len(raw_text),
                "image_area_ratio_estimate": 0.0,
                "text_path": f"pages/page_{page_number:04d}.txt",
                "raw_text": raw_text,
                "normalized_text": normalized_text,
                "needs_visual_review": needs_visual_review,
                "needs_ocr_review": needs_ocr_review,
            }
        )
        page_rows.append(page)

    sections_document = {
        "source_pdf": str(pdf),
        "method": "Host parser JSONL with conservative exact-quote heading heuristics",
        "sections": sections,
        "warnings": warnings,
    }

    pages_directory = work / "pages"
    pages_directory.mkdir(parents=True, exist_ok=True)
    _write_jsonl(work / "pages.jsonl", page_rows)
    for page in page_rows:
        (work / page["text_path"]).write_text(
            page["raw_text"], encoding="utf-8", newline="\n"
        )
    _write_jsonl(work / "blocks.jsonl", blocks)
    (work / "sections.json").write_text(
        json.dumps(sections_document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    return {
        "ok": True,
        "backend": "host_parser",
        "parser_jsonl": str(parser),
        "pdf": str(pdf),
        "page_count": len(page_rows),
        "block_count": len(blocks),
        "section_count": len(sections),
        "pages_needing_visual_review": [
            page["pdf_page"] for page in page_rows if page["needs_visual_review"]
        ],
        "pages_needing_ocr_review": [
            page["pdf_page"] for page in page_rows if page["needs_ocr_review"]
        ],
        "warnings": warnings,
    }


def self_test() -> dict[str, Any]:
    """Run a temporary end-to-end smoke test, print JSON, and return its report."""

    check_count = 0
    failures: list[str] = []

    def check(name: str, condition: bool) -> None:
        nonlocal check_count
        check_count += 1
        if not condition:
            failures.append(name)

    try:
        # Use the module directory so this command does not write outside the
        # task's permitted tree; TemporaryDirectory removes all test artifacts.
        with tempfile.TemporaryDirectory(
            prefix="parser_adapter_self_test_", dir=Path(__file__).resolve().parent
        ) as temporary:
            root = Path(temporary)
            parser = root / "host.jsonl"
            chinese = list(_CHINESE_HEADINGS)
            records = [
                {
                    "page": 1,
                    "kind": "document_title",
                    "text": "Synthetic Paper",
                    "resourceId": "title",
                    "figurePaths": [],
                },
                {
                    "page": 1,
                    "kind": "text",
                    "text": "ABSTRACT",
                    "resourceId": "abstract",
                    "figurePaths": [],
                },
                {
                    "page": 1,
                    "kind": "figure",
                    "text": "4.1.1 Not A Section",
                    "resourceId": "figure",
                    "figurePaths": ["figure.png"],
                    "max_font_size": 99,
                    "bold": True,
                },
                {
                    "page": 1,
                    "kind": "caption",
                    "text": "5 REFERENCES",
                    "resourceId": "caption",
                    "figurePaths": [],
                },
                {
                    "page": 1,
                    "kind": "heading",
                    "text": "Query",
                    "resourceId": "axis",
                    "figurePaths": [],
                    "max_font_size": 99,
                    "bold": True,
                },
                {
                    "page": 1,
                    "kind": "text",
                    "text": "4.1.1 Datasets & Metrics\nBody follows.",
                    "resourceId": "combined-newline",
                    "figurePaths": [],
                },
                {
                    "page": 2,
                    "kind": "text",
                    "text": "Body continues on the second page.",
                    "resourceId": "body-page-2",
                    "figurePaths": [],
                },
                {
                    "page": 3,
                    "kind": "text",
                    "text": "4.1.3 Hardware & Time Measurements. Body text follows.",
                    "resourceId": "combined-period",
                    "figurePaths": [],
                },
                {
                    "page": 3,
                    "kind": "text",
                    "text": "Related Work",
                    "resourceId": "english",
                    "figurePaths": [],
                },
            ]
            records.extend(
                {
                    "page": 4,
                    "kind": "text",
                    "text": name,
                    "resourceId": f"zh-{index}",
                    "figurePaths": [],
                }
                for index, name in enumerate(chinese)
            )
            with parser.open("w", encoding="utf-8", newline="\n") as handle:
                for record in records:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")

            report = extract_with_host_parser(parser, root / "paper.pdf", root / "work")
            blocks = [
                json.loads(line)
                for line in (root / "work" / "blocks.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            section_document = json.loads(
                (root / "work" / "sections.json").read_text(encoding="utf-8")
            )
            sections = section_document["sections"]
            by_title = {section["title"]: section for section in sections}

            check("host backend", report["backend"] == "host_parser")
            check("all source blocks retained", len(blocks) == len(records))
            check("implicit front matter", sections[0]["section_id"] == "sec_0000")
            check("figure excluded", "4.1.1 Not A Section" not in by_title)
            check("caption excluded", "5 REFERENCES" not in by_title)
            check("short axis label excluded", "Query" not in by_title)

            newline_heading = by_title.get("4.1.1 Datasets & Metrics")
            check("newline combined heading exists", newline_heading is not None)
            if newline_heading:
                evidence = newline_heading["segmentation_evidence"]
                check(
                    "newline exact quote",
                    evidence["quote"] == "4.1.1 Datasets & Metrics",
                )
                check(
                    "newline heading level",
                    newline_heading["level"] == 3,
                )
                check(
                    "section ends on last assigned page",
                    newline_heading["pdf_page_end"] == 2,
                )
                heading_block = next(
                    block
                    for block in blocks
                    if block["block_id"] == newline_heading["heading_block_id"]
                )
                check(
                    "newline combined role and raw text",
                    heading_block["role"] == "heading_and_body"
                    and heading_block["raw_text"]
                    == "4.1.1 Datasets & Metrics\nBody follows.",
                )

            period_heading = by_title.get("4.1.3 Hardware & Time Measurements")
            check("period combined heading title strips punctuation", period_heading is not None)
            if period_heading:
                evidence = period_heading["segmentation_evidence"]
                check(
                    "period exact quote retains punctuation",
                    evidence["quote"] == "4.1.3 Hardware & Time Measurements.",
                )
                heading_block = next(
                    block
                    for block in blocks
                    if block["block_id"] == period_heading["heading_block_id"]
                )
                check(
                    "period combined role and raw text",
                    heading_block["role"] == "heading_and_body"
                    and heading_block["raw_text"]
                    == "4.1.3 Hardware & Time Measurements. Body text follows.",
                )

            check(
                "all required Chinese headings",
                all(name in by_title for name in chinese),
            )
            check(
                "all section evidence needs semantic review",
                all(
                    section["segmentation_evidence"]["needs_semantic_review"] is True
                    for section in sections
                ),
            )
            figure_block = next(block for block in blocks if block["resourceId"] == "figure")
            check(
                "figure paths and kind preserved",
                figure_block["figurePaths"] == ["figure.png"]
                and figure_block["kind"] == "figure",
            )
            check(
                "visual review and OCR conservatism",
                report["pages_needing_visual_review"] == [1]
                and report["pages_needing_ocr_review"] == [],
            )
            check(
                "unknown image ratio and printed page",
                all(
                    page["image_area_ratio_estimate"] == 0.0
                    and page["printed_page"] is None
                    for page in (
                        json.loads(line)
                        for line in (root / "work" / "pages.jsonl")
                        .read_text(encoding="utf-8")
                        .splitlines()
                    )
                ),
            )
            check(
                "page text sidecar",
                (root / "work" / "pages" / "page_0001.txt").is_file(),
            )
    except Exception as exc:  # Self-test failure must be reported as JSON.
        failures.append(f"unexpected {type(exc).__name__}: {exc}")

    result = {
        "ok": not failures,
        "check_count": check_count,
        "failures": failures,
        "limitations": [
            "Heuristic section titles, levels, and boundaries require semantic review.",
            "Image coverage remains unknown; 0.0 is only a schema placeholder.",
        ],
    }
    print(json.dumps(result, ensure_ascii=False))
    return result


def main() -> int:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument(
        "--self-test", action="store_true", help="run the temporary synthetic smoke test"
    )
    arguments = argument_parser.parse_args()
    if not arguments.self_test:
        argument_parser.error("no action requested; use --self-test")
    return 0 if self_test()["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
