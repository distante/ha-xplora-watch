#!/usr/bin/env python3

"""Check relative markdown links/images and in-page anchors across README.md and docs/*.md.

External (http/https) links are left to the "Docs links" workflow's lychee step; this only
verifies the links lychee can't check: repo-relative file/image paths and `#anchor` fragments,
using GitHub's actual heading-slug algorithm (each whitespace run collapses to nothing lost -
every individual space becomes its own hyphen, so "Foo & Bar" -> "foo--bar").

Scoped to README.md and docs/*.md, which link to each other with plain filesystem-relative
paths. CONTRIBUTING.md is deliberately excluded: it uses GitHub's separate "repo-root-relative"
convention (e.g. `../../issues/new`, resolved against the file's `/blob/<branch>/` URL, not the
filesystem) for portable links to top-level repo pages, which this filesystem-based checker
cannot evaluate.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
MARKDOWN_FILES = [ROOT_DIR / "README.md", *sorted((ROOT_DIR / "docs").glob("*.md"))]
LINK_PATTERN = re.compile(r"!?\]\(([^)]+)\)")
HEADING_PATTERN = re.compile(r"^#{1,6}\s+(.*)")
FENCE_PATTERN = re.compile(r"^```")
INLINE_CODE_PATTERN = re.compile(r"`([^`]*)`")
NON_SLUG_CHAR_PATTERN = re.compile(r"[^\w\s-]")


def github_slug(heading_text: str) -> str:
    """Reproduce github-slugger's algorithm closely enough for our headings (no emoji/unicode edge cases)."""
    text = INLINE_CODE_PATTERN.sub(r"\1", heading_text).lower()
    text = NON_SLUG_CHAR_PATTERN.sub("", text).strip()
    return re.sub(r"\s", "-", text)


def heading_slugs(text: str) -> set[str]:
    slugs = set()
    in_fence = False
    for line in text.splitlines():
        if FENCE_PATTERN.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = HEADING_PATTERN.match(line)
        if match:
            slugs.add(github_slug(match.group(1)))
    return slugs


def find_problems() -> list[str]:
    problems: list[str] = []
    slugs_by_file = {path: heading_slugs(path.read_text()) for path in MARKDOWN_FILES}

    for path in MARKDOWN_FILES:
        text = path.read_text()
        for match in LINK_PATTERN.finditer(text):
            target = match.group(1)
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if target.startswith("#"):
                fragment = target[1:]
                if fragment not in slugs_by_file[path]:
                    problems.append(f"{path.relative_to(ROOT_DIR)}: missing anchor '#{fragment}'")
                continue

            file_part, _, fragment = target.partition("#")
            if not file_part:
                continue
            resolved = (path.parent / file_part).resolve()
            try:
                resolved.relative_to(ROOT_DIR)
            except ValueError:
                problems.append(f"{path.relative_to(ROOT_DIR)}: link escapes repo: '{target}'")
                continue
            if not resolved.exists():
                problems.append(f"{path.relative_to(ROOT_DIR)}: broken link to '{target}'")
                continue
            if fragment and resolved in slugs_by_file and fragment not in slugs_by_file[resolved]:
                problems.append(f"{path.relative_to(ROOT_DIR)}: '{target}' has no anchor '#{fragment}' in {resolved.name}")

    return problems


def main() -> int:
    problems = find_problems()
    if problems:
        print(f"Found {len(problems)} broken internal doc link(s):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"OK: checked {len(MARKDOWN_FILES)} markdown files, all internal links/anchors resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
