#!/usr/bin/env python3
"""Validate required repository assets, Markdown headings, local links and Python syntax."""

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def slug(text):
    text = re.sub(r"[`*_]", "", text).lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def check(root=ROOT):
    root = Path(root)
    errors = []
    for name in [
        "README.md",
        "LICENSE",
        "CONTRIBUTING.md",
        "pyproject.toml",
        "MANIFEST.in",
        "docker-compose.yml",
        "config/syntony_config.json",
        "config/README.md",
        "deployment/README.md",
        "runtime/README.md",
        "scripts/README.md",
        "tests/README.md",
        "docs/state-and-policies.md",
        "docs/extending-the-catalog.md",
        "docs/event-contracts.md",
        "docs/algorithm-alignment.md",
        "docs/release-checklist.md",
    ]:
        if not (root / name).is_file():
            errors.append("Missing required file: " + name)
    for role in ["monitor", "composer", "cep", "coordinator", "executor"]:
        for name in ["__init__.py", "Dockerfile", "requirements.txt", "README.md"]:
            path = root / "runtime" / role / name
            if not path.is_file() or not path.read_text().strip():
                errors.append("Missing/empty module asset: " + str(path.relative_to(root)))
    for path in root.rglob("*.py"):
        if any(
            part in {".venv", ".local", "build", "dist", "generated", "__pycache__"}
            for part in path.relative_to(root).parts
        ):
            continue
        try:
            ast.parse(path.read_text(), filename=str(path))
        except SyntaxError as error:
            errors.append(str(error))
    for path in root.rglob("*.md"):
        if any(
            part in {".venv", ".local", "build", "dist", "generated", "__pycache__"}
            or part.endswith(".egg-info")
            for part in path.relative_to(root).parts
        ):
            continue
        text = path.read_text()
        outside = re.sub(r"```.*?```", "", text, flags=re.S)
        headings = re.findall(r"^(#{1,6})\s+(.+)$", outside, flags=re.M)
        if sum(level == "#" for level, _ in headings) != 1:
            errors.append("Expected one H1: " + str(path.relative_to(root)))
        previous = 0
        for level, _ in headings:
            if len(level) > previous + 1:
                errors.append("Skipped heading level: " + str(path.relative_to(root)))
            previous = len(level)
        for target in re.findall(r"\]\(([^)]+)\)", text):
            if re.match(r"[a-z]+:", target):
                continue
            name, _, fragment = target.partition("#")
            resolved = (path.parent / name).resolve() if name else path
            if not resolved.exists():
                errors.append(f"Broken link in {path.relative_to(root)}: {target}")
            elif fragment and resolved.suffix == ".md":
                anchors = {
                    slug(heading)
                    for heading in re.findall(r"^#{1,6}\s+(.+)$", resolved.read_text(), flags=re.M)
                }
                if fragment not in anchors:
                    errors.append(f"Unknown heading in {path.relative_to(root)}: {target}")
    return errors


if __name__ == "__main__":
    errors = check()
    print(
        "\n".join(errors)
        if errors
        else "Repository structure, Markdown headings, links and Python syntax: OK"
    )
    sys.exit(bool(errors))
