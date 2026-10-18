<p align="center"><img src="assets/emkay-256.png" width="220" alt="Emkay, a Mark I face"></p>

<p align="center"><em>Hi! I'm Emkay – let's talk and have a coffee in my café at
<code>alt.robot.cafe</code> on Unseenet.</em></p>

# Emkay

A friendly, fact-based bot who runs a small robot café on [Unseenet](https://unseenet.org):

| Group | What it's for |
|---|---|
| `alt.robot.cafe.chat` | Quick chat – short answers, fast |
| `alt.robot.cafe.talk` | Longer discussions, Usenet style – Emkay quotes and cites sources |

Everyone in the café knows Emkay is a bot, so he joins conversations freely – but people
are just as welcome to talk with each other.

## The name

"Emkay" is "MK" said out loud: a nod to Mycroft's Mark I and Mark II, the open source
voice assistants whose work [OpenVoiceOS](https://openvoiceos.org) carries on. No, he
is not a Mark III – just a bot who likes coffee and facts.

## What he knows

- **Unseenet and OpenVoiceOS** – the README of every public repository of both GitHub
  organisations, plus all of the Unseenet docs and specs and the
  [OVOS technical manual](https://github.com/OpenVoiceOS/ovos-technical-manual).
- **The world** – Wikipedia and DuckDuckGo through the OVOS
  [Wikipedia](https://github.com/OpenVoiceOS/ovos-wikipedia-plugin) and
  [DuckDuckGo](https://github.com/OpenVoiceOS/ovos-ddg-solver-plugin) plugins.
- **Exact things a small model gets wrong** – a calculator (also for geometry), unit
  conversion, the clock and week number anywhere, public holidays, weather
  (Open-Meteo), places and distances (OpenStreetMap), what happened on this day
  (Wikipedia), word meanings (Wiktionary), scientific papers (arXiv) and dad jokes.

He thinks with a small language model running locally (Ollama, Qwen 2.5 7B), replies
in the language you write in, and says so when he isn't sure.

## How he behaves

- Answers at once when named – "Emkay, …" or "@emkay".
- Otherwise chimes in when a chat line has gone unanswered for a minute and a half, or
  a new thread for ten minutes – humans first.
- Welcomes people the first time they write.
- Never answers himself or other bots, and has limits per hour, per day and per thread.

## Running him

```sh
sudo bash contrib/install.sh               # on a Debian/Ubuntu LXC: everything, as a service
emkay -c emkay.toml ask "Hvad er OVOS?"    # try him without Unseenet
journalctl -u emkay -f
```

See [`emkay.example.toml`](emkay.example.toml) for the knobs.

---

<p align="center">
  <a href="https://unseenet.org">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="assets/unseenet-logo-dark.svg">
      <img src="assets/unseenet-logo-light.svg" width="200" alt="Unseenet">
    </picture>
  </a>
</p>

<p align="center"><sub>Emkay lives on Unseenet. A private hobby project, not affiliated
with the OpenVoiceOS project or Mycroft AI.</sub></p>
