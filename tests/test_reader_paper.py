import json
from datetime import datetime, timezone

from emkay import paper, reader
from emkay.news import Headline

TV2 = """User-agent: *
Allow: /

User-agent: ClaudeBot
User-agent: Claude-User
User-agent: ChatGPT-User
Disallow: /
"""


def test_robots_that_shut_out_ai_agents_mean_no_reading():
    assert reader.robots_allow("User-agent: *\nAllow: /\n", "https://www.dr.dk/nyheder/x")
    assert not reader.robots_allow(TV2, "https://nyheder.tv2.dk/x")
    assert not reader.robots_allow("User-agent: *\nDisallow: /\n", "https://x.dk/a")


DR = """User-agent: GPTBot
Disallow: /
User-agent: Claude-User
Disallow: /
User-agent: Perplexity-User
Disallow: /

User-agent: *
Disallow: /beta/
"""


def test_dr_shuts_out_ai_agents_too():
    assert not reader.robots_allow(DR, "https://www.dr.dk/nyheder/indland/x")


async def test_a_refused_robots_txt_means_no_reading_but_a_missing_one_is_fine():
    from unseenet.client.webfetch import FetchError

    class Refused(reader.Reader):
        def __init__(self, err):
            super().__init__(llm=None)
            self.err = err

        async def _get(self, url, limit):
            raise FetchError(self.err)

    assert not await Refused("the page answered 403").allowed("https://www.dr.dk/x")
    assert not await Refused("Tor could not connect").allowed("https://www.dr.dk/x")
    assert await Refused("the page answered 404").allowed("https://lille.dk/x")


def test_extract_keeps_the_text():
    html = "<html><body><nav>menu</nav><article>" + "<p>Dette er en lang sætning om noget. </p>" * 20 \
           + "</article></body></html>"
    assert "lang sætning" in reader.extract(html, "https://x.dk/a")


HEADS = [Headline("Kulturminister kalder ishockey-udvikling fortvivlende", "u1", "DR", "g", "1", "x"),
         Headline("Præsident i Polen suspenderet", "u2", "DR", "g", "2", "x")]


def test_quiz_questions_must_have_their_answer_in_the_headline():
    raw = json.dumps({"questions": [
        {"headline": 1, "question": "Hvilken sport kalder kulturministeren fortvivlende?",
         "answer": "ishockey"},
        {"headline": 2, "question": "Hvilket land?", "answer": "Ungarn"},             # made up
        {"headline": 2, "question": "Hvad blev præsidenten i Polen?", "answer": "Polen"},  # in q
        {"headline": 9, "question": "?", "answer": "x"}]})
    got = paper.check_quiz(raw, HEADS)
    assert [(q, a) for q, a, _ in got] == [("Hvilken sport kalder kulturministeren fortvivlende?",
                                           "ishockey")]
    assert paper.check_quiz("no json here", HEADS) == []


def test_the_paper_and_quiz_times():
    sch = paper.Schedule()
    fri = datetime(2026, 10, 30, 14, 5, tzinfo=timezone.utc)        # 15:05 in Copenhagen
    assert sch.quiz_due(fri, None) and not sch.quiz_due(fri, "2026-10-30")
    assert not sch.quiz_due(datetime(2026, 10, 29, 14, 5, tzinfo=timezone.utc), None)
    morning = datetime(2026, 10, 31, 6, 40, tzinfo=timezone.utc)    # 07:40
    assert sch.morning_due(morning, "2026-10-30") and not sch.morning_due(morning, "2026-10-31")
    assert not sch.morning_due(datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc), None)
    assert paper.today_da(morning, sch.tz) == "lørdag den 31. oktober"
