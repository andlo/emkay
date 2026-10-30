"""Emkay's own model, asked directly - for reading articles and making the quiz.

Café messages go through OVOS (skills first, then the persona). An article's text must
not: it is long, and a word in it could set off a skill. So for those two jobs Emkay asks
the same local model (Ollama's OpenAI-compatible API) with the persona's own system
prompt, so it is still him talking.
"""

from __future__ import annotations

import asyncio
import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path

PERSONA = Path("~/.config/ovos_persona/emkay.json")
FALLBACK_PROMPT = ("You are Emkay, a friendly, fact-based robot in a small robot café on "
                   "Unseenet. Be kind and calm. Never invent facts, links, numbers or quotes.")


@dataclass
class Llm:
    url: str = "http://127.0.0.1:11434/v1"
    model: str = "emkay-qwen"
    timeout: float = 300.0
    persona: Path = PERSONA

    def system_prompt(self) -> str:
        """The Emkay persona's system prompt, so the voice is the same as in the café."""
        try:
            d = json.loads(self.persona.expanduser().read_text())
            return d.get("system_prompt") or next(
                (v["system_prompt"] for v in d.values()
                 if isinstance(v, dict) and v.get("system_prompt")), FALLBACK_PROMPT)
        except (OSError, ValueError, StopIteration):
            return FALLBACK_PROMPT

    def _chat(self, prompt: str, temperature: float, json_out: bool) -> str:
        body = {"model": self.model, "temperature": temperature,
                "messages": [{"role": "system", "content": self.system_prompt()},
                             {"role": "user", "content": prompt}]}
        if json_out:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(self.url.rstrip("/") + "/chat/completions",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer ollama"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            d = json.loads(r.read())
        return d["choices"][0]["message"]["content"].strip()

    async def chat(self, prompt: str, temperature: float = 0.3, json_out: bool = False) -> str:
        return await asyncio.get_running_loop().run_in_executor(
            None, self._chat, prompt, temperature, json_out)
