"""Emkay follows the news: the headlines newsbot posts in ``dk.newsbot.*``.

He keeps each headline (title, medium, link, when he saw it) for a week, so he can tell
the café what is happening today, find a story when someone asks about it, and show how
different media headline the same story. He only knows the headlines - to say more, he
reads the article (``reader.py``) when someone asks.

Questions he understands (Danish and English), checked before a message goes to OVOS:

- *today*: "hvad sker der i verden i dag?", "dagens nyheder", "what's happening today?"
- *more*: "fortæl mere om Polen", "læs artiklen om …", "tell me more about …"
- *compare*: "hvad skriver de andre om …", "hvad skriver medierne om …", "how do the media cover …"
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

KEEP_DAYS = 7
LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
SITE_LINE = re.compile(r"^\*([^*\n]+)\*\s*$", re.MULTILINE)
WORD = re.compile(r"[a-zæøåäöüéè0-9]+", re.IGNORECASE)
# small words that say nothing about which story is meant
STOP = set("""
og i at er en et den det de der som på til med for af om fra har var vil kan skal ikke
nu så men eller hvad hvem hvor hvordan hvorfor mere lidt den det om artiklen historien
nyheden fortæl læs skriver andre medierne aviserne the a an of in on to and is are was
for with about from what who how why more tell me news article story read
""".split())


@dataclass
class Headline:
    title: str
    url: str
    site: str
    group: str
    msgid: str
    seen: str                      # ISO time Emkay first saw it

    @property
    def when(self) -> datetime:
        return datetime.fromisoformat(self.seen)

    def words(self) -> set[str]:
        return {w.lower() for w in WORD.findall(self.title)} - STOP

    def md(self) -> str:
        return f"[{self.title}]({self.url})"


def site_of(msg) -> str:
    """The medium: newsbot's ``*DR*`` line, else its From name ``newsbot · DR``."""
    m = SITE_LINE.search(msg.body or "")
    if m:
        return m.group(1).strip()
    name = msg.from_name or ""
    return name.split("·", 1)[1].strip() if "·" in name else name.strip()


def headline_of(msg, group: str, now: datetime) -> Headline | None:
    """A newsbot post as a headline: a thread's first post with a link to the article."""
    if msg.references or not msg.readable:
        return None
    card = getattr(msg, "card", None)
    url = getattr(card, "url", None)
    if not url:
        m = LINK.search(msg.body or "")
        url = m.group(2) if m else None
    title = (msg.subject or "").strip()
    if not url or not title:
        return None
    return Headline(title, url, site_of(msg), group, msg.message_id, now.isoformat())


@dataclass
class NewsStore:
    """Headlines and what Emkay has done with them, in one JSON file next to his state."""
    path: Path | None = None
    headlines: list[Headline] = field(default_factory=list)
    extra: dict = field(default_factory=dict)       # the paper, quizzes, read articles

    @classmethod
    def load(cls, path: str | Path | None) -> "NewsStore":
        if path is None:
            return cls()
        path = Path(path).expanduser()
        try:
            d = json.loads(path.read_text())
        except (OSError, ValueError):
            d = {}
        return cls(path, [Headline(**h) for h in d.get("headlines", [])], d.get("extra", {}))

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"headlines": [asdict(h) for h in self.headlines],
                                   "extra": self.extra}, indent=1, ensure_ascii=False))
        tmp.replace(self.path)

    def ingest(self, msgs, group: str, now: datetime | None = None) -> int:
        """Remember the new headlines among a group's messages; returns how many."""
        now = now or datetime.now(timezone.utc)
        have = {h.msgid for h in self.headlines}
        titles = {(h.site, h.title.lower()) for h in self.headlines}
        n = 0
        for m in msgs:
            if m.message_id in have:
                continue
            h = headline_of(m, group, now)
            if h and (h.site, h.title.lower()) not in titles:
                self.headlines.append(h)
                titles.add((h.site, h.title.lower()))
                n += 1
        self.prune(now)
        return n

    def prune(self, now: datetime) -> None:
        old = now - timedelta(days=KEEP_DAYS)
        self.headlines = [h for h in self.headlines if h.when > old]

    def recent(self, hours: float = 24, now: datetime | None = None) -> list[Headline]:
        """Newest first."""
        now = now or datetime.now(timezone.utc)
        since = now - timedelta(hours=hours)
        return sorted((h for h in self.headlines if h.when > since),
                      key=lambda h: h.when, reverse=True)

    def find(self, query: str, hours: float = 72, now: datetime | None = None,
             min_score: float = 0.0) -> list[Headline]:
        """The headlines that best match ``query``, best first: at least one word in common,
        and at least ``min_score`` of the query's words."""
        want = {w.lower() for w in WORD.findall(query)} - STOP
        if not want:
            return []
        scored = []
        for h in self.recent(hours, now):
            got = h.words()
            hits = sum(1 for w in want if w in got or any(g.startswith(w) and len(w) >= 4
                                                         for g in got))
            if hits and hits / len(want) >= min_score:
                scored.append((hits / len(want), h.when, h))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [h for _, _, h in scored]

    def compare(self, query: str, now: datetime | None = None) -> list[Headline]:
        """The best match from each medium: how they each headline the same story."""
        out, sites = [], set()
        for h in self.find(query, hours=72, now=now, min_score=0.5):
            if h.site not in sites:
                sites.add(h.site)
                out.append(h)
        return out


