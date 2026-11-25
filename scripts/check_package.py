#!/usr/bin/env python3
"""Check the contest Skill package without installing any validator dependency."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

MAX_FILES = 50
MAX_SINGLE = 500 * 1024
MAX_TOTAL = 2 * 1024 * 1024
SECRET_PATTERNS = [re.compile(r"PLAYGROUND_TOKEN\s*="), re.compile(r"asp_[A-Za-z0-9]{20,}")]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_github_url(root: Path, errors: list[str], warnings: list[str]) -> dict:
    """The contest requires submission/GITHUB_URL.txt whose first line is a public URL."""

    path = root / "GITHUB_URL.txt"
    if not path.is_file():
        errors.append("GITHUB_URL.txt is missing; the contest awards 0/5 for the public repository link")
        return {"present": False}
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    first = lines[0] if lines else ""
    if not first:
        errors.append("GITHUB_URL.txt first line is empty")
    elif not re.match(r"^https://[^\s]+$", first):
        errors.append("GITHUB_URL.txt first line must be a single https:// URL, not a local path or note")
    elif "github.com" not in first:
        warnings.append("GITHUB_URL.txt first line is not a github.com URL; reviewers may not be able to open it")
    if len(lines) > 2:
        errors.append("GITHUB_URL.txt may contain at most two lines (URL, optional relative skill path)")
    return {"present": True, "url": first, "second_line": lines[1] if len(lines) > 1 else None, "line_count": len(lines)}



def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", help="Skill directory to inspect")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    errors: list[str] = []
    warnings: list[str] = []
    if not root.is_dir():
        print(json.dumps({"ok": False, "errors": [f"not a directory: {root}"]}))
        raise SystemExit(2)
    all_paths = list(root.rglob("*"))
    symlinks = [str(path.relative_to(root)) for path in all_paths if path.is_symlink()]
    if symlinks:
        errors.append(f"symlinks are not allowed: {symlinks}")
    files = [path for path in all_paths if path.is_file()]
    if len(files) > MAX_FILES:
        errors.append(f"too many files: {len(files)} > {MAX_FILES}")
    total = 0
    for path in files:
        size = path.stat().st_size
        total += size
        relative = str(path.relative_to(root))
        if size > MAX_SINGLE:
            errors.append(f"file exceeds 500 KiB: {relative} ({size} bytes)")
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append(f"Skill resource is not UTF-8 text: {relative}")
        for pattern in SECRET_PATTERNS:
            if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
                errors.append(f"possible credential in {relative}")
    if total > MAX_TOTAL:
        errors.append(f"package exceeds 2 MiB: {total} bytes")
    skill = root / "SKILL.md"
    if not skill.is_file():
        errors.append("SKILL.md is missing")
        frontmatter = {}
    else:
        text = skill.read_text(encoding="utf-8")
        match = re.match(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", text, re.DOTALL)
        if not match:
            errors.append("SKILL.md must begin with YAML frontmatter")
            frontmatter = {}
        else:
            frontmatter = {}
            for line in match.group(1).splitlines():
                if not line.strip():
                    continue
                if ":" not in line or line.startswith((" ", "\t", "-")):
                    errors.append(f"unsupported frontmatter line: {line}")
                    continue
                key, value = line.split(":", 1)
                frontmatter[key.strip()] = value.strip()
            extra = set(frontmatter) - {"name", "description"}
            if extra:
                errors.append(f"frontmatter has keys other than name/description: {sorted(extra)}")
            if not frontmatter.get("name"):
                errors.append("frontmatter name is empty")
            if not frontmatter.get("description"):
                errors.append("frontmatter description is empty")
            accepted_runtime_names = {frontmatter.get("name"), "submission", "weekly-skill"}
            if root.name not in accepted_runtime_names:
                warnings.append("directory name differs from both the skill name and official runtime directory names")
    required = [
        "USAGE.md", "references/field-dictionary.md", "references/anchor-format.md",
        "references/validation-and-evaluation.md", "examples/minimal_example.md",
        "scripts/notes_tool.py", "scripts/parser_adapter.py", "scripts/self_test.py",
    ]
    for name in required:
        if not (root / name).is_file():
            errors.append(f"required project file is missing: {name}")
    if not (root / "LICENSE").is_file():
        warnings.append("LICENSE is missing; reusers cannot tell the terms")
    github = check_github_url(root, errors, warnings)
    manifest = {
        "generated_by": "check_package.py", "root": str(root),
        "files": [
            {"path": str(path.relative_to(root)).replace("\\", "/"), "bytes": path.stat().st_size,
             "sha256": sha256_of(path)}
            for path in sorted((p for p in files if p.name not in {"manifest.json", "submission.sha256"}),
                               key=lambda item: str(item).lower())
        ],
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for path in ((root.parent / "submission.sha256"),):
        path.write_text("".join(f"{row['sha256']}  {row['path']}\n" for row in manifest["files"]), encoding="utf-8")
    report = {
        "ok": not errors, "root": str(root), "file_count": len(files),
        "total_bytes": total, "limits": {"files": MAX_FILES, "single_bytes": MAX_SINGLE, "total_bytes": MAX_TOTAL},
        "frontmatter": frontmatter, "errors": errors, "warnings": warnings,
        "github_url": github,
        "manifest_path": "manifest.json", "sha256_list": "submission.sha256",
        "semantic_review_required": "Package checks cannot prove scientific correctness, fair baseline gains or compliance of an unseen case result.",
    }
    print(json.dumps(report, ensure_ascii=False))
    (root.parent / "package_check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    raise SystemExit(0 if not errors else 2)


if __name__ == "__main__":
    main()
