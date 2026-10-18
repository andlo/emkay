"""The bot's knowledge: the Unseenet docs and specs, cut into sections and
searched with BM25.

BM25 needs no model and no extra package, runs in milliseconds on the whole
docs folder, and is good at the words people ask with ("vault", "peering",
"UN-RFC 0019"). Embeddings can be added later behind the same `search`.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

SITE = "https://unseenet.org/"
MAX_CHARS = 2400          # a section longer than this is split in pieces
WHOLE = 4000              # a file this short stays one piece: its parts belong together
SKIP_DIRS = {"assets", "img", "javascripts", "stylesheets"}

_WORD = re.compile(r"[a-z0-9æøå]+(?:[-_.][a-z0-9æøå]+)*", re.IGNORECASE)
_HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*#*\s*$")
_RFC_TITLE = re.compile(r"^\s*Unseenet:\s*(.+?)\s*$")
_RFC_NUM = re.compile(r"un-rfc-(\d{4})")
STOP = set("""a an and are as at be by can do does for from has have how i if in is it there
its my not of on or so that the this to was what when where which who why will with you your
""".split())


@dataclass
class Chunk:
    path: str                 # relative to the docs folder, e.g. spec/un-rfc-0019.md
    title: str                # the document's title
    heading: str              # the section's heading ("" before the first one)
    text: str
    url: str
    kind: str = "docs"        # "docs", "issue" (an issue or a pull request) or "repo"
    repo: str = ""            # the repository, for files from GitHub
    terms: Counter = field(default_factory=Counter, repr=False)

    @property
    def label(self) -> str:
        """How an answer names this source, e.g. 'UN-RFC 0019, 4. Registered names'."""
        if self.kind in ("issue", "repo"):
            return self.title
        m = _RFC_NUM.search(self.path)
        doc = f"UN-RFC {m.group(1)}" if m else self.title
        if self.repo and self.repo != "unseenet" and not m:
            doc = f"{self.repo}: {doc}"
        return f"{doc}, {self.heading}" if self.heading and self.heading != self.title else doc


def tokens(text: str) -> list[str]:
    out = []
    for w in _WORD.findall(text.lower()):
        if w in STOP or len(w) < 2:
            continue
        out.append(w)
        if "-" in w or "." in w or "_" in w:      # un-rfc-0019 also matches 0019
            out.extend(p for p in re.split(r"[-_.]", w) if len(p) > 1 and p not in STOP)
    return out


def url_for(rel: str) -> str:
    if m := _RFC_NUM.search(rel):             # rfc/ in git is spec/ on the site
        return f"{SITE}spec/un-rfc-{m.group(1)}/"
    rel = rel[:-3] if rel.endswith(".md") else rel
    if rel == "index":
        return SITE
    if rel.endswith("/index"):
        rel = rel[: -len("index")]
        return SITE + rel
    return SITE + rel + "/"


def _title(lines: list[str], rel: str) -> str:
    in_block = False
    for ln in lines[:20]:                     # UN-RFC header block: "Unseenet: Names ..."
        if ln.startswith("```"):
            in_block = not in_block
            continue
        if in_block and (m := _RFC_TITLE.match(ln)):
            return m.group(1)
    for ln in lines:
        if (m := _HEADING.match(ln)) and len(m.group(1)) == 1:
            return m.group(2)
    return Path(rel).stem.replace("-", " ").title()


def chunk_file(path: Path, rel: str) -> list[Chunk]:
    return chunk_text(path.read_text(encoding="utf-8", errors="replace"), rel, url_for(rel))


def chunk_text(text: str, rel: str, url: str, repo: str = "") -> list[Chunk]:
    # MkDocs key notation: ++ctrl+p++ -> Ctrl+P
    text = re.sub(r"\+\+([a-z0-9+-]+?)\+\+", lambda m: "+".join(
        k.capitalize() if len(k) > 1 else k.upper() for k in m.group(1).split("+")), text)
    lines = text.splitlines()
    title = _title(lines, rel)
    sections: list[tuple[str, list[str]]] = [("", [])]
    in_code = False
    for ln in lines:
        if ln.startswith("```"):
            in_code = not in_code
        m = None if in_code else _HEADING.match(ln)
        if m and len(m.group(1)) <= 2:            # split at # and ##; ### stays inside
            sections.append((m.group(2), [ln]))
        else:
            sections[-1][1].append(ln)
    if len(text) <= WHOLE:
        sections = [("", lines)]
    out = []
    for heading, body in sections:
        body_text = "\n".join(body).strip()
        if len(body_text) < 40:
            continue
        for piece in _split(body_text):
            c = Chunk(rel, title, heading, piece, url, repo=repo)
            c.terms = Counter(tokens(f"{repo} {title} {heading} {heading} {piece}"))
            out.append(c)
    return out


def _split(text: str) -> list[str]:
    if len(text) <= MAX_CHARS:
        return [text]
    parts, cur = [], ""
    for para in text.split("\n\n"):
        if cur and len(cur) + len(para) > MAX_CHARS:
            parts.append(cur)
            cur = ""
        cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        parts.append(cur)
    return parts


class Index:
    """BM25 over sections of Markdown files."""

    def __init__(self, chunks: list[Chunk], k1: float = 1.4, b: float = 0.75):
        seen, unique = set(), []
        for c in chunks:              # the same section from a local folder and from GitHub
            key = (c.url, c.heading, c.text[:200])
            if key not in seen:
                seen.add(key)
                unique.append(c)
        chunks = unique
        self.chunks, self.k1, self.b = chunks, k1, b
        self.avg = sum(sum(c.terms.values()) for c in chunks) / max(len(chunks), 1)
        df: Counter = Counter()
        for c in chunks:
            df.update(c.terms.keys())
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    @classmethod
    def from_folders(cls, folders: list[str | Path]) -> "Index":
        chunks, seen = [], set()
        for folder in folders:
            folder = Path(folder).expanduser()
            files = [folder] if folder.is_file() else sorted(folder.rglob("*.md"))
            for f in files:
                rel = f.name if folder.is_file() else f.relative_to(folder).as_posix()
                if set(Path(rel).parts) & SKIP_DIRS or f.name in ("LICENSE.md", "README.md"):
                    continue
                if (m := _RFC_NUM.search(rel)) and m.group(1) in seen:
                    continue                  # the same UN-RFC in docs/spec and rfc/
                if m:
                    seen.add(m.group(1))
                chunks.extend(chunk_file(f, rel))
        return cls(chunks)

    def search(self, query: str, k: int = 5, max_issues: int = 3) -> list[tuple[float, Chunk]]:
        q = Counter(tokens(query))
        scored = []
        for c in self.chunks:
            length = sum(c.terms.values())
            s = 0.0
            for t in q:
                tf = c.terms.get(t)
                if tf:
                    s += self.idf[t] * tf * (self.k1 + 1) / (
                        tf + self.k1 * (1 - self.b + self.b * length / self.avg))
            if s > 0:
                scored.append((s, c))
        scored.sort(key=lambda sc: sc[0], reverse=True)
        out: list[tuple[float, Chunk]] = []
        per_doc: Counter = Counter()
        for s, c in scored:                       # at most two pieces of one document
            if per_doc[c.path] >= 2 or (c.kind == "issue" and per_doc["#issues"] >= max_issues):
                continue
            per_doc[c.path] += 1
            if c.kind == "issue":
                per_doc["#issues"] += 1
            out.append((s, c))
            if len(out) >= k:
                break
        return out