# --- what is asked ---------------------------------------------------------------------

@dataclass
class Ask:
    what: str                      # "today", "more" or "compare"
    topic: str = ""


_TODAY = re.compile(
    r"hvad\s+(?:sker|skete)\s+(?:der\s+)?(?:i\s+(?:verden|dag|danmark)|ude\s+i\s+verden)"
    r"|dagens\s+nyheder|nyhederne?\s+i\s+dag|hvad\s+er\s+(?:der\s+)?(?:af\s+)?nyt\b"
    r"|what'?s\s+(?:happening|going\s+on|new)\b.*\b(?:world|today)"
    r"|(?:today'?s|the\s+latest)\s+news|news\s+today", re.IGNORECASE)
_MORE = re.compile(
    r"(?:fortæl|sig|læs)\s+(?:mig\s+)?(?:lidt\s+)?(?:mere\s+)?(?:om|artiklen\s+om|historien\s+om)\s+(.+)"
    r"|tell\s+me\s+(?:a\s+bit\s+)?more\s+about\s+(.+)|read\s+(?:the\s+article|up)\s+(?:on|about)\s+(.+)",
    re.IGNORECASE)
_COMPARE = re.compile(
    r"hvad\s+skriver\s+(?:de\s+andre|medierne|aviserne|de\s+forskellige(?:\s+medier)?)\s+om\s+(.+)"
    r"|hvordan\s+(?:dækker|skriver)\s+(?:medierne|aviserne)\s+(?:om\s+)?(.+)"
    r"|how\s+do\s+(?:the\s+)?(?:media|papers|others)\s+(?:cover|report|write\s+about)\s+(.+)"
    r"|what\s+do\s+the\s+(?:others|media|papers)\s+(?:say|write)\s+about\s+(.+)", re.IGNORECASE)


def _topic(m: re.Match) -> str:
    t = next((g for g in m.groups() if g), "")
    return t.strip().rstrip("?!.").strip()


def ask_of(text: str) -> Ask | None:
    """What a café message asks about the news, if it does."""
    if m := _COMPARE.search(text):
        return Ask("compare", _topic(m))
    if m := _MORE.search(text):
        return Ask("more", _topic(m))
    if _TODAY.search(text):
        return Ask("today")
    return None


# --- what he says ----------------------------------------------------------------------

