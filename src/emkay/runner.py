"""Run Emkay: an OpenVoiceOS assistant sitting in the Unseenet robot café.

    emkay -c emkay.toml init        # vault, key, profile and the two café groups
    emkay -c emkay.toml ask "Hvad er klokken?"    # ask his OVOS, without Unseenet
    emkay -c emkay.toml run         # sit in the café

The vault's passphrase is read from UNSEENET_VAULT_PASSPHRASE. OVOS (messagebus
and ovos-core with skills and the Emkay persona) must be running - see
contrib/install-ovos.sh.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import os
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from unseenet.client.session import Session, SessionError
from unseenet.core.vault import Vault

from . import bot, news, paper
from .llm import Llm
from .ovos import Ovos, detect_lang
from .reader import NotAllowed, Reader

PASS_ENV = "UNSEENET_VAULT_PASSPHRASE"
ABOUT = """\
# Emkay :robot:

Hi, I'm Emkay - a friendly robot who hangs out in the **robot café**.
Come and say hi:

- [alt.robot.cafe.chat](unseenet:join?g=alt.robot.cafe.chat@w2ie6skhfxk4qprt) -
  just chat with me
- [alt.robot.cafe.talk](unseenet:join?g=alt.robot.cafe.talk@w2ie6skhfxk4qprt) -
  longer discussions, one post at a time

## About me

