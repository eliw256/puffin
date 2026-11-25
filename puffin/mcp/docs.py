"""Keyword search over parts' extracted docs. Pure apart from reading files; no network.

Docs live at `<root>/<vendor>/docs/<name>.md` in each part library root and belong to part
`<vendor>/<name>`. Each file splits into sections at `## ` headings; text before the first
`## ` is the `intro` section. A section id is `<vendor>/<name>#<slug>`.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

_TOKEN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_SECTION_ID = re.compile(r"^([a-z0-9][a-z0-9_.-]*)/([a-z0-9][a-z0-9_.-]*)#([a-z0-9-]+)$")


class PathEscape(ValueError):
    """A doc path resolves outside its part library root."""


@dataclass(frozen=True)
class Section:
    part: str  # "espressif/esp32-s3-wroom-1"
    doc: str  # path relative to its library root, POSIX
    slug: str
    heading: str
    text: str

    @property
    def section_id(self) -> str:
        return f"{self.part}#{self.slug}"


def slugify(heading: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-") or "section"


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def split_sections(part: str, doc: str, text: str) -> list[Section]:
    sections: list[Section] = []
    title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), "")
    heading, slug = title or "Introduction", "intro"
    lines: list[str] = []
    seen: Counter[str] = Counter()

    def flush() -> None:
        body = "\n".join(lines).strip()
        if body or slug != "intro":
            seen[slug] += 1
            unique = slug if seen[slug] == 1 else f"{slug}-{seen[slug]}"
            sections.append(Section(part, doc, unique, heading, body))

    for line in text.splitlines():
        if line.startswith("## "):
            flush()
            heading = line[3:].strip()
            slug, lines = slugify(heading), []
        else:
            lines.append(line)
    flush()
    return sections


class DocIndex:
    """All doc sections under the given library roots. Earlier roots win for the same part."""

    def __init__(self, roots: list[Path]) -> None:
        self.roots = roots
        self.sections: dict[str, Section] = {}
        self.by_part: dict[str, list[str]] = {}
        for root in roots:
            if not root.is_dir():
                continue
            for path in sorted(root.glob("*/docs/*.md")):
                if not inside(path, root):
                    continue  # a symlink out of the library is never indexed
                part = f"{path.parent.parent.name}/{path.stem}"
                if part in self.by_part:
                    continue
                doc = path.relative_to(root).as_posix()
                found = split_sections(part, doc, path.read_text(encoding="utf-8"))
                self.by_part[part] = [s.section_id for s in found]
                for section in found:
                    self.sections[section.section_id] = section

    def get(self, section_id: str) -> Section | None:
        if ".." in section_id or "/" in section_id.split("#", 1)[-1]:
            raise PathEscape(section_id)
        if not _SECTION_ID.match(section_id):
            return None
        return self.sections.get(section_id)

    def search(
        self, query: str, part: str | None = None, limit: int = 8
    ) -> list[dict[str, object]]:
        terms = list(dict.fromkeys(tokens(query)))
        if not terms:
            return []
        candidates = [s for s in self.sections.values() if part is None or s.part == part]
        bags = {s.section_id: Counter(tokens(f"{s.heading}\n{s.text}")) for s in candidates}
        total = len(candidates) or 1
        weight = {
            t: math.log(1 + total / (1 + sum(1 for bag in bags.values() if t in bag)))
            for t in terms
        }
        scored = []
        for section in candidates:
            bag = bags[section.section_id]
            score = sum(weight[t] * (1 + math.log(bag[t])) for t in terms if bag[t])
            score += sum(weight[t] for t in terms if t in tokens(section.heading))
            if score > 0:
                scored.append((round(score, 4), section))
        scored.sort(key=lambda item: (-item[0], item[1].section_id))
        return [
            {
                "part": s.part,
                "doc": s.doc,
                "section_id": s.section_id,
                "heading": s.heading,
                "snippet": _snippet(s, terms),
                "score": score,
            }
            for score, s in scored[: max(1, limit)]
        ]


def _snippet(section: Section, terms: list[str]) -> str:
    lines = [line.strip() for line in section.text.splitlines() if line.strip()]
    if not lines:
        return section.heading
    best = max(lines, key=lambda line: (sum(t in tokens(line) for t in terms), -lines.index(line)))
    return best if len(best) <= 200 else best[:197] + "..."
