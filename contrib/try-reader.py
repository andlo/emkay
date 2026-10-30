"""Try Emkay's article reader once, without Unseenet: robots.txt, fetch over Tor, his answer.

    python try-reader.py SOCKS_PORT URL [question]
"""
import asyncio
import sys
import time

from emkay.llm import Llm
from emkay.reader import NotAllowed, Reader


async def main() -> None:
    socks, url = int(sys.argv[1]), sys.argv[2]
    question = sys.argv[3] if len(sys.argv) > 3 else "Hvad handler artiklen om?"
    r = Reader(Llm(), socks=socks)
    t = time.time()
    print("må læses:", await r.allowed(url), f"({time.time() - t:.0f} s)")
    try:
        text = await r.text(url)
    except NotAllowed:
        print("robots.txt siger nej")
        return
    print(f"artikeltekst: {len(text)} tegn ({time.time() - t:.0f} s)")
    print("---")
    print(await r.answer(url, "(titel)", "testmedie", question, "da"))
    print(f"--- ({time.time() - t:.0f} s i alt)")


asyncio.run(main())
