"""Capability layer for PDF rasterisation and image OCR.

Design rule: backends are *probed*, never assumed, and every result records which
backend actually produced it. A missing backend is a reported capability gap, not
a silent failure and not a reason to fabricate a visual review.

Render backend order (first usable wins):
    pymupdf -> pypdfium2 -> pdftoppm -> mutool -> gs -> winrt_pdf -> platform_tool

`winrt_pdf` uses Windows.Data.Pdf through the bundled `winrt_pdf.ps1`, which
needs nothing but Windows 10/11 and Windows PowerShell 5.1. It exists so that
visual verification does not depend on installing Python wheels or poppler.

OCR backend order:
    tesseract -> rapidocr -> winrt_ocr -> platform_tool
`winrt_ocr` uses Windows.Media.Ocr (bundled `winrt_pdf.ps1`), i.e. the OCR language
packs the operating system already ships with.

Pure standard library. Optional Pillow is used only to measure ink coverage, which
makes a blank or mostly-empty render detectable instead of looking like success.
"""

from __future__ import annotations

import sys as _sys
_sys.dont_write_bytecode = True  # never mutate the package while self-checking

import json
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable


SCRIPTS_DIR = Path(__file__).resolve().parent
RENDER_BACKENDS = ("pymupdf", "pypdfium2", "pdftoppm", "mutool", "gs", "winrt_pdf", "platform_tool")
OCR_BACKENDS = ("tesseract", "rapidocr", "winrt_ocr", "platform_tool")
PS_EXE = "powershell.exe"
TIMEOUT = 180


# --------------------------------------------------------------------------- utils

def _which(command: str) -> str | None:
    return shutil.which(command)


