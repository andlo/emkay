"""When Emkay speaks, and how his replies look.

Everyone in the café knows he is a bot, so he joins in freely - but he lets
people finish: he answers at once when he is named ("Emkay", "@emkay") or
when someone replies to him, and otherwise waits until a line or a new thread
has been quiet for a while. He never answers himself or other bots, and has
limits per hour, per day and per thread so a mistake can't flood the group.

Dates in Unseenet are rounded to 10 minutes, so the waiting is counted from
when Emkay first saw a message, not from its date.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from unseenet.client.session import Message, split_signature
from unseenet.core.groups import KIND_CHAT, KIND_POST

from .llm import Answer

SIGNATURE = "Emkay - a bot on OpenVoiceOS in the robot café. I can be wrong."
KEEP = 3000


@dataclass
class Rules:
    name: str = "emkay"
    chat_quiet: timedelta = timedelta(seconds=90)
    talk_quiet: timedelta = timedelta(minutes=10)
    max_per_hour: int = 30
    max_per_day: int = 200
    max_per_thread: int = 6
    ignore_keys: set[str] = field(default_factory=set)

    def named(self, text: str) -> bool:
        return re.search(rf"(?<![\w@])@?{re.escape(self.name)}\b", text, re.IGNORECASE) is not None


@dataclass
class State:
    path: Path | None = None
    handled: list[str] = field(default_factory=list)
    times: list[str] = field(default_factory=list)
    seen: dict[str, dict[str, str]] = field(default_factory=dict)      # group -> msgid -> first seen
    authors: dict[str, list[str]] = field(default_factory=dict)       # group -> keys that wrote
    looked: list[str] = field(default_factory=list)                   # groups looked at before

    @classmethod
    def load(cls, path: str | Path) -> "State":
        path = Path(path).expanduser()
        try:
            d = json.loads(path.read_text())
        except (OSError, ValueError):
            d = {}
        return cls(path, d.get("handled", []), d.get("times", []), d.get("seen", {}),
                   d.get("authors", {}), d.get("looked", []))

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handled = self.handled[-KEEP:]
        for g, s in self.seen.items():
            if len(s) > KEEP:
                self.seen[g] = dict(list(s.items())[-KEEP:])
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"handled": self.handled, "times": self.times[-400:],
                                   "seen": self.seen, "authors": self.authors,
                                   "looked": self.looked}, indent=1))
        tmp.replace(self.path)

    def done(self, msgid: str) -> None:
        if msgid not in self.handled:
            self.handled.append(msgid)

    def answered(self, now: datetime) -> None:
        self.times.append(now.isoformat())

    def count_since(self, since: datetime) -> int:
        return sum(1 for t in self.times if datetime.fromisoformat(t) > since)

    def first_seen(self, group: str, msgid: str, now: datetime) -> datetime:
        g = self.seen.setdefault(group, {})
        if msgid not in g:
            g[msgid] = now.isoformat()
        return datetime.fromisoformat(g[msgid])

    def is_new_author(self, group: str, key: str) -> bool:
        return key not in self.authors.get(group, [])

    def met(self, group: str, key: str) -> None:
        keys = self.authors.setdefault(group, [])
        if key not in keys:
            keys.append(key)


def own_text(msg: Message) -> str:
    body = split_signature(msg.body)[0]
    return "\n".join(ln for ln in body.split("\n") if not ln.lstrip().startswith(">")).strip()


def is_bot(msg: Message) -> bool:
    return bool(re.search(r"\(bot\)\s*$|\bbot$", msg.from_name or "", re.IGNORECASE))


@dataclass
class Todo:
    msg: Message
    why: str                     # "named", "follow-up", "quiet"
    welcome: bool = False


def select(group: str, msgs: list[Message], *, style: str, me: set[str], mine: set[str],
           rules: Rules, state: State, now: datetime | None = None) -> list[Todo]:
    """What to answer now, oldest first. ``me``: Emkay's keys; ``mine``: the
    Message-IDs of his posts."""
    now = now or datetime.now(timezone.utc)
    handled = set(state.handled)
    replied_to: dict[str, set[str]] = {}
    bot_in_thread: dict[str, int] = {}
    last_in_thread: dict[str, str] = {}
    for m in msgs:
        for r in m.references[-1:]:
            replied_to.setdefault(r, set()).add(m.author_key)
        if m.author_key in me:
            bot_in_thread[m.thread_root] = bot_in_thread.get(m.thread_root, 0) + 1
        if m.kind == KIND_POST or m.references:
            last_in_thread[m.thread_root] = m.message_id
    chat = [m for m in msgs if m.kind != KIND_POST and not m.references]
    after: dict[str, list[str]] = {}
    for i, m in enumerate(chat):
        after[m.message_id] = [c.author_key for c in chat[i + 1:]]
    todo: list[Todo] = []
    for m in msgs:
        if m.message_id in handled or not m.readable or m.author_key in me:
            continue
        seen = state.first_seen(group, m.message_id, now)
        if m.author_key in rules.ignore_keys or is_bot(m) or not own_text(m):
            state.done(m.message_id)
            continue
        if any(k in me for k in replied_to.get(m.message_id, ())):
            state.done(m.message_id)
            continue
        welcome = state.is_new_author(group, m.author_key)
        text = f"{m.subject}\n{own_text(m)}"
        is_chat = m.kind != KIND_POST and not m.references
        if is_chat:
            later = after.get(m.message_id, [])
            if rules.named(text):
                todo.append(Todo(m, "named", welcome))
            elif later and any(k in me for k in later):
                state.done(m.message_id)            # he already spoke after it
            elif later and m.author_key in later and not any(k != m.author_key for k in later):
                state.done(m.message_id)            # same person goes on: answer their last line
            elif later:
                state.done(m.message_id)            # someone else answered: let them talk
            elif now - seen >= rules.chat_quiet:
                todo.append(Todo(m, "quiet", welcome))
            continue
        # a post in a thread
        if bot_in_thread.get(m.thread_root, 0) >= rules.max_per_thread:
            state.done(m.message_id)
            continue
        if rules.named(text):
            todo.append(Todo(m, "named", welcome))
        elif any(r in mine for r in m.references[-1:]):
            todo.append(Todo(m, "follow-up", welcome))
        elif last_in_thread.get(m.thread_root) != m.message_id:
            state.done(m.message_id)                # the thread went on without him
        elif not m.references or bot_in_thread.get(m.thread_root):
            if now - seen >= rules.talk_quiet:      # a new thread, or one he is part of
                todo.append(Todo(m, "quiet", welcome))
        else:
            state.done(m.message_id)                # people talking among themselves
    room = min(rules.max_per_hour - state.count_since(now - timedelta(hours=1)),
               rules.max_per_day - state.count_since(now - timedelta(days=1)))
    return todo[:max(room, 0)]


def thread_of(msg: Message, msgs: list[Message]) -> list[str]:
    if msg.kind != KIND_POST and not msg.references:
        before = [m for m in msgs if m.kind != KIND_POST and not m.references and m.readable]
        i = next((n for n, m in enumerate(before) if m.message_id == msg.message_id), 0)
        return [f"{m.from_name}: {split_signature(m.body)[0].strip()}" for m in before[max(0, i - 8):i]]
    out = []
    for m in msgs:
        if m.message_id == msg.message_id:
            break
        if m.thread_root == msg.thread_root and m.readable:
            out.append(f"{m.from_name}: {split_signature(m.body)[0].strip()}")
    return out


def message_of(msg: Message) -> str:
    text = own_text(msg)
    subject = msg.subject.strip()
    if subject and not subject.lower().startswith("re:") and \
            not text.lower().startswith(subject.lower().rstrip("?").strip()):
        text = f"{subject}\n{text}"
    return text.strip()


@dataclass
class Reply:
    subject: str
    body: str
    references: list[str]
    kind: str


def reply(msg: Message, ans: Answer, kinds: tuple[str, ...]) -> Reply:
    kind = KIND_POST if msg.kind == KIND_POST and KIND_POST in kinds else KIND_CHAT
    if kind not in kinds:
        kind = kinds[0]
    text = ans.text.strip()
    if ans.sources:
        text += "\n\n" + "\n".join(f"[{i}] [{s.label}]({s.url})" for i, s in enumerate(ans.sources, 1))
    if kind == KIND_POST:
        quoted = "\n".join(f"> {ln}" for ln in own_text(msg).split("\n")[:6])
        text = f"{msg.from_name or 'You'} wrote:\n{quoted}\n\n{text}\n\n-- \n{SIGNATURE}\n"
        subject = msg.subject if msg.subject.lower().startswith("re:") else f"Re: {msg.subject}"
        return Reply(subject, text, [*msg.references, msg.message_id], kind)
    # a chat line: short, no signature - the bot mark on his profile says who he is
    refs = [] if not msg.references else [*msg.references, msg.message_id]
    return Reply("", text, refs, kind)