WAIT = {
    "da": ["Et øjeblik, jeg henter lige en kop kaffe ☕ og læser artiklen, inden jeg svarer …",
           "Øjeblik - jeg sætter mig lige med kaffen ☕ og læser artiklen først …",
           "Godt spørgsmål! Lad mig lige læse artiklen, så svarer jeg om lidt ☕",
           "Hmm, det må jeg hellere læse op på. Kaffen er sat over ☕ - et øjeblik …",
           "Jeg skimmer lige artiklen med en kop kaffe ☕ - tilbage om et øjeblik …"],
    "en": ["One moment, I'll grab a cup of coffee ☕ and read the article before I answer …",
           "Hold on - let me settle in with my coffee ☕ and read the article first …",
           "Good question! Let me read the article, I'll be right back ☕",
           "Hmm, I'd better read up on that. Coffee's on ☕ - one moment …"],
}
NO_ROBOTS = {
    "da": "{site} vil ikke have robotter til at læse deres artikler, så den lader jeg være - "
          "men her er linket, så du selv kan læse den: {link}",
    "en": "{site} doesn't want robots reading their articles, so I'll leave it - "
          "but here's the link to read it yourself: {link}",
}
ELSEWHERE = {
    "da": "_{sites} vil ikke have robotter til at læse deres artikler, men {site} skriver "
          "også om det - så jeg har læst deres._\n\n",
    "en": "_{sites} doesn't want robots reading their articles, but {site} covers it too - "
          "so I read theirs._\n\n",
}
NO_ARTICLE = {
    "da": "Jeg kunne desværre ikke hente artiklen lige nu. Her er linket: {link}",
    "en": "Sorry, I couldn't fetch the article just now. Here's the link: {link}",
}
NOT_FOUND = {
    "da": "Den historie har jeg ikke set i overskrifterne de sidste par dage. "
          "Jeg følger nyhederne i !dk.newsbot.dr og de andre newsbot-grupper.",
    "en": "I haven't seen that story in the headlines the last few days. "
          "I follow the news in !dk.newsbot.dr and the other newsbot groups.",
}
NOTHING_TODAY = {
    "da": "Jeg har ikke set nogen overskrifter det sidste døgn - newsbot sover måske? 😴",
    "en": "I haven't seen any headlines in the last day - maybe newsbot is asleep? 😴",
}
TODAY_INTRO = {
    "da": ["Her er, hvad der sker - frisk fra overskrifterne ☕", "Dagens nyheder, kort fortalt:",
           "Det her fylder i nyhederne i dag:"],
    "en": ["Here's what's happening - fresh from the headlines ☕", "Today's news, in short:"],
}
ONLY_HEADLINES = {
    "da": "_Jeg kender kun overskrifterne. Spørg \"fortæl mere om …\", så læser jeg artiklen._",
    "en": "_I only know the headlines. Ask \"tell me more about …\" and I'll read the article._",
}
COMPARE_INTRO = {
    "da": "Sådan skriver medierne om det:", "en": "Here's how the media headline it:",
}
COMPARE_ONE = {
    "da": "Kun {site} har den i overskrifterne lige nu: {link}",
    "en": "Only {site} has it in the headlines right now: {link}",
}


def say(table: dict, lang: str, **kw) -> str:
    v = table.get(lang) or table["en"]
    return (random.choice(v) if isinstance(v, list) else v).format(**kw)


def group_ref(gid: str) -> str:
    """``!dk.newsbot.dr`` - a reference that opens (or offers to add) the group (#group refs)."""
    return "!" + gid.split("@", 1)[0]


def today(store: NewsStore, lang: str, short: bool = False, now: datetime | None = None) -> str:
    """'What's happening today?': the newest headlines, a few from each medium, DR first."""
    heads = store.recent(24, now)
    if not heads:
        return say(NOTHING_TODAY, lang)
    per_site: dict[str, list[Headline]] = {}
    for h in heads:
        per_site.setdefault(h.site, []).append(h)
    order = sorted(per_site, key=lambda s: (s != "DR", -len(per_site[s])))
    lines = [say(TODAY_INTRO, lang), ""]
    if short:                                    # a chat line: the five newest, one per medium
        for site in order[:5]:
            h = per_site[site][0]
            lines.append(f"- {h.md()} ({site})")
    else:
        for site in order[:8]:
            lines.append(f"**{site}** {group_ref(per_site[site][0].group)}")
            lines += [f"- {h.md()}" for h in per_site[site][:3]]
            lines.append("")
    lines += ["", say(ONLY_HEADLINES, lang)]
    return "\n".join(lines).strip()


def compared(store: NewsStore, topic: str, lang: str, now: datetime | None = None) -> str:
    heads = store.compare(topic, now)
    if not heads:
        return say(NOT_FOUND, lang)
    if len(heads) == 1:
        return say(COMPARE_ONE, lang, site=heads[0].site, link=heads[0].md())
    return "\n".join([say(COMPARE_INTRO, lang), ""] +
                     [f"- **{h.site}:** {h.md()}" for h in heads[:8]])
