"""Emkay's own feed: the morning coffee paper and the Friday quiz.

Both go to his personal feed (UN-RFC 0034), so people can follow him in their timeline
and comment underneath - he answers comments there like anywhere else.

- **Morgenkaffe** (every morning): the five headlines of the night, one per medium, with a
  line of his own about the day. Only headlines and links - nothing he has not read.
- **Fredagsquiz** (Friday afternoon): five questions on the week's headlines. The answer
  to each is a word from a headline, so he cannot make one up; the answers come as a
  reply the next day, with the links.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .llm import Llm
from .news import Headline, NewsStore, group_ref

DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
WEEKDAY_DA = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]
MONTH_DA = ["januar", "februar", "marts", "april", "maj", "juni", "juli", "august",
            "september", "oktober", "november", "december"]


@dataclass
class Schedule:
    tz: str = "Europe/Copenhagen"
    morning: str = "07:30"           # the coffee paper, every day
    quiz_day: str = "fri"
    quiz_time: str = "15:00"
    answers_after_hours: float = 21  # the quiz's answers, the next day around noon
    enabled: bool = True

    def _at(self, now: datetime, hhmm: str) -> datetime:
        local = now.astimezone(ZoneInfo(self.tz))
        h, m = (int(x) for x in hhmm.split(":"))
        return datetime.combine(local.date(), time(h, m), ZoneInfo(self.tz))

    def morning_due(self, now: datetime, last: str | None) -> bool:
        """Today's paper is due once its time has come - and not later than noon."""
        at = self._at(now, self.morning)
        local = now.astimezone(ZoneInfo(self.tz))
        return (at <= local < at.replace(hour=12, minute=0)
                and last != local.date().isoformat())

    def quiz_due(self, now: datetime, last: str | None) -> bool:
        local = now.astimezone(ZoneInfo(self.tz))
        at = self._at(now, self.quiz_time)
        return (local.weekday() == DAYS[self.quiz_day] and at <= local < at + timedelta(hours=4)
                and last != local.date().isoformat())


def today_da(now: datetime, tz: str) -> str:
    d = now.astimezone(ZoneInfo(tz))
    return f"{WEEKDAY_DA[d.weekday()]} den {d.day}. {MONTH_DA[d.month - 1]}"


def morning_heads(store: NewsStore, now: datetime) -> list[Headline]:
    """Five headlines from the last twelve hours, one per medium, DR first."""
    out, sites = [], set()
    heads = sorted(store.recent(12, now), key=lambda h: (h.site != "DR", -h.when.timestamp()))
    for h in heads:
        if h.site not in sites:
            sites.add(h.site)
            out.append(h)
        if len(out) == 5:
            break
    return out


MORNING_SUBJECT = ["☕ Morgenkaffe {day}", "☕ Morgenkaffe med Emkay - {day}",
                   "☕ Godmorgen! Avisen {day}"]
MORNING_END = ("_Jeg kender kun overskrifterne. Skriv `@emkay fortæl mere om …` herunder, "
               "så læser jeg artiklen for dig._")


async def morning_post(store: NewsStore, llm: Llm | None, now: datetime,
                       tz: str) -> tuple[str, str] | None:
    """The coffee paper: subject and body, or None when there is no news to bring."""
    heads = morning_heads(store, now)
    if len(heads) < 3:
        return None
    hello = "Godmorgen! Kaffen er klar, og her er nattens nyheder."
    if llm:
        try:
            got = await llm.chat(
                "Write ONE short, warm Danish sentence (at most 20 words) to say good morning "
                "to the readers of your morning coffee paper. You may mention the weekday "
                f"({today_da(now, tz)}). Do not mention or guess at any news. "
                "No hashtags, no quotes.", temperature=0.8)
            if 5 < len(got) < 200 and "\n" not in got.strip():
                hello = got.strip().strip('"')
        except Exception:                              # noqa: BLE001 - the plain hello will do
            pass
    lines = [hello, ""]
    for h in heads:
        lines.append(f"- **{h.site}:** {h.md()}")
    refs = " ".join(dict.fromkeys(group_ref(h.group) for h in heads))
    lines += ["", f"Mere i {refs}.", "", MORNING_END]
    subject = random.choice(MORNING_SUBJECT).format(day=today_da(now, tz))
    return subject, "\n".join(lines)


QUIZ_PROMPT = """\
Here are this week's news headlines, numbered:

{heads}

Make a fun quiz of {n} questions in Danish for the café, each about a DIFFERENT headline.
Rules:
- The answer to each question MUST be one word (or a name) copied exactly from its headline.
- The question must not contain the answer.
- Ask only what the headline itself says - nothing else, no guessing.
- Light and friendly; no questions about crime victims, deaths or accidents.

Reply with JSON only: {{"questions": [{{"headline": <number>, "question": "...", "answer": "..."}}]}}"""


def check_quiz(raw: str, heads: list[Headline]) -> list[tuple[str, str, Headline]]:
    """The questions that keep the rules: answer from its headline, not in the question."""
    try:
        qs = json.loads(re.search(r"\{.*\}", raw, re.DOTALL).group(0))["questions"]
    except (AttributeError, ValueError, KeyError, TypeError):
        return []
    out, used = [], set()
    for q in qs if isinstance(qs, list) else []:
        try:
            i = int(q["headline"]) - 1
            question, answer = str(q["question"]).strip(), str(q["answer"]).strip()
        except (KeyError, ValueError, TypeError):
            continue
        if not (0 <= i < len(heads)) or i in used or not question or not answer:
            continue
        title = heads[i].title.lower()
        if answer.lower() not in title or answer.lower() in question.lower():
            continue
        used.add(i)
        out.append((question, answer, heads[i]))
    return out


async def quiz_post(store: NewsStore, llm: Llm, now: datetime,
                    n: int = 5) -> tuple[str, str, list[dict]] | None:
    """The Friday quiz: subject, body and the answers to post later - or None."""
    week = [h for h in store.recent(24 * 6, now)]
    random.shuffle(week)
    heads = week[:30]
    if len(heads) < n:
        return None
    listing = "\n".join(f"{i}. {h.title} ({h.site})" for i, h in enumerate(heads, 1))
    got = check_quiz(await llm.chat(QUIZ_PROMPT.format(heads=listing, n=n + 2),
                                    temperature=0.7, json_out=True), heads)[:n]
    if len(got) < 3:
        return None
    lines = ["Fredag, og kaffen er varm ☕ Her er ugens nyhedsquiz - "
             "skriv dine svar herunder! Svarene kommer i morgen.", ""]
    lines += [f"{i}. {q}" for i, (q, _, _) in enumerate(got, 1)]
    lines += ["", "_Alle svar står i ugens overskrifter i dk.newsbot.* 😉_"]
    answers = [{"q": q, "a": a, "title": h.title, "url": h.url, "site": h.site}
               for q, a, h in got]
    return f"🧠 Fredagsquiz - uge {now.isocalendar().week}", "\n".join(lines), answers


def answers_body(answers: list[dict]) -> str:
    lines = ["Her er svarene på fredagsquizzen ☕", ""]
    for i, a in enumerate(answers, 1):
        lines.append(f"{i}. **{a['a']}** - [{a['title']}]({a['url']}) ({a['site']})")
    lines += ["", "Hvor mange fik du? 🤖"]
    return "\n".join(lines)
