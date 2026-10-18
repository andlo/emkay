"""What Emkay knows about Unseenet and OpenVoiceOS: the README and description of
every public, non-fork repository of a GitHub user (or organisation).

Only the repository list goes through the GitHub API (a few requests); the
READMEs come from raw.githubusercontent.com, so 60 API requests an hour is
plenty. Everything is kept in one JSON cache.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .docs import Chunk, chunk_text, tokens

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
README_NAMES = ("README.md", "readme.md", "Readme.md", "README.MD", "README.rst", "README")
MAX_FILE = 200_000


@dataclass
class Repos:
    owner: str = "unseenet"
    token: str = ""
    exclude: set[str] = field(default_factory=set)

    def _get(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "emkay-bot"})
        if self.token and url.startswith(API):
            req.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()

    def repos(self) -> list[dict]:
        out = []
        for kind in ("users", "orgs"):
            try:
                for page in range(1, 11):
                    batch = json.loads(self._get(
                        f"{API}/{kind}/{self.owner}/repos?type=owner&per_page=100&page={page}"))
                    out.extend(batch)
                    if len(batch) < 100:
                        break
                break
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    raise
        return [r for r in out if not r.get("private") and not r.get("fork")
                and r["name"] not in self.exclude]

    def readme(self, name: str, branch: str) -> str:
        for fn in README_NAMES:
            try:
                raw = self._get(f"{RAW}/{self.owner}/{name}/{branch}/{fn}")
            except (urllib.error.HTTPError, OSError):
                continue
            return raw[:MAX_FILE].decode("utf-8", "replace")
        return ""

    def sync(self, log=print) -> dict:
        out = {"synced": time.time(), "owner": self.owner, "repos": []}
        for r in self.repos():
            name = r["name"]
            out["repos"].append({
                "name": name, "description": r.get("description") or "",
                "topics": r.get("topics") or [], "language": r.get("language") or "",
                "archived": bool(r.get("archived")), "url": r["html_url"],
                "readme": self.readme(name, r.get("default_branch") or "main")})
        log(f"repos: {len(out['repos'])} repositories of {self.owner}")
        return out


def chunks(cache: dict) -> list[Chunk]:
    out = []
    for r in cache.get("repos", []):
        name, url = r["name"], r["url"]
        bits = [r["description"]] if r["description"] else []
        if r.get("language"):
            bits.append(f"Written in {r['language']}.")
        if r.get("topics"):
            bits.append("Topics: " + ", ".join(r["topics"]) + ".")
        if r.get("archived"):
            bits.append("It is archived: no longer worked on.")
        title = f"The {name} repository"
        c = Chunk(f"repo:{name}", title, "", f"{title} ({url}): " + " ".join(bits), url,
                  kind="repo", repo=name)
        c.terms = Counter(tokens(f"{name} {name} {' '.join(bits)} {' '.join(bits)}"))
        out.append(c)
        if r.get("readme"):
            out.extend(chunk_text(r["readme"], f"{name}/README.md", url, repo=name))
    return out


def load_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache))
    tmp.replace(path)
