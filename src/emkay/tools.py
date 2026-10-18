"""Emkay's tools: small, exact things a small model gets wrong or cannot know.

The planner picks at most one tool per message and gives it an input; the
result becomes a note the answer must use. Every tool fails softly (an empty
list) so a dead API never stops a reply. Only open APIs without keys.
"""

from __future__ import annotations

import ast
import json
import logging
import math
import operator
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from .world import Fact

log = logging.getLogger("emkay.tools")
UA = "emkay-bot/0.1 (https://github.com/andlo/emkay)"
HOME_TZ = "Europe/Copenhagen"


def _get(url: str, accept: str = "application/json", timeout: float = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _json(url: str):
    return json.loads(_get(url))


# --- calc -------------------------------------------------------------------

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}
_NAMES = {k: getattr(math, k) for k in (
    "sqrt", "sin", "cos", "tan", "asin", "acos", "atan", "log", "log10", "log2", "exp",
    "floor", "ceil", "factorial", "radians", "degrees", "hypot", "pi", "e", "tau")}
_NAMES.update(abs=abs, round=round, min=min, max=max)


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        a, b = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(b) > 1000:
            raise ValueError("exponent too large")
        return _OPS[type(node.op)](a, b)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Name) and node.id in _NAMES and not callable(_NAMES[node.id]):
        return _NAMES[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and \
            callable(_NAMES.get(node.func.id)) and not node.keywords:
        args = [_eval(a) for a in node.args]
        if node.func.id == "factorial" and args and args[0] > 1000:
            raise ValueError("too large")
        return _NAMES[node.func.id](*args)
    raise ValueError("not allowed")


def calc(expr: str, lang: str) -> list[Fact]:
    e = expr.replace("^", "**").replace("×", "*").replace("÷", "/").replace("π", "pi")
    e = re.sub(r"(\d),(\d)", r"\1.\2", e)          # 2,5 -> 2.5
    try:
        v = _eval(ast.parse(e, mode="eval"))
    except Exception:                                # noqa: BLE001
        return []
    if isinstance(v, float):
        v = round(v, 10)
        v = int(v) if v.is_integer() and abs(v) < 1e15 else v
    return [Fact("Calculator", f"{expr} = {v}", "")]


# --- convert ----------------------------------------------------------------

_ureg = None


def convert(text: str, lang: str) -> list[Fact]:
    global _ureg
    m = re.match(r"\s*([-\d.,]+)\s*(.+?)\s+(?:to|in|i|til)\s+(.+?)\s*$", text, re.IGNORECASE)
    if not m:
        return []
    try:
        import pint
        _ureg = _ureg or pint.UnitRegistry()
        q = _ureg.Quantity(float(m.group(1).replace(",", ".")), m.group(2).strip())
        r = q.to(m.group(3).strip())
    except Exception:                                # noqa: BLE001
        return []
    return [Fact("Unit conversion", f"{q:~P} = {r.magnitude:.6g} {r.units:~P}", "")]


# --- places, time, weather ---------------------------------------------------

def _geocode(place: str, lang: str) -> dict | None:
    q = urllib.parse.quote(place)
    try:
        d = _json(f"https://geocoding-api.open-meteo.com/v1/search?name={q}&count=1&language={lang}")
    except Exception:                                # noqa: BLE001
        return None
    return (d.get("results") or [None])[0]


def now(place: str, lang: str) -> list[Fact]:
    tz, where = HOME_TZ, "Denmark (Emkay's home)"
    if place.strip():
        g = _geocode(place, lang)
        if g and g.get("timezone"):
            tz, where = g["timezone"], f"{g['name']}, {g.get('country', '')}"
    t = datetime.now(ZoneInfo(tz))
    return [Fact("Clock", f"In {where} it is {t:%A %d %B %Y, %H:%M} ({tz}). "
                          f"Week {t.isocalendar().week}.", "")]


def holidays(text: str, lang: str) -> list[Fact]:
    m = re.search(r"\b([A-Za-z]{2})\b", text)
    cc = (m.group(1) if m else "DK").upper()
    y = re.search(r"\b(20\d\d)\b", text)
    year = int(y.group(1)) if y else datetime.now(timezone.utc).year
    try:
        days = _json(f"https://date.nager.at/api/v3/PublicHolidays/{year}/{cc}")
    except Exception:                                # noqa: BLE001
        return []
    today = datetime.now(timezone.utc).date().isoformat()
    lines = [f"{d['date']}: {d['localName']} ({d['name']})" for d in days]
    nxt = next((d for d in days if d["date"] >= today), None)
    head = f"Next: {nxt['date']} {nxt['localName']}. " if nxt else ""
    return [Fact(f"Public holidays {cc} {year}", head + "All: " + "; ".join(lines),
                 f"https://date.nager.at/PublicHoliday/Country/{cc}/{year}")]


_WMO = {0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "fog",
        51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain",
        65: "heavy rain", 71: "light snow", 73: "snow", 75: "heavy snow", 80: "showers",
        81: "showers", 82: "heavy showers", 95: "thunderstorm", 96: "thunderstorm with hail"}


def weather(place: str, lang: str) -> list[Fact]:
    g = _geocode(place or "Copenhagen", lang)
    if not g:
        return []
    url = ("https://api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}"
           "&current=temperature_2m,wind_speed_10m,weather_code"
           "&daily=temperature_2m_max,temperature_2m_min,weather_code,precipitation_sum"
           "&timezone=auto&forecast_days=3&wind_speed_unit=ms").format(**g)
    try:
        d = _json(url)
    except Exception:                                # noqa: BLE001
        return []
    c, dd = d["current"], d["daily"]
    days = "; ".join(f"{dd['time'][i]}: {dd['temperature_2m_min'][i]:.0f}-{dd['temperature_2m_max'][i]:.0f} °C, "
                     f"{_WMO.get(dd['weather_code'][i], 'mixed')}, {dd['precipitation_sum'][i]} mm"
                     for i in range(len(dd["time"])))
    text = (f"{g['name']}, {g.get('country', '')} now: {c['temperature_2m']:.0f} °C, "
            f"{_WMO.get(c['weather_code'], 'mixed')}, wind {c['wind_speed_10m']:.0f} m/s. Forecast: {days}.")
    return [Fact(f"Open-Meteo: {g['name']}", text, f"https://open-meteo.com/en/docs#latitude={g['latitude']}&longitude={g['longitude']}")]


def place(q: str, lang: str) -> list[Fact]:
    try:
        d = _json("https://nominatim.openstreetmap.org/search?format=json&limit=1&addressdetails=0"
                  f"&accept-language={lang}&q=" + urllib.parse.quote(q))
    except Exception:                                # noqa: BLE001
        return []
    if not d:
        return []
    p = d[0]
    return [Fact(f"OpenStreetMap: {q}",
                 f"{p['display_name']} ({p.get('type', '')}), at {float(p['lat']):.4f}, {float(p['lon']):.4f}",
                 f"https://www.openstreetmap.org/{p['osm_type']}/{p['osm_id']}")]


def distance(text: str, lang: str) -> list[Fact]:
    parts = re.split(r"\s+(?:to|and|til|og)\s+|\s*-\s*|\s*,\s*", text.strip(), maxsplit=1)
    if len(parts) != 2:
        return []
    a, b = _geocode(parts[0], lang), _geocode(parts[1], lang)
    if not a or not b:
        return []
    la1, lo1, la2, lo2 = map(math.radians, (a["latitude"], a["longitude"], b["latitude"], b["longitude"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    km = 2 * 6371 * math.asin(math.sqrt(h))
    return [Fact("Distance", f"{a['name']} ({a.get('country', '')}) to {b['name']} ({b.get('country', '')}): "
                             f"about {km:.0f} km in a straight line", "")]


# --- knowledge and fun --------------------------------------------------------

def on_this_day(text: str, lang: str) -> list[Fact]:
    m = re.search(r"(\d{1,2})[-/.](\d{1,2})", text)
    t = datetime.now(ZoneInfo(HOME_TZ))
    day, month = (int(m.group(1)), int(m.group(2))) if m else (t.day, t.month)
    for code in dict.fromkeys([lang, "en"]):
        try:
            d = _json(f"https://api.wikimedia.org/feed/v1/wikipedia/{code}/onthisday/selected/{month:02d}/{day:02d}")
        except Exception:                            # noqa: BLE001
            continue
        ev = d.get("selected") or []
        if ev:
            lines = [f"{e['year']}: {e['text']}" for e in ev[:6]]
            return [Fact(f"Wikipedia ({code}): on {day}/{month}", "\n".join(lines),
                         f"https://{code}.wikipedia.org/wiki/Special:Search?search={month}/{day}")]
    return []


def joke(text: str, lang: str) -> list[Fact]:
    try:
        if text.strip():
            d = _json("https://icanhazdadjoke.com/search?limit=5&term=" + urllib.parse.quote(text.strip()))
            jokes = [r["joke"] for r in d.get("results", [])]
            if jokes:
                import random
                return [Fact("icanhazdadjoke", random.choice(jokes), "https://icanhazdadjoke.com")]
        d = _json("https://icanhazdadjoke.com/")
        return [Fact("icanhazdadjoke", d["joke"], f"https://icanhazdadjoke.com/j/{d['id']}")]
    except Exception:                                # noqa: BLE001
        return []


def define(word: str, lang: str) -> list[Fact]:
    w = word.strip().split()[0] if word.strip() else ""
    if not w:
        return []
    try:
        d = _json("https://en.wiktionary.org/api/rest_v1/page/definition/" + urllib.parse.quote(w))
    except Exception:                                # noqa: BLE001
        return []
    out = []
    for code in dict.fromkeys([lang, "en"]):
        for entry in d.get(code, [])[:2]:
            for df in entry.get("definitions", [])[:3]:
                txt = re.sub(r"<[^>]+>", "", df.get("definition", "")).strip()
                if txt:
                    out.append(f"({entry.get('language', code)}, {entry.get('partOfSpeech', '')}) {txt}")
    if not out:
        return []
    return [Fact(f"Wiktionary: {w}", "\n".join(out[:6]), f"https://en.wiktionary.org/wiki/{urllib.parse.quote(w)}")]


def arxiv(q: str, lang: str) -> list[Fact]:
    try:
        raw = _get("http://export.arxiv.org/api/query?sortBy=relevance&max_results=3&search_query=all:"
                   + urllib.parse.quote(q), accept="application/atom+xml")
    except Exception:                                # noqa: BLE001
        return []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in ET.fromstring(raw).findall("a:entry", ns):
        title = " ".join(e.findtext("a:title", "", ns).split())
        summ = " ".join(e.findtext("a:summary", "", ns).split())[:500]
        out.append(Fact(f"arXiv: {title}", f"{title} ({e.findtext('a:published', '', ns)[:4]}). {summ}",
                        e.findtext("a:id", "", ns)))
    return out


TOOLS: dict[str, tuple[str, Callable[[str, str], list[Fact]]]] = {
    "calc": ("exact arithmetic and geometry (area, volume, trig); input: a Python-style "
             "math expression, e.g. pi*3**2 or sqrt(2)*10", calc),
    "convert": ("unit conversion; input like '3 meter to foot' or '100 fahrenheit to celsius'", convert),
    "time": ("the current date, weekday, time or week number; input: a place, or empty for Denmark", now),
    "holidays": ("public holidays; input: ISO country code and optional year, e.g. 'DK 2026'", holidays),
    "weather": ("current weather and 3-day forecast; input: a place name", weather),
    "place": ("where a place is; input: a place name", place),
    "distance": ("straight-line distance between two places; input: 'Copenhagen to Aarhus'", distance),
    "onthisday": ("historical events on a date; input: 'day-month' or empty for today", on_this_day),
    "joke": ("a dad joke (English); input: a topic or empty", joke),
    "define": ("the meaning of a word (Wiktionary); input: the word", define),
    "arxiv": ("scientific papers; input: search words in English", arxiv),
}


def describe() -> str:
    return "\n".join(f"- {k}: {v[0]}" for k, v in TOOLS.items())


def run(name: str, arg: str, lang: str) -> list[Fact]:
    t = TOOLS.get(name)
    if not t:
        return []
    try:
        return t[1](arg or "", lang)
    except Exception as exc:                         # noqa: BLE001
        log.warning("tool %s: %s", name, exc)
        return []
