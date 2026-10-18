"""Run Emkay.

    emkay -c emkay.toml init        # vault, key, profile and the two café groups
    emkay -c emkay.toml sync        # fetch the Unseenet and OpenVoiceOS repositories
    emkay -c emkay.toml ask "Hvad er OVOS?"     # try him locally, no Unseenet
    emkay -c emkay.toml run         # sit in the café

The vault's passphrase is read from UNSEENET_VAULT_PASSPHRASE.
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

from . import bot, llm, repos
from .docs import Index
from .world import World

PASS_ENV = "UNSEENET_VAULT_PASSPHRASE"
ABOUT = ("I'm Emkay - \"MK\" said out loud, a nod to Mycroft's Mark I and Mark II. A friendly, "
         "fact-based bot running on OpenVoiceOS (OVOS), living in alt.robot.cafe.chat and "
         "alt.robot.cafe.talk. A private hobby project by andlo, not affiliated with the "
         "OpenVoiceOS project or Mycroft AI. I can be wrong.")
BLURB = ("Emkay is a bot and joins conversations freely; humans are welcome to talk with each "
         "other too. Emkay runs on OpenVoiceOS (OVOS). The name is \"MK\" said out loud, a nod "
         "to Mycroft's Mark I and Mark II, the open source voice assistants whose work OVOS "
         "carries on.\n\nA private hobby project, not affiliated with the OpenVoiceOS project "
         "or Mycroft AI.")
GROUPS = {
    "chat": dict(name="alt.robot.cafe.chat", title="Robot café: chat",
                 description="Quick chat with Emkay, a friendly, fact-based assistant bot.\n\n" + BLURB,
                 view="chat", kinds=["chat"]),
    "talk": dict(name="alt.robot.cafe.talk", title="Robot café: talk",
                 description="Longer discussions, Usenet style. Emkay quotes and cites "
                             "sources.\n\n" + BLURB,
                 view="threads", kinds=["post"]),
}


DOCS_REPOS = ["unseenet/unseenet", "OpenVoiceOS/ovos-technical-manual"]


@dataclass
class Config:
    vault: Path
    state_file: Path
    name: str = "Emkay (bot)"
    nodes: list[str] = field(default_factory=list)
    poll: float = 30.0
    rules: bot.Rules = field(default_factory=bot.Rules)
    model: llm.Model = field(default_factory=llm.Model)
    repos: list = field(default_factory=list)
    repos_cache: Path = Path("~/.local/state/emkay/repos.json").expanduser()
    repos_hours: float = 12.0
    wikipedia: bool = True
    duckduckgo: bool = True


def load_config(path: str | Path) -> Config:
    path = Path(path).expanduser()
    d = tomllib.loads(path.read_text())
    b, m, g, w = d.get("bot", {}), d.get("model", {}), d.get("github", {}), d.get("world", {})
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
            model=llm.Model(base_url=m.get("base_url", llm.Model.base_url),
                            model=m.get("model", llm.Model.model),
                            api_key=os.environ.get(m["api_key_env"], "") if m.get("api_key_env") else "",
                            timeout=float(m.get("timeout", 600))),
            repos=[repos.Repos(owner=o, exclude=set(g.get("exclude", [])),
                               full_docs={r.split("/", 1)[1] for r in g.get("docs_repos", DOCS_REPOS)
                                          if r.split("/", 1)[0] == o},
                               token=os.environ.get(g["token_env"], "") if g.get("token_env") else "")
                   for o in g.get("owners", ["unseenet", "OpenVoiceOS"])] if g.get("enabled", True) else [],
            repos_cache=Path(g.get("cache_file", "~/.local/state/emkay/repos.json")).expanduser(),
            repos_hours=float(g.get("refresh_hours", 12)),
            wikipedia=bool(w.get("wikipedia", True)), duckduckgo=bool(w.get("duckduckgo", True)))
    except KeyError as exc:
        raise SystemExit(f"{path}: missing {exc}") from exc


def open_session(cfg: Config, log=print) -> Session:
    pw = os.environ.get(PASS_ENV)
    if not pw:
        raise SystemExit(f"set {PASS_ENV} to the vault's passphrase")
    return Session(Vault.open(cfg.vault, pw), status=log)


def build_index(cfg: Config) -> Index:
    cache = repos.load_cache(cfg.repos_cache)
    return Index([c for o in cache.get("owners", {}).values() for c in repos.chunks(o)])


async def sync_repos(cfg: Config, force: bool = False, log=print) -> None:
    if not cfg.repos:
        return
    cache = repos.load_cache(cfg.repos_cache)
    if not force and datetime.now(timezone.utc).timestamp() - cache.get("synced", 0) < cfg.repos_hours * 3600:
        return
    owners = dict(cache.get("owners", {}))
    for r in cfg.repos:
        try:
            fresh = await asyncio.get_running_loop().run_in_executor(None, r.sync, log)
        except (OSError, ValueError, KeyError) as exc:
            log(f"repos: could not sync {r.owner}: {exc}")
            continue
        if fresh["repos"]:
            owners[r.owner] = fresh
    owners = {o: v for o, v in owners.items() if o in {r.owner for r in cfg.repos}}
    repos.save_cache(cfg.repos_cache, {"synced": datetime.now(timezone.utc).timestamp(), "owners": owners})


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


async def publish_profile(s: Session, log=print) -> None:
    if s.data.get("emkay_profile"):
        return
    from unseenet.core.profiles import make_profile
    try:
        await s.publish_profile(make_profile(about=ABOUT, lang=["en", "da", "de", "sv", "no"], bot=True))
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


async def cmd_ask(cfg: Config, question: str, style: str) -> None:
    ans = await llm.answer(cfg.model, build_index(cfg), World(cfg.wikipedia, cfg.duckduckgo),
                           question, style=style)
    print(ans.text)
    for i, f in enumerate(ans.sources, 1):
        print(f"[{i}] {f.label} - {f.url}")


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


async def look_once(s: Session, cfg: Config, index: Index, world: World, state: bot.State,
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
            text = bot.message_of(t.msg)
            log(f"{style} {t.why}: {t.msg.message_id} {text[:60]!r}")
            try:
                ans = await llm.answer(cfg.model, index, world, text, style=style,
                                       thread=bot.thread_of(t.msg, msgs),
                                       author=t.msg.from_name or "", welcome=t.welcome)
            except (OSError, ValueError, KeyError) as exc:
                log(f"  the model did not answer: {exc}")
                break
            if not ans.text:
                state.done(t.msg.message_id)
                continue
            r = bot.reply(t.msg, ans, s.group_kinds(gid))
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
    world = World(cfg.wikipedia, cfg.duckduckgo)
    await sync_repos(cfg)
    index, built = build_index(cfg), datetime.now(timezone.utc)
    print(f"{len(index.chunks)} sections from {', '.join(r.owner for r in cfg.repos) or 'no'} repositories; "
          f"sitting in {', '.join(groups(s).values())}")
    try:
        if not dry_run:
            await publish_profile(s)
            await publish_face(s)
        while True:
            if datetime.now(timezone.utc) - built > timedelta(hours=1):
                await sync_repos(cfg)
                index, built = build_index(cfg), datetime.now(timezone.utc)
            try:
                await look_once(s, cfg, index, world, state, dry_run)
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
    a = sub.add_parser("ask", help="ask Emkay one thing, without Unseenet")
    a.add_argument("question")
    a.add_argument("--style", choices=("chat", "talk"), default="chat")
    sub.add_parser("sync", help="fetch the repositories from GitHub now")
    r = sub.add_parser("run", help="sit in the café and talk")
    r.add_argument("--once", action="store_true")
    r.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    try:
        if args.cmd == "init":
            asyncio.run(cmd_init(cfg))
        elif args.cmd == "ask":
            asyncio.run(cmd_ask(cfg, args.question, args.style))
        elif args.cmd == "sync":
            asyncio.run(sync_repos(cfg, force=True))
            print(f"{len(build_index(cfg).chunks)} sections in all")
        else:
            asyncio.run(cmd_run(cfg, args.once, args.dry_run))
    except KeyboardInterrupt:
        sys.exit(130)
