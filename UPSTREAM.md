# Back to OpenVoiceOS

Emkay runs a stock OpenVoiceOS behind a text chat instead of a microphone, which
shakes out things a voice setup rarely hits. What we find goes back upstream; this
file keeps track.

| # | Where | What | Status |
|---|---|---|---|
| 1 | [ovos-skill-convert](https://github.com/andlo/ovos-skill-convert) | Bare `{quantity} i/til/in/to {to_unit}` patterns made padatious take ordinary sentences ("Hvordan har du det i dag?") as conversions, so nothing later in the pipeline got a chance | PR [#7](https://github.com/andlo/ovos-skill-convert/pull/7) |
| 2 | [ovos-skill-wikipedia](https://github.com/OpenVoiceOS/ovos-skill-wikipedia) | Start-up race: `self.wiki` is set after `super().__init__()`, which already registers the common-query handler, so an early question fails with `AttributeError: 'WikipediaSkill' object has no attribute 'wiki'` | to report / PR |
| 3 | [ovos-openai-plugin](https://github.com/OpenVoiceOS/ovos-openai-plugin) | `ovos-solver-openai-plugin` refuses to start without `key`, also for local OpenAI-compatible servers (Ollama, llama.cpp) that need none; Emkay passes a dummy key | to report |
| 4 | [ovos-core](https://github.com/OpenVoiceOS/ovos-core) | `ovos-core[plugins]` installs `ovos-m2v-pipeline`, whose default model needs `scikit-learn` and `skops`, which nothing installs: the pipeline fails to load | to report |
| 5 | skills in general | Skills speak for a voice: "toogtyve tredive" instead of "22:30". A session flag for text clients (and `nice_time(speech=False)` and friends when it is set) would make OVOS a good chat assistant too | idea, to discuss |
| 7 | [ovos-persona](https://github.com/OpenVoiceOS/ovos-persona) 0.9.0a28 (alpha) | The persona pipeline does not load on alpha: `template '' yields an empty sample`. 0.9.0a28 splits each intent file on `"\n"`, so the final newline becomes an empty sample, which current ovos-spec-tools rejects. Already fixed on dev (#234, 13 Oct); Emkay installs dev until a release | ask for an alpha release |
| 8 | [ovos-openai-plugin](https://github.com/OpenVoiceOS/ovos-openai-plugin) 2.0.12a1 (alpha) | `ovos-solver-openai-plugin` is gone (now `ovos-chat-openai-plugin`), so persona files that name it stop loading: `Failed to load 'Emkay': 'ovos-solver-openai-plugin' not installed`. An alias or a clear migration note would help | to report |
| – | ovos-core 2.1.1 (testing) | Loads a slow skill twice at start-up (no lock around `load_plugin_skills`), and both copies answer - fixed in 2.2.4a1 (alpha); on testing Emkay backports the lock (`contrib/ovos-core-locked.py`) | fixed upstream |
| 6 | translations | With many `secondary_langs`, skills log "Unable to find …intent" for every language they lack (e.g. no Norwegian in ovos-skill-weather) - translations via [ovos-localize](https://github.com/OpenVoiceOS/ovos-localize) | to do |
