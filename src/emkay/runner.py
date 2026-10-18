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

from . import bot
from .ovos import Ovos, detect_lang

PASS_ENV = "UNSEENET_VAULT_PASSPHRASE"
ABOUT = ("I'm Emkay - \"MK\" said out loud, a nod to Mycroft's Mark I and Mark II. A friendly, "
         "fact-based OpenVoiceOS (OVOS) assistant living in alt.robot.cafe.chat and "
         "alt.robot.cafe.talk: OVOS skills answer what they can, and a small local language "
         "model the rest. A private hobby project by andlo, not affiliated with the "
         "OpenVoiceOS project or Mycroft AI. I can be wrong.")
BLURB = ("Emkay is a bot and joins conversations freely; humans are welcome to talk with each "
         "other too. Emkay is an OpenVoiceOS (OVOS) assistant. The name is \"MK\" said out loud, "
         "a nod to Mycroft's Mark I and Mark II, the open source voice assistants whose work OVOS "
         "carries on.\n\nA private hobby project, not affiliated with the OpenVoiceOS project "
         "or Mycroft AI.")
GROUPS = {
    "chat": dict(name="alt.robot.cafe.chat", title="Robot café: chat",
                 description="Quick chat with Emkay, a friendly, fact-based assistant bot.\n\n" + BLURB,
                 view="chat", kinds=["chat"]),
    "talk": dict(name="alt.robot.cafe.talk", title="Robot café: talk",
                 description="Longer discussions, Usenet style. Emkay quotes what he answers.\n\n" + BLURB,
                 view="threads", kinds=["post"]),
}
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


def load_config(path: str | Path) -> Config:
    path = Path(path).expanduser()
    d = tomllib.loads(path.read_text())
    b, o = d.get("bot", {}), d.get("ovos", {})
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
            default_lang=o.get("default_lang", "en"))
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
                                       kinds=spec["kinds"])
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
    from unseenet.core.profiles import make_profile
    s = open_session(cfg)
    try:
        for style, gid in groups(s).items():
            spec = GROUPS[style]
            await s.update_group(gid, title=spec["title"], description=spec["description"])
            print(f"updated {gid}")
        await s.publish_profile(make_profile(about=ABOUT, lang=list(WELCOME), bot=True))
        print("updated the profile")
    finally:
        await s.close()


async def publish_profile(s: Session, log=print) -> None:
    if s.data.get("emkay_profile"):
        return
    from unseenet.core.profiles import make_profile
    try:
        await s.publish_profile(make_profile(about=ABOUT, lang=list(WELCOME), bot=True))
    except (SessionError, OSError, ConnectionError) as exc:
        log(f"could not publish the profile yet: {exc}")
        return
    s.data["emkay_profile"] = True
    s.save()
    log("published the profile with the bot mark")
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


async def look_once(s: Session, cfg: Config, state: bot.State,
                    dry_run: bool = False, log=print) -> int:
    posted = 0
    me = {s.identity(n).key_text for n in s.data["identities"]}
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
            text = bot.strip_name(bot.message_of(t.msg), cfg.rules.name)
            sid = session_id(style, gid, t.msg)
            lang = LAST_LANG[sid] = detect_lang(text, LAST_LANG.get(sid, cfg.default_lang))
            log(f"{style} {t.why} ({lang}): {t.msg.message_id} {text[:60]!r}")
            try:
                said = await cfg.ovos.ask(text, lang, sid)
            except (OSError, ConnectionError) as exc:
                log(f"  OVOS did not answer: {exc}")
                break
            if not said:
                log("  OVOS said nothing; staying quiet")
                state.done(t.msg.message_id)
                continue
            answer = ("\n\n" if style == "talk" else "\n").join(said)
            if t.welcome:
                hello = WELCOME.get(lang, WELCOME["en"]).format(name=t.msg.from_name or "")
                answer = f"{hello.replace(' , ', ', ').replace(', !', '!')}\n\n{answer}"
            r = bot.reply(t.msg, bot.Answer(answer), s.group_kinds(gid))
            if dry_run:
                log(f"  would post ({r.kind}) {r.subject!r}:\n{r.body}")
            else:
                try:
                    msgid = await s.post(gid, r.subject, r.body, references=r.references, kind=r.kind)
                except (SessionError, OSError, ConnectionError) as exc:
                    log(f"  could not post: {exc}")
                    break
                log(f"  posted {msgid}")
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
    if dry_run:
        state.path = None
    print(f"OVOS on {cfg.ovos.host}:{cfg.ovos.port}; sitting in {', '.join(groups(s).values())}")
    try:
        if not dry_run:
            await publish_profile(s)
            await publish_face(s)
        while True:
            try:
                await look_once(s, cfg, state, dry_run)
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
