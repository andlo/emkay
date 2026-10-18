"""Emkay's brain is OpenVoiceOS: this is the bridge to its messagebus.

A message from the café goes to ovos-core as an utterance, exactly like a
spoken one would from a microphone; skills answer what they can, and the
Emkay persona (ovos-persona with a local LLM) answers the rest. Everything
OVOS says back for that utterance (`speak`) becomes Emkay's reply.

Each thread or chat gets its own OVOS session, so OVOS (and the persona's
short-term memory) keeps conversations apart, and each session carries the
language the message was written in.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger("emkay.ovos")

# languages Emkay's OVOS is configured for (secondary_langs in mycroft.conf)
LANGS = {"en": "en-US", "da": "da-DK", "de": "de-DE", "sv": "sv-SE", "nb": "nb-NO",
         "nl": "nl-NL", "fr": "fr-FR", "es": "es-ES", "it": "it-IT", "pt": "pt-PT"}
DONE = ("ovos.utterance.handled", "complete_intent_failure")


_detector = None


def detect_lang(text: str, default: str = "en") -> str:
    """The language a message is written in, offline (Lingua), limited to LANGS."""
    global _detector
    try:
        from lingua import IsoCode639_1, LanguageDetectorBuilder
        if _detector is None:
            codes = [getattr(IsoCode639_1, ("NB" if c == "nb" else c).upper()) for c in LANGS]
            _detector = LanguageDetectorBuilder.from_iso_codes_639_1(*codes).build()
        conf = _detector.compute_language_confidence_values(text)
    except Exception as exc:                          # noqa: BLE001
        log.warning("language detection: %s", exc)
        return default
    if not conf or len(text.split()) < 2 and conf[0].value < 0.5:
        return default                               # "hej" alone could be anything
    scores = {c.language.iso_code_639_1.name.lower(): c.value for c in conf}
    best = max(scores, key=scores.get)
    # Danish and Norwegian are close cousins; Emkay lives in Denmark, so a near tie is Danish
    if best == "nb" and scores.get("da", 0) >= scores["nb"] * 0.6:
        best = "da"
    return best if best in LANGS else default


@dataclass
class Ovos:
    host: str = "127.0.0.1"
    port: int = 8181
    timeout: float = 240.0                 # the LLM on a CPU can be slow
    _bus: object = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _client(self):
        if self._bus is None:
            from ovos_bus_client import MessageBusClient
            bus = MessageBusClient(host=self.host, port=self.port)
            bus.run_in_thread()
            if not bus.connected_event.wait(30):
                raise ConnectionError(f"no OVOS messagebus on {self.host}:{self.port}")
            self._bus = bus
        return self._bus

    def _ask(self, text: str, lang: str, session_id: str) -> list[str]:
        from ovos_bus_client.message import Message
        with self._lock:                    # one utterance at a time: one small CPU
            bus = self._client()
            said: list[str] = []
            done = threading.Event()

            def mine(m) -> bool:
                return (m.context.get("session") or {}).get("session_id") == session_id

            def on_speak(m):
                if mine(m) and m.data.get("utterance"):
                    said.append(m.data["utterance"].strip())

            def on_done(m):
                if mine(m):
                    done.set()

            bus.on("speak", on_speak)
            for ev in DONE:
                bus.on(ev, on_done)
            try:
                tag = LANGS.get(lang, "en-US")
                bus.emit(Message("recognizer_loop:utterance",
                                 {"utterances": [text], "lang": tag},
                                 {"source": "emkay", "destination": ["skills"],
                                  "session": {"session_id": session_id, "lang": tag}}))
                if not done.wait(self.timeout):
                    log.warning("OVOS did not finish within %ss", self.timeout)
                time.sleep(0.5)             # a skill may speak right after it reports done
            finally:
                bus.remove("speak", on_speak)
                for ev in DONE:
                    bus.remove(ev, on_done)
            return [s for s in said if s]

    async def ask(self, text: str, lang: str, session_id: str) -> list[str]:
        return await asyncio.get_running_loop().run_in_executor(
            None, self._ask, text, lang, session_id)
