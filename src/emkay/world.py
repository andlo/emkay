"""Looking things up in the world, the OpenVoiceOS way: the OVOS Wikipedia
retrieval engine and the OVOS DuckDuckGo solver.

Both are loaded lazily and fail softly - a lookup that does not work just
gives Emkay nothing to cite, and he answers from what he knows.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from urllib.parse import quote

log = logging.getLogger("emkay.world")


@dataclass
class Fact:
    label: str
    text: str
    url: str


class World:
    def __init__(self, wikipedia: bool = True, ddg: bool = True):
        self._wiki = self._ddg = None
        self.use_wiki, self.use_ddg = wikipedia, ddg

    def _wikipedia(self):
        if self._wiki is None and self.use_wiki:
            try:
                from ovos_wikipedia import WikipediaRetrievalEngine
                self._wiki = WikipediaRetrievalEngine()
            except Exception as exc:              # noqa: BLE001 - optional
                log.warning("no Wikipedia: %s", exc)
                self.use_wiki = False
        return self._wiki

    def _duck(self):
        if self._ddg is None and self.use_ddg:
            try:
                from ovos_ddg_solver import DuckDuckGoSolver
                self._ddg = DuckDuckGoSolver()
            except Exception as exc:              # noqa: BLE001
                log.warning("no DuckDuckGo: %s", exc)
                self.use_ddg = False
        return self._ddg

    def wikipedia(self, query: str, lang: str, k: int = 2) -> list[Fact]:
        w = self._wikipedia()
        if w is None or not query:
            return []
        out = []
        for code in dict.fromkeys([lang, "en"]):  # the asker's language first, then English
            try:
                pages = w.search(query, lang=code, top_k=k)
            except Exception as exc:              # noqa: BLE001
                log.warning("wikipedia %s: %s", code, exc)
                continue
            for p in pages:
                text = (p.short_summary or p.best_passage or p.summary or "").strip()
                if len(text) > 60:
                    out.append(Fact(f"Wikipedia ({p.lang}): {p.title}", text[:1500],
                                    f"https://{p.lang}.wikipedia.org/wiki/"
                                    + quote(p.title.replace(" ", "_"))))
            if out:
                break
        return out

    def duckduckgo(self, query: str) -> list[Fact]:
        d = self._duck()
        if d is None or not query:
            return []
        try:
            text = (d.get_spoken_answer(query, lang="en") or "").strip()
        except Exception as exc:                  # noqa: BLE001
            log.warning("duckduckgo: %s", exc)
            return []
        if len(text) < 40:
            return []
        return [Fact(f"DuckDuckGo: {query}", text[:1500],
                     "https://duckduckgo.com/?q=" + quote(query))]

    async def lookup(self, query: str, query_en: str, lang: str) -> list[Fact]:
        loop = asyncio.get_running_loop()
        wiki, duck = await asyncio.gather(
            loop.run_in_executor(None, self.wikipedia, query, lang),
            loop.run_in_executor(None, self.duckduckgo, query_en or query))
        return wiki + duck