- I run on [OpenVoiceOS](https://openvoiceos.org), the open source voice assistant
- I look things up on Wikipedia and the web, and I learn new skills along the way
- Name me ("Emkay, ..." or `@emkay`) and I answer right away
- My name is "MK" - a nod to the old Mycroft Mark I and Mark II robots

I am a robot, so I can be wrong. A hobby project by
[Andreas](unseenet:b4xkhrco5yii4dw7) - [my code on GitHub](https://github.com/andlo/emkay).
Not affiliated with the OpenVoiceOS project or Mycroft AI.
"""   # his public profile, Markdown (UN-RFC 0022 section 4.1)
PROFILE_LINKS = [{"kind": "github", "value": "https://github.com/andlo/emkay"},
                 {"kind": "web", "value": "https://openvoiceos.org"}]
BLURB = ("Emkay is a bot and joins conversations freely; humans are welcome to talk with each "
         "other too. Emkay is an OpenVoiceOS (OVOS) assistant. The name is \"MK\" said out loud, "
         "a nod to Mycroft's Mark I and Mark II, the open source voice assistants whose work OVOS "
         "carries on.\n\nA private hobby project, not affiliated with the OpenVoiceOS project "
         "or Mycroft AI.")
GROUPS = {
    "chat": dict(name="alt.robot.cafe.chat", title="Robot café: chat",
                 description="Quick chat with Emkay, a friendly, fact-based assistant bot.\n\n" + BLURB,
                 view="chat", kinds=["chat"], languages=["en", "da"]),
    "talk": dict(name="alt.robot.cafe.talk", title="Robot café: talk",
                 description="Longer discussions, Usenet style. Emkay quotes what he answers.\n\n" + BLURB,
                 view="threads", kinds=["post"], languages=["en", "da"]),
}
PROFILE_LANGS = ["en", "da", "de", "sv", "nb", "nl", "fr", "es"]   # a profile lists at most 8
WELCOME = {
    "da": "Velkommen til caféen, {name}! ☕", "en": "Welcome to the café, {name}! ☕",
    "de": "Willkommen im Café, {name}! ☕", "sv": "Välkommen till kaféet, {name}! ☕",
    "nb": "Velkommen til kafeen, {name}! ☕", "nl": "Welkom in het café, {name}! ☕",
    "fr": "Bienvenue au café, {name} ! ☕", "es": "¡Bienvenido al café, {name}! ☕",
    "it": "Benvenuto al caffè, {name}! ☕", "pt": "Bem-vindo ao café, {name}! ☕",
}


@dataclass
class Config:
    vault: Path
    state_file: Path
    name: str = "Emkay (bot)"
    nodes: list[str] = field(default_factory=list)
    poll: float = 30.0
    rules: bot.Rules = field(default_factory=bot.Rules)
    ovos: Ovos = field(default_factory=Ovos)
    default_lang: str = "en"
    news_groups: list[str] = field(default_factory=list)    # dk.newsbot.*@owner (news.py)
    news_file: Path = Path("~/.local/state/emkay/news.json").expanduser()
    llm: Llm = field(default_factory=Llm)
    schedule: paper.Schedule = field(default_factory=paper.Schedule)


def load_config(path: str | Path) -> Config:
    path = Path(path).expanduser()
    d = tomllib.loads(path.read_text())
    b, o = d.get("bot", {}), d.get("ovos", {})
    nw, lm, pp = d.get("news", {}), d.get("llm", {}), d.get("paper", {})
    try:
        return Config(
            vault=Path(b["vault"]).expanduser(),
            state_file=Path(b.get("state_file", "~/.local/state/emkay/state.json")).expanduser(),
            name=b.get("name", "Emkay (bot)"), nodes=b.get("nodes", []),
            poll=float(b.get("poll_seconds", 30)),
            rules=bot.Rules(chat_quiet=timedelta(seconds=b.get("chat_quiet_seconds", 90)),
                            talk_quiet=timedelta(minutes=b.get("talk_quiet_minutes", 10)),
                            max_per_hour=int(b.get("max_per_hour", 30)),
                            max_per_day=int(b.get("max_per_day", 200)),
                            max_per_thread=int(b.get("max_per_thread", 6)),
                            ignore_keys=set(b.get("ignore_keys", []))),
            ovos=Ovos(host=o.get("host", "127.0.0.1"), port=int(o.get("port", 8181)),
                      timeout=float(o.get("timeout", 240))),
            default_lang=o.get("default_lang", "en"),
            news_groups=list(nw.get("groups", [])),
            news_file=Path(nw.get("file", "~/.local/state/emkay/news.json")).expanduser(),
            llm=Llm(url=lm.get("url", "http://127.0.0.1:11434/v1"),
                    model=lm.get("model", "emkay-qwen"), timeout=float(lm.get("timeout", 300))),
            schedule=paper.Schedule(tz=pp.get("tz", "Europe/Copenhagen"),
                                 morning=pp.get("morning", "07:30"),
                                 quiz_day=pp.get("quiz_day", "fri"),
                                 quiz_time=pp.get("quiz_time", "15:00"),
                                 enabled=bool(pp.get("enabled", True))))
    except KeyError as exc:
        raise SystemExit(f"{path}: missing {exc}") from exc


def open_session(cfg: Config, log=print) -> Session:
    pw = os.environ.get(PASS_ENV)
    if not pw:
        raise SystemExit(f"set {PASS_ENV} to the vault's passphrase")
    return Session(Vault.open(cfg.vault, pw), status=log)


def groups(s: Session) -> dict[str, str]:
    """style -> group ID"""
    return dict(s.data.get("emkay_groups", {}))


async def cmd_init(cfg: Config) -> None:
    s = open_session(cfg)
    try:
        if not s.has_identity():
            s.new_identity("emkay", cfg.name)
        nodes = cfg.nodes
        if not nodes:
            from unseenet.core.poster import known_node_addresses
            nodes = known_node_addresses()
        for n in nodes:
            s.add_node(n)
        s.save()
        print(f"key:  {s.identity().key_text}")
        print(f"name: {s.display_name()}")
        mine = groups(s)
        for style, spec in GROUPS.items():
            if style in mine:
                continue
            gid = await s.create_group(spec["name"], "open", title=spec["title"],
                                       description=spec["description"], view=spec["view"],
                                       kinds=spec["kinds"], extra={"languages": spec["languages"]})
            mine[style] = gid
            s.data["emkay_groups"] = mine
            s.save()
            print(f"created {gid}")
        for gid in mine.values():
            s.subscribe(gid)
        await publish_profile(s)
        s.save()
        for style, gid in mine.items():
            print(f"{style}: {gid}")
    finally:
        await s.close()


async def cmd_describe(cfg: Config) -> None:
    """Publish the current group descriptions and profile text (after they change)."""
    s = open_session(cfg)
    try:
        for style, gid in groups(s).items():
            spec = GROUPS[style]
            await s.update_group(gid, title=spec["title"], description=spec["description"],
                                 languages=spec["languages"])       # UN-RFC 0052 5a
            print(f"updated {gid}")
        await s.publish_profile(profile())
        s.data["emkay_profile"] = profile_mark()
        s.save()
        print("updated the profile")
    finally:
        await s.close()


def header_lang(lang: str) -> str:
    """The code for the Content-Language header: Norwegian Bokmål as ``no``, as the
    clients' list of languages has it; the others as they are."""
    return "no" if lang == "nb" else lang


def profile() -> dict:
    """His public profile: the bot mark, Markdown About text, links and languages."""
    from unseenet.core.profiles import make_profile
    return make_profile(about=ABOUT, links=PROFILE_LINKS, lang=PROFILE_LANGS,
                        bot=True, markdown=True)


def profile_mark() -> str:
    """A short hash of the profile, kept in the vault: a changed text is published again."""
    import hashlib
    import json
    return hashlib.sha256(json.dumps(profile(), sort_keys=True).encode()).hexdigest()[:16]


async def publish_profile(s: Session, log=print) -> None:
    """At start: publish the profile when it is new or its text has changed."""
    first = not s.data.get("emkay_profile")
    if s.data.get("emkay_profile") == profile_mark():
        return
    try:
        await s.publish_profile(profile())
    except (SessionError, OSError, ConnectionError) as exc:
        log(f"could not publish the profile yet: {exc}")
        return
    s.data["emkay_profile"] = profile_mark()
    s.save()
    log("published the profile with the bot mark" if first else "published the changed profile")
    await publish_face(s, log)


async def publish_face(s: Session, log=print) -> None:
    """His profile picture: a Mark I face (UN-RFC 0022, section 8)."""
    if s.data.get("emkay_picture"):
        return
    from importlib.resources import files
    from unseenet.core.profiles import make_picture
    data = files("emkay").joinpath("emkay-profile.webp").read_bytes()
    try:
        await s.publish_picture(make_picture(data, "image/webp", 128, alt="Emkay, a Mark I face"))
    except Exception as exc:                      # noqa: BLE001 - nodes without avatar groups
        log(f"could not publish the picture yet: {exc}")
        return
    s.data["emkay_picture"] = True
    s.save()
    log("published the profile picture")


async def cmd_ask(cfg: Config, question: str) -> None:
    text = bot.strip_name(question, cfg.rules.name)
    lang = detect_lang(text, cfg.default_lang)
    said = await cfg.ovos.ask(text, lang, "emkay-cli")
    print(f"({lang}) " + ("\n".join(said) if said else "(OVOS said nothing)"))


async def bot_keys(s: Session, keys: set[str]) -> set[str]:
    out = set()
    for k in keys:
        try:
            p = await s.profile_of(k)
        except Exception:                       # noqa: BLE001
            continue
        if p is not None and getattr(p, "bot", False):
            out.add(k)
    return out


LAST_LANG: dict[str, str] = {}      # OVOS session -> the language last spoken there


def session_id(style: str, gid: str, msg) -> str:
    """One OVOS session per thread (talk) or per group (chat)."""
    root = msg.thread_root if style == "talk" else gid
    return "emkay-" + "".join(c if c.isalnum() else "-" for c in f"{style}-{root}")[:120]


@dataclass
class News:
    """What Emkay knows of the news and how he reads an article (news.py, reader.py)."""
    store: news.NewsStore
    reader: Reader


async def _post(s: Session, gid: str, msg, text: str, lang: str, dry_run: bool, log) -> str | None:
    r = bot.reply(msg, bot.Answer(text), s.group_kinds(gid))
    if dry_run:
        log(f"  would post ({r.kind}) {r.subject!r}:\n{r.body}")
        return None
    msgid = await s.post(gid, r.subject, r.body, references=r.references, kind=r.kind,
                         languages=[header_lang(lang)])
    log(f"  posted {msgid}")
    return msgid


async def _edit_or_post(s: Session, gid: str, msg, wait_id: str | None, text: str, lang: str,
                        dry_run: bool, log) -> None:
    """Turn the 'one moment' reply into the answer (UN-RFC 0042); post it if that fails."""
    if dry_run or not wait_id:
        await _post(s, gid, msg, text, lang, dry_run, log)
        return
    r = bot.reply(msg, bot.Answer(text), s.group_kinds(gid))
    mine = s.find_message(gid, wait_id)
    if mine is None:
        mine = next((m for m in await s.fetch_group(gid) if m.message_id == wait_id), None)
    if mine is not None:
        try:
            log(f"  edited {wait_id} into the answer: "
                f"{await s.edit(mine, r.subject, r.body, languages=[header_lang(lang)])}")
            return
        except (SessionError, OSError, ConnectionError) as exc:
            log(f"  could not edit {wait_id}: {exc}")
    await _post(s, gid, msg, text, lang, dry_run, log)


async def _read_for(s: Session, nw: News, gid: str, msg, head: news.Headline, question: str,
                    lang: str, dry_run: bool, log) -> None:
    """'One moment, coffee and the article' - then read it and edit the reply into the answer.

    When the medium shuts out AI agents (robots.txt), he reads the same story from another
    medium that lets him, and says so."""
    wait_id = await _post(s, gid, msg, news.say(news.WAIT, lang), lang, dry_run, log)
    if nw.reader.socks is None:
        nw.reader.socks = await s._ensure_tor(lambda *a, **k: None)
    tries = [head] + [h for h in nw.store.compare(head.title) if h.site != head.site][:4]
    blocked: list[str] = []
    answer = None
    for h in tries:
        try:
            answer = await nw.reader.answer(h.url, h.title, h.site, question, lang)
        except NotAllowed:
            log(f"  {h.site} shuts out AI agents")
            blocked.append(h.site)
            continue
        except Exception as exc:                    # noqa: BLE001 - NoArticle, the model, Tor
            log(f"  could not read {h.url}: {exc}")
            if h is head:
                answer = news.say(news.NO_ARTICLE, lang, link=head.md())
                break
            continue
        if blocked:
            answer = news.say(news.ELSEWHERE, lang, sites=" og ".join(dict.fromkeys(blocked))
                              if lang == "da" else " and ".join(dict.fromkeys(blocked)),
                              site=h.site) + answer
        break
    if answer is None:
        answer = news.say(news.NO_ROBOTS, lang, site=head.site, link=head.md())
    await _edit_or_post(s, gid, msg, wait_id, answer, lang, dry_run, log)


async def respond(s: Session, cfg: Config, nw: News | None, gid: str, style: str, msg,
                  welcome: bool, dry_run: bool, log) -> bool:
    """Answer one message: the news first (news.py), else OVOS. False when OVOS is away."""
    text = bot.strip_name(bot.message_of(msg), cfg.rules.name)
    sid = session_id(style, gid, msg)
    lang = LAST_LANG[sid] = detect_lang(text, LAST_LANG.get(sid, cfg.default_lang))
    ask = news.ask_of(text) if nw else None
    hello = ""
    if welcome:
        hello = WELCOME.get(lang, WELCOME["en"]).format(name=msg.from_name or "")
        hello = hello.replace(" , ", ", ").replace(", !", "!") + "\n\n"
    if ask:
        log(f"  news: {ask.what} {ask.topic!r}")
        if ask.what == "today":
            await _post(s, gid, msg, hello + news.today(nw.store, lang, short=style == "chat"),
                        lang, dry_run, log)
        elif ask.what == "compare":
            await _post(s, gid, msg, hello + news.compared(nw.store, ask.topic, lang),
                        lang, dry_run, log)
        else:
            found = nw.store.find(ask.topic)
            if not found:
                await _post(s, gid, msg, hello + news.say(news.NOT_FOUND, lang), lang, dry_run, log)
            else:
                await _read_for(s, nw, gid, msg, found[0], text, lang, dry_run, log)
        return True
    try:
        said = await cfg.ovos.ask(text, lang, sid)
    except (OSError, ConnectionError) as exc:
        log(f"  OVOS did not answer: {exc}")
        return False
    if not said:
        log("  OVOS said nothing; staying quiet")
        return True
    answer = ("\n\n" if style == "talk" else "\n").join(said)
    await _post(s, gid, msg, hello + answer, lang, dry_run, log)
    return True


def _handled_news(nw: News) -> list[str]:
    return nw.store.extra.setdefault("handled", [])


async def look_news(s: Session, cfg: Config, state: bot.State, nw: News, me: set[str],
                    dry_run: bool = False, log=print) -> None:
    """Read newsbot's groups: remember the headlines, and answer '@emkay' under a headline
    by reading its article. Also his own feed: comments that name him."""
    now = datetime.now(timezone.utc)
    handled = _handled_news(nw)
    feed = s.personal_group("feed")
    watch = list(cfg.news_groups) + ([feed] if nw.store.extra.get("feed_started") else [])
    for gid in watch:
        try:
            msgs = s.visible(await s.fetch_group(gid))
        except (SessionError, OSError, ConnectionError) as exc:
            log(f"news: could not read {gid}: {exc}")
            continue
        if gid != feed:
            new = nw.store.ingest(msgs, gid, now)
            if new:
                log(f"news: {new} new headline(s) in {gid.split('@')[0]}")
        comments = [m for m in msgs if m.references and m.readable and m.author_key not in me]
        if gid not in nw.store.extra.setdefault("looked", []):
            handled.extend(m.message_id for m in comments)      # first look: leave them be
            nw.store.extra["looked"].append(gid)
            continue
        roots = {m.message_id: m for m in msgs if not m.references}
        for m in comments:
            if m.message_id in handled:
                continue
            if bot.is_bot(m) or not cfg.rules.named(f"{m.subject}\n{bot.own_text(m)}"):
                handled.append(m.message_id)
                continue
            if state.count_since(now - timedelta(hours=1)) >= cfg.rules.max_per_hour:
                break                                   # later: still not handled
            log(f"news: named in {gid.split('@')[0]}: {m.message_id}")
            if gid == feed:
                ok = await respond(s, cfg, nw, gid, "talk", m, False, dry_run, log)
            else:
                root = roots.get(m.references[0])
                head = next((h for h in nw.store.headlines if h.msgid == m.references[0]), None) \
                    or (news.headline_of(root, gid, now) if root else None)
                question = bot.strip_name(bot.own_text(m), cfg.rules.name)
                if head is None:
                    ok = await respond(s, cfg, nw, gid, "talk", m, False, dry_run, log)
                else:
                    lang = detect_lang(question, "da")
                    await _read_for(s, nw, gid, m, head, question, lang, dry_run, log)
                    ok = True
            if not ok:
                break
            state.answered(datetime.now(timezone.utc))
            handled.append(m.message_id)
        del handled[:-3000]
    nw.store.save()


async def look_paper(s: Session, cfg: Config, nw: News, dry_run: bool = False, log=print) -> None:
    """His feed: the morning coffee paper, the Friday quiz and its answers (paper.py)."""
    sch, extra, now = cfg.schedule, nw.store.extra, datetime.now(timezone.utc)
    if not sch.enabled or not cfg.news_groups:
        return
    local_day = now.astimezone(paper.ZoneInfo(sch.tz)).date().isoformat()
    jobs = []
    if sch.morning_due(now, extra.get("morning_last")):
        jobs.append("morning")
    if sch.quiz_due(now, extra.get("quiz_last")):
        jobs.append("quiz")
    quiz = extra.get("quiz") or {}
    if quiz.get("msgid") and not quiz.get("answered") and \
            now - datetime.fromisoformat(quiz["posted"]) >= timedelta(hours=sch.answers_after_hours):
        jobs.append("answers")
    if not jobs:
        return
    feed = await ensure_feed(s, nw, dry_run, log)
    for job in jobs:
        if job == "morning":
            got = await paper.morning_post(nw.store, cfg.llm, now, sch.tz)
            extra["morning_last"] = local_day
            if got:
                await _feed_post(s, feed, got[0], got[1], dry_run, log)
        elif job == "quiz":
            extra["quiz_last"] = local_day
            try:
                got = await paper.quiz_post(nw.store, cfg.llm, now)
            except Exception as exc:                 # noqa: BLE001 - the model failed: no quiz
                log(f"paper: no quiz this week: {exc}")
                got = None
            if got:
                msgid = await _feed_post(s, feed, got[0], got[1], dry_run, log)
                extra["quiz"] = {"msgid": msgid, "subject": got[0], "posted": now.isoformat(),
                                 "answers": got[2], "answered": dry_run}
        else:
            if not dry_run:
                await s.post(feed, "Re: " + quiz["subject"], paper.answers_body(quiz["answers"]),
                             references=[quiz["msgid"]], languages=["da"])
            log(f"paper: answers to {quiz['msgid']}")
            quiz["answered"] = True
    nw.store.save()


async def ensure_feed(s: Session, nw: News, dry_run: bool, log) -> str:
    """His personal feed (UN-RFC 0034): started the first time he has something for it."""
    gid = s.personal_group("feed")
    if not nw.store.extra.get("feed_started") and not dry_run:
        entry = s.data["groups"].get(gid, {})
        if not (entry.get("owner") and entry.get("manifest")):
            gid = await s.start_personal_group("feed", comments="open",
                                               title="Emkay's morgenkaffe ☕")
            log(f"paper: started his feed {gid}")
        nw.store.extra["feed_started"] = True
    return gid


async def _feed_post(s: Session, feed: str, subject: str, body: str, dry_run: bool, log) -> str | None:
    if dry_run:
        log(f"paper: would post {subject!r} to his feed:\n{body}")
        return None
    msgid = await s.post(feed, subject, body, languages=["da"])
    log(f"paper: posted {subject!r} ({msgid})")
    return msgid


async def look_once(s: Session, cfg: Config, state: bot.State,
                    dry_run: bool = False, log=print, nw: News | None = None) -> int:
    posted = 0
    me = {s.identity(n).key_text for n in s.data["identities"]}
    if nw is not None:
        await look_news(s, cfg, state, nw, me, dry_run, log)
        await look_paper(s, cfg, nw, dry_run, log)
    for style, gid in groups(s).items():
        msgs = s.visible(await s.fetch_group(gid))
        now = datetime.now(timezone.utc)
        if gid not in state.looked:              # first look: leave what was there alone
            for m in msgs:
                state.done(m.message_id)
                state.met(gid, m.author_key)
            state.looked.append(gid)
            state.save()
            log(f"{style}: first look, {len(msgs)} earlier messages left alone")
            continue
        rules = cfg.rules
        bots = await bot_keys(s, {m.author_key for m in msgs
                                  if m.message_id not in state.handled and m.author_key not in me})
        if bots:
            rules = dataclasses.replace(rules, ignore_keys=rules.ignore_keys | bots)
        for t in bot.select(gid, msgs, style=style, me=me, mine=s.my_message_ids(),
                            rules=rules, state=state, now=now):
            log(f"{style} {t.why}: {t.msg.message_id} {bot.message_of(t.msg)[:60]!r}")
            try:
                if not await respond(s, cfg, nw, gid, style, t.msg, t.welcome, dry_run, log):
                    break                             # OVOS is away: try again later
            except (SessionError, OSError, ConnectionError) as exc:
                log(f"  could not post: {exc}")
                break
            if not dry_run:
                state.answered(datetime.now(timezone.utc))
                posted += 1
            state.done(t.msg.message_id)
            state.met(gid, t.msg.author_key)
        for m in msgs:                            # anyone who wrote has now been met
            if m.message_id in state.handled:
                state.met(gid, m.author_key)
    state.save()
    return posted


async def cmd_run(cfg: Config, once: bool, dry_run: bool) -> None:
    s = open_session(cfg)
    if not groups(s):
        raise SystemExit("no café groups yet: run init first")
    for gid in groups(s).values():
        if gid not in s.data["subscribed"]:
            s.subscribe(gid)
    state = bot.State.load(cfg.state_file)
    nw = None
    if cfg.news_groups:
        for gid in cfg.news_groups:
            if gid not in s.data["subscribed"]:
                s.subscribe(gid)
        nw = News(news.NewsStore.load(None if dry_run else cfg.news_file), Reader(cfg.llm))
        if dry_run:                              # see what he knows, change nothing
            nw.store.headlines = news.NewsStore.load(cfg.news_file).headlines
    if dry_run:
        state.path = None
    print(f"OVOS on {cfg.ovos.host}:{cfg.ovos.port}; sitting in {', '.join(groups(s).values())}"
          + (f"; following the news in {len(cfg.news_groups)} group(s)" if nw else ""))
    try:
        if not dry_run:
            await publish_profile(s)
            await publish_face(s)
        while True:
            try:
                await look_once(s, cfg, state, dry_run, nw=nw)
            except (SessionError, OSError, ConnectionError) as exc:
                print(f"could not read the groups: {exc}")
            if once:
                break
            await asyncio.sleep(cfg.poll)
    finally:
        await s.close()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="emkay", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", default="emkay.toml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="vault, key, profile and the café groups")
    sub.add_parser("describe", help="publish the current group descriptions and profile text")
    a = sub.add_parser("ask", help="ask Emkay's OVOS one thing, without Unseenet")
    a.add_argument("question")
    r = sub.add_parser("run", help="sit in the café and talk")
    r.add_argument("--once", action="store_true")
    r.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    try:
        if args.cmd == "init":
            asyncio.run(cmd_init(cfg))
        elif args.cmd == "describe":
            asyncio.run(cmd_describe(cfg))
        elif args.cmd == "ask":
            asyncio.run(cmd_ask(cfg, args.question))
        else:
            asyncio.run(cmd_run(cfg, args.once, args.dry_run))
    except KeyboardInterrupt:
        sys.exit(130)
