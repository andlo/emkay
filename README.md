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

<p align="center"><img src="assets/screenshot-chat.png" width="880" alt="Emkay in alt.robot.cafe.chat, in the Unseenet web client: a welcome and the time in Danish, Canberra in English, a unit conversion and a coffee preference in Spanish"></p>
<p align="center"><sub>Danish, English and Spanish in one chat: OVOS skills answer the time, the capital and the
conversion; the Emkay persona answers the coffee question.</sub></p>

## The name

"Emkay" is "MK" said out loud: a nod to Mycroft's Mark I and Mark II, the open source
voice assistants whose work [OpenVoiceOS](https://openvoiceos.org) carries on. No, he
is not a Mark III – just a bot who likes coffee and facts.

## How he works

Emkay **is** an OpenVoiceOS assistant – a stock, headless OVOS (messagebus and
ovos-core, no audio) – with the Unseenet café as his mouth and ears instead of a
microphone and a speaker:

```
café message ─► Emkay bridge ─► OVOS messagebus ─► ovos-core
                                                    ├─ skills: date-time, weather, wikipedia,
                                                    │  calculator, geometry, geography, convert,
                                                    │  holidays, jokes, days in history, …
                                                    └─ ovos-persona "Emkay" (when no skill answers):
                                                       a local LLM through ovos-solver-openai-plugin
café reply  ◄─ Emkay bridge ◄─ "speak" ◄────────────┘
```

- **Skills first.** What a skill can answer, it answers – exact time, sums, conversions,
  weather, capitals. The [Emkay persona](contrib/install-ovos.sh) (ovos-persona with
  Qwen 2.5 7B on Ollama, all local) takes the rest: chit-chat, opinions, explanations.
- **The bridge** ([`src/emkay`](src/emkay)) only decides when he speaks, gives every thread
  its own OVOS session (so the persona remembers the conversation), tells the language
  of a message offline ([Lingua](https://github.com/pemistahl/lingua-py)), welcomes
  newcomers and quotes in the discussion group.
- **Languages:** English, Danish, German, Swedish, Norwegian, Dutch, French, Spanish,
  Italian and Portuguese – as far as each skill is translated; the persona speaks them all.

## How he behaves

- Answers at once when named – "Emkay, …" or "@emkay".
- Otherwise chimes in when a chat line has gone unanswered for a minute and a half, or
  a new thread for ten minutes – humans first.
- Welcomes people the first time they write.
- Never answers himself or other bots, and has limits per hour, per day and per thread.

## He follows the news

Emkay reads the headlines [newsbot](https://github.com/andlo/newsbot) posts in
`dk.newsbot.*` (DR, TV 2's regions, Arbejderen and more) and remembers them for a week
([`news.py`](src/emkay/news.py)):

- **"Hvad sker der i dag?"** in the café: the newest headlines, a few from each medium,
  with links and `!dk.newsbot.…` references to talk about them there.
- **"Fortæl mere om …"** - or `@emkay` under a headline in a newsbot group: first a
  "one moment, I'll grab a coffee ☕ and read the article" (one of a few, at random), then
  he reads the article and edits that reply into his answer - two to five sentences in
  his own words, with the link ([`reader.py`](src/emkay/reader.py)).
- **"Hvad skriver de andre om …?"**: the same story as each medium headlines it, side by side.
- **His own feed** ([`paper.py`](src/emkay/paper.py)) - follow him to get it in your
  timeline: ☕ *Morgenkaffe* every morning with the night's headlines, and a 🧠
  *Fredagsquiz* on the week's news (each answer is a word from a headline, so he can't
  make one up; the answers come the next day).

**He only reads where he may.** Before he reads an article he checks the site's
`robots.txt` - also for the agents that fetch a page because a person asked an AI
(`Claude-User`, `ChatGPT-User`, `Perplexity-User`). DR, TV 2, Videnskab.dk and Ekstra
Bladet shut those out, so for them he finds the same story at a medium that lets him
read (and says so), or gives the link. He reads over Tor, only when asked, and never
repeats the article's text.

Articles and the quiz go to his model directly ([`llm.py`](src/emkay/llm.py)), with the
persona's own system prompt, rather than through OVOS's intents: an article's text could
otherwise set off a skill.

## Back to OVOS

Running OVOS behind a chat shakes out things a voice setup rarely hits. Fixes go back
upstream – see [UPSTREAM.md](UPSTREAM.md).

## Running him

```sh
sudo bash contrib/install.sh                  # Debian/Ubuntu LXC: Ollama, OVOS, the bridge
emkay -c emkay.toml ask "Hvad er klokken?"    # ask his OVOS, without Unseenet
journalctl -u emkay -u emkay-ovos-core -f
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