def _has_module(name: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _module_version(name: str) -> str | None:
    import importlib.metadata

    try:
        return importlib.metadata.version(name)
    except Exception:
        return None


def _run(command: list[str], timeout: int = TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, timeout=timeout, check=False)


def _ps51() -> tuple[bool, str]:
    """Windows PowerShell 5.1 is the only host that can project WinRT types."""

    if sys.platform != "win32":
        return False, "not running on Windows"
    exe = _which(PS_EXE)
    if not exe:
        return False, "powershell.exe not found"
    try:
        probe = _run([exe, "-NoProfile", "-Command", "[int]$PSVersionTable.PSVersion.Major"], timeout=30)
    except Exception as exc:  # pragma: no cover - environment dependent
        return False, f"powershell probe failed: {type(exc).__name__}"
    major = probe.stdout.decode("utf-8", "replace").strip()
    if probe.returncode != 0 or major != "5":
        return False, f"Windows PowerShell major version is {major or 'unknown'}, WinRT needs 5"
    return True, exe


# ---------------------------------------------------------------------- render probe

def probe_render() -> dict[str, dict[str, Any]]:
    """Report every render backend with availability and the reason it is usable or not."""

    report: dict[str, dict[str, Any]] = {}

    fitz = "pymupdf" if _has_module("pymupdf") else ("fitz" if _has_module("fitz") else None)
    report["pymupdf"] = {
        "available": bool(fitz),
        "detail": f"python module {fitz}" if fitz else "PyMuPDF not importable (pip install pymupdf)",
        "version": _module_version("PyMuPDF") if fitz else None,
    }

    pdfium = _has_module("pypdfium2")
    report["pypdfium2"] = {
        "available": pdfium,
        "detail": "python module pypdfium2" if pdfium else "pypdfium2 not importable (pip install pypdfium2)",
        "version": _module_version("pypdfium2") if pdfium else None,
    }

    for backend, command in (("pdftoppm", "pdftoppm"), ("mutool", "mutool"), ("gs", "gs")):
        exe = _which(command)
        report[backend] = {
            "available": bool(exe),
            "detail": exe or f"{command} not on PATH",
            "version": None,
        }

    ps_ok, ps_detail = _ps51()
    report["winrt_pdf"] = {
        "available": ps_ok and (SCRIPTS_DIR / "winrt_pdf.ps1").is_file(),
        "detail": (
            f"Windows.Data.Pdf via {ps_detail}"
            if ps_ok else ps_detail
        ),
        "version": "Windows.Data.Pdf",
    }

    platform_tool = _which("pdf-render-tool")
    report["platform_tool"] = {
        "available": bool(platform_tool),
        "detail": platform_tool or "platform pdf-render-tool not on PATH; its CLI is not assumed",
        "version": None,
    }
    return report


def select_render_backend(requested: str = "auto") -> tuple[str | None, dict[str, dict[str, Any]]]:
    probes = probe_render()
    if requested != "auto":
        probe = probes.get(requested)
        if probe and probe["available"]:
            return requested, probes
        return None, probes
    for backend in RENDER_BACKENDS:
        if probes.get(backend, {}).get("available"):
            return backend, probes
    return None, probes


# ------------------------------------------------------------------- PNG inspection

def png_info(path: Path) -> dict[str, Any]:
    """Read PNG dimensions from the IHDR chunk without any third-party library."""

    info: dict[str, Any] = {"bytes": path.stat().st_size if path.is_file() else 0}
    try:
        with path.open("rb") as handle:
            head = handle.read(33)
        if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
            width, height = struct.unpack(">II", head[16:24])
            info.update({"width": width, "height": height})
        else:
            info["error"] = "not a PNG header"
    except OSError as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info


def image_ink_ratio(path: Path) -> float | None:
    """Fraction of non-background pixels; None when Pillow is unavailable."""

    if not _has_module("PIL"):
        return None
    try:
        from PIL import Image  # type: ignore

        with Image.open(path) as image:
            grey = image.convert("L")
            grey.thumbnail((600, 600))
            pixels = list(grey.getdata())
        if not pixels:
            return None
        dark = sum(1 for value in pixels if value < 200)
        return round(dark / len(pixels), 4)
    except Exception:
        return None


def inspect_render(path: Path) -> dict[str, Any]:
    info = png_info(path)
    info["ink_ratio"] = image_ink_ratio(path)
    width, height = info.get("width"), info.get("height")
    concerns: list[str] = []
    if not width or not height:
        concerns.append("no readable PNG dimensions")
    elif width < 200 or height < 200:
        concerns.append(f"implausibly small page image {width}x{height}")
    if info["ink_ratio"] is not None and info["ink_ratio"] < 0.002:
        concerns.append(f"near-blank page (ink_ratio={info['ink_ratio']})")
    info["suspect_blank"] = bool(concerns)
    info["concerns"] = concerns
    return info


# ---------------------------------------------------------------------- page count

def page_count(pdf: Path) -> tuple[int | None, str]:
    """Resolve the page count without assuming PyMuPDF.

    Rendering and OCR used to fail before reaching any backend because page-count
    resolution required PyMuPDF or `pdfinfo`. The bundled PyPDF2 reader and the
    Windows.Data.Pdf counter remove that hidden dependency.
    """

    pdf = Path(pdf)

    try:
        import PyPDF2  # bundled vendor/PyPDF2 is on sys.path when run through notes_tool

        with pdf.open("rb") as handle:
            return len(PyPDF2.PdfReader(handle).pages), "bundled_pypdf2"
    except Exception:
        pass

    fitz = "pymupdf" if _has_module("pymupdf") else ("fitz" if _has_module("fitz") else None)
    if fitz:
        try:
            import importlib

            module = importlib.import_module(fitz)
            document = module.open(str(pdf))
            try:
                return int(document.page_count), fitz
            finally:
                document.close()
        except Exception:
            pass

    if _has_module("pypdfium2"):
        try:
            import pypdfium2 as pdfium  # type: ignore

            document = pdfium.PdfDocument(str(pdf))
            try:
                return len(document), "pypdfium2"
            finally:
                document.close()
        except Exception:
            pass

    exe = _which("pdfinfo")
    if exe:
        try:
            completed = _run([exe, str(pdf)], timeout=60)
            match = re.search(r"^Pages:\s*(\d+)", completed.stdout.decode("utf-8", "replace"), re.MULTILINE)
            if completed.returncode == 0 and match:
                return int(match.group(1)), "pdfinfo"
        except Exception:
            pass

    ps_ok, ps_detail = _ps51()
    script = SCRIPTS_DIR / "winrt_pdf.ps1"
    if ps_ok and script.is_file():
        try:
            completed = _run([ps_detail, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                              "-Mode", "count", "-Pdf", str(pdf)], timeout=90)
            text = completed.stdout.decode("utf-8", "replace")
            match = re.search(r"PAGE_COUNT=(\d+)", text)
            if completed.returncode == 0 and match:
                return int(match.group(1)), "winrt_pdf"
        except Exception:
            pass

    return None, "unavailable"


# ----------------------------------------------------------------------- rendering

def _render_pymupdf(pdf: Path, pages: Iterable[int], out_dir: Path, dpi: int) -> list[dict[str, Any]]:
    import importlib

    module = importlib.import_module("pymupdf" if _has_module("pymupdf") else "fitz")
    rendered = []
    doc = module.open(str(pdf))
    try:
        for page_number in pages:
            image_path = out_dir / f"page_{page_number:04d}.png"
            pixmap = doc[page_number - 1].get_pixmap(dpi=dpi, alpha=False)
            pixmap.save(str(image_path))
            rendered.append({"pdf_page": page_number, "path": str(image_path)})
    finally:
        doc.close()
    return rendered


def _render_pypdfium2(pdf: Path, pages: Iterable[int], out_dir: Path, dpi: int) -> list[dict[str, Any]]:
    import pypdfium2 as pdfium  # type: ignore

    rendered = []
    document = pdfium.PdfDocument(str(pdf))
    try:
        for page_number in pages:
            image_path = out_dir / f"page_{page_number:04d}.png"
            bitmap = document[page_number - 1].render(scale=dpi / 72)
            bitmap.to_pil().save(str(image_path))
            rendered.append({"pdf_page": page_number, "path": str(image_path)})
    finally:
        document.close()
    return rendered


def _render_pdftoppm(pdf: Path, pages: Iterable[int], out_dir: Path, dpi: int) -> list[dict[str, Any]]:
    exe = _which("pdftoppm")
    if not exe:
        raise RuntimeError("pdftoppm not available")
    rendered = []
    for page_number in pages:
        image_path = out_dir / f"page_{page_number:04d}.png"
        completed = _run([exe, "-f", str(page_number), "-l", str(page_number), "-r", str(dpi),
                          "-singlefile", "-png", str(pdf), str(image_path.with_suffix(""))])
        if completed.returncode != 0 or not image_path.is_file():
            raise RuntimeError(f"pdftoppm failed: {completed.stderr.decode('utf-8', 'replace').strip()}")
        rendered.append({"pdf_page": page_number, "path": str(image_path)})
    return rendered


def _render_gs(pdf: Path, pages: Iterable[int], out_dir: Path, dpi: int) -> list[dict[str, Any]]:
    exe = _which("gs")
    if not exe:
        raise RuntimeError("gs not available")
    rendered = []
    for page_number in pages:
        image_path = out_dir / f"page_{page_number:04d}.png"
        completed = _run([exe, "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=png16m",
                          f"-r{dpi}", f"-dFirstPage={page_number}", f"-dLastPage={page_number}",
                          f"-sOutputFile={image_path}", str(pdf)])
        if completed.returncode != 0 or not image_path.is_file():
            raise RuntimeError(f"gs failed: {completed.stderr.decode('utf-8', 'replace').strip()}")
        rendered.append({"pdf_page": page_number, "path": str(image_path)})
    return rendered


def _render_mutool(pdf: Path, pages: Iterable[int], out_dir: Path, dpi: int) -> list[dict[str, Any]]:
    exe = _which("mutool")
    if not exe:
        raise RuntimeError("mutool not available")
    rendered = []
    for page_number in pages:
        image_path = out_dir / f"page_{page_number:04d}.png"
        completed = _run([exe, "draw", "-o", str(image_path), "-r", str(dpi),
                          str(pdf), str(page_number)])
        if completed.returncode != 0 or not image_path.is_file():
            raise RuntimeError(f"mutool failed: {completed.stderr.decode('utf-8', 'replace').strip()}")
        rendered.append({"pdf_page": page_number, "path": str(image_path)})
    return rendered


def _render_winrt(pdf: Path, pages: Iterable[int], out_dir: Path, dpi: int) -> list[dict[str, Any]]:
    script = SCRIPTS_DIR / "winrt_pdf.ps1"
    if not script.is_file():
        raise RuntimeError("bundled winrt_pdf.ps1 is missing")
    ps_ok, ps_detail = _ps51()
    if not ps_ok:
        raise RuntimeError(f"WinRT render unavailable: {ps_detail}")
    ordered = sorted(pages)
    page_spec = ",".join(str(p) for p in ordered)
    completed = _run([ps_detail, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                      "-Mode", "render",
                      "-Pdf", str(pdf), "-OutDir", str(out_dir), "-Pages", page_spec, "-Dpi", str(dpi)])
    stdout = completed.stdout.decode("utf-8", "replace")
    if completed.returncode != 0:
        raise RuntimeError(f"winrt_pdf.ps1 failed: {stdout.strip()[:400]}")
    rendered = []
    for page_number in ordered:
        image_path = out_dir / f"page_{page_number:04d}.png"
        if not image_path.is_file():
            raise RuntimeError(f"winrt render produced no image for page {page_number}: {stdout.strip()[:300]}")
        rendered.append({"pdf_page": page_number, "path": str(image_path)})
    return rendered


_DISPATCH = {
    "pymupdf": _render_pymupdf,
    "pypdfium2": _render_pypdfium2,
    "pdftoppm": _render_pdftoppm,
    "mutool": _render_mutool,
    "gs": _render_gs,
    "winrt_pdf": _render_winrt,
}


def render_pages(pdf: Path, pages: list[int], out_dir: Path, dpi: int = 200,
                 backend: str = "auto") -> dict[str, Any]:
    """Rasterise the requested pages and inspect every produced image.

    Never raises for an unavailable backend: it falls through the chain and reports
    the attempts. Callers must treat a non-empty ``error`` as "no visual evidence".
    """

    pdf = Path(pdf)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    probes = probe_render()
    attempts: list[dict[str, Any]] = []
    order = [backend] if backend != "auto" else [b for b in RENDER_BACKENDS if b != "platform_tool"]

    for candidate in order:
        if candidate not in _DISPATCH:
            attempts.append({"backend": candidate, "ok": False, "error": "backend not wired for automatic use"})
            continue
        if not probes.get(candidate, {}).get("available"):
            attempts.append({"backend": candidate, "ok": False,
                             "error": probes.get(candidate, {}).get("detail", "unavailable")})
            continue
        try:
            rendered = _DISPATCH[candidate](pdf, pages, out_dir, dpi)
        except Exception as exc:
            attempts.append({"backend": candidate, "ok": False,
                             "error": f"{type(exc).__name__}: {exc}"})
            continue
        for item in rendered:
            item.update(inspect_render(Path(item["path"])))
            item["source"] = "rendered_page"
        attempts.append({"backend": candidate, "ok": True, "error": None})
        return {
            "ok": True, "backend": candidate, "dpi": dpi, "rendered": rendered,
            "backend_attempts": attempts, "capabilities": probes,
            "vision_review_note": (
                "A rendered page is mechanical evidence only. A `rendered_page` anchor still needs a named "
                "reviewer or OCR text for the same page; neither is implied by rendering."
            ),
        }

    return {"ok": False, "backend": None, "dpi": dpi, "rendered": [],
            "backend_attempts": attempts, "capabilities": probes,
            "error": "no usable PDF render backend in this environment"}


# ---------------------------------------------------------------------------- OCR

def probe_ocr() -> dict[str, dict[str, Any]]:
    report: dict[str, dict[str, Any]] = {}

    exe = _which("tesseract")
    report["tesseract"] = {"available": bool(exe), "detail": exe or "tesseract not on PATH"}

    module = "rapidocr_onnxruntime" if _has_module("rapidocr_onnxruntime") else (
        "rapidocr" if _has_module("rapidocr") else None)
    report["rapidocr"] = {
        "available": bool(module),
        "detail": f"python module {module}" if module else "no rapidocr package importable",
    }

    ps_ok, ps_detail = _ps51()
    langs: list[str] = []
    detail = ps_detail
    script = SCRIPTS_DIR / "winrt_pdf.ps1"
    if ps_ok and script.is_file():
        try:
            completed = _run([ps_detail, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                              "-Mode", "ocr", "-Probe"], timeout=60)
            text = completed.stdout.decode("utf-8", "replace")
            for line in text.splitlines():
                if line.startswith("AVAILABLE_LANGS="):
                    langs = [tag for tag in line.split("=", 1)[1].split(",") if tag]
            detail = f"Windows.Media.Ocr langs={','.join(langs) or 'none'}"
        except Exception as exc:
            detail = f"Windows.Media.Ocr probe failed: {type(exc).__name__}"
    report["winrt_ocr"] = {
        "available": bool(langs),
        "detail": detail,
        "languages": langs,
    }

    platform_tool = _which("ocr-tool")
    report["platform_tool"] = {
        "available": bool(platform_tool),
        "detail": platform_tool or "platform ocr-tool not on PATH; its CLI is not assumed",
    }
    return report


def select_ocr_backend(requested: str = "auto") -> tuple[str | None, dict[str, dict[str, Any]]]:
    probes = probe_ocr()
    if requested != "auto":
        probe = probes.get(requested)
        return (requested if probe and probe["available"] else None), probes
    for backend in OCR_BACKENDS:
        if probes.get(backend, {}).get("available"):
            return backend, probes
    return None, probes


def ocr_image(image: Path, backend: str, lang: str = "en-US", timeout: int = TIMEOUT) -> dict[str, Any]:
    """OCR one page image. Returns the text plus the backend that actually produced it."""

    image = Path(image)
    result: dict[str, Any] = {"image": str(image), "backend": backend, "lang": lang, "text": ""}

    if backend == "tesseract":
        exe = _which("tesseract")
        if not exe:
            raise RuntimeError("tesseract not available")
        completed = _run([exe, str(image), "stdout", "-l", lang], timeout=timeout)
        if completed.returncode != 0:
            raise RuntimeError(f"tesseract failed: {completed.stderr.decode('utf-8', 'replace').strip()}")
        result["text"] = completed.stdout.decode("utf-8", "replace")
        return result

    if backend == "rapidocr":
        module_name = "rapidocr_onnxruntime" if _has_module("rapidocr_onnxruntime") else "rapidocr"
        import importlib

        module = importlib.import_module(module_name)
        output = module.RapidOCR()(str(image))
        if isinstance(output, tuple):
            lines = [str(item[1]) for item in (output[0] or []) if len(item) > 1 and str(item[1]).strip()]
        else:
            lines = [str(text) for text in (getattr(output, "txts", None) or []) if str(text).strip()]
        result["text"] = "\n".join(lines)
        return result

    if backend == "winrt_ocr":
        ps_ok, ps_detail = _ps51()
        if not ps_ok:
            raise RuntimeError(f"WinRT OCR unavailable: {ps_detail}")
        script = SCRIPTS_DIR / "winrt_pdf.ps1"
        if not script.is_file():
            raise RuntimeError("bundled winrt_pdf.ps1 is missing")
        completed = _run([ps_detail, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                          "-Mode", "ocr", "-Image", str(image), "-Lang", lang], timeout=timeout)
        text = completed.stdout.decode("utf-8", "replace")
        if completed.returncode != 0:
            raise RuntimeError(f"winrt_pdf.ps1 failed: {text.strip()[:300]}")
        if "-----BEGIN_OCR_TEXT-----" in text and "-----END_OCR_TEXT-----" in text:
            body = text.split("-----BEGIN_OCR_TEXT-----", 1)[1].split("-----END_OCR_TEXT-----", 1)[0]
        else:
            body = ""
        for line in text.splitlines():
            if line.startswith("ENGINE_LANG="):
                result["engine_lang"] = line.split("=", 1)[1]
        result["text"] = body.strip("\r\n")
        return result

    raise RuntimeError(f"unsupported OCR backend: {backend}")


# ------------------------------------------------------------------------- doctor

def doctor() -> dict[str, Any]:
    """Full capability matrix plus an explicit statement of what evidence is possible."""

    render = probe_render()
    ocr = probe_ocr()
    chosen_render, _ = select_render_backend()
    chosen_ocr, _ = select_ocr_backend()
    return {
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "render": render,
        "ocr": ocr,
        "selected_render_backend": chosen_render,
        "selected_ocr_backend": chosen_ocr,
        "can_render": chosen_render is not None,
        "can_ocr": chosen_ocr is not None,
        "vision_model_available": False,
        "evidence_policy": [
            "text_layer anchors: verbatim quote on the declared PDF page",
            "ocr anchors: quote matched against real OCR text for the same page",
            "rendered_page anchors: require a named reviewer OR OCR text for the same page",
            "no render backend: record the gap in uncertainty.json and never claim a visual review",
        ],
    }


if __name__ == "__main__":
    print(json.dumps(doctor(), ensure_ascii=False, indent=2))
