#!/usr/bin/env python3
"""Package source files for deployment and write archive and per-file SHA-256 checksums."""

import argparse
import hashlib
import json
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INCLUDE = [
    "runtime",
    "config",
    "specifications",
    "deployment",
    "scripts",
    "docs",
    "tests",
    ".github",
    ".gitignore",
    ".dockerignore",
    "README.md",
    "CITATION.bib",
    "LICENSE",
    "CONTRIBUTING.md",
    "AGENTS.md",
    "pyproject.toml",
    "MANIFEST.in",
    "Makefile",
    "docker-compose.yml",
]


def bundle(output):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    files = []
    for entry in INCLUDE:
        source = ROOT / entry
        candidates = source.rglob("*") if source.is_dir() else [source]
        for path in candidates:
            if (
                path.is_file()
                and not path.is_symlink()
                and not any(
                    p
                    in {
                        "__pycache__",
                        ".ruff_cache",
                        ".pytest_cache",
                        ".local",
                        ".venv",
                        ".git",
                        "generated",
                        "build",
                        "dist",
                    }
                    for p in path.relative_to(ROOT).parts
                )
                and path.name != ".env"
                and not path.name.startswith(".env.")
                and path.name != ".DS_Store"
                and path.suffix not in {".pyc", ".tar", ".gz"}
            ):
                files.append(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)
    }
    with tarfile.open(output, "x:gz") as archive:
        for path in sorted(files):
            archive.add(path, arcname=str(path.relative_to(ROOT)), recursive=False)
    output.with_suffix(output.suffix + ".sha256.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(digest + "  " + output.name + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    bundle(args.output)
    print(args.output)
