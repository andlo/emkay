from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from emkay import news

NOW = datetime(2026, 10, 30, 20, 0, tzinfo=timezone.utc)


@dataclass
class Msg:
    message_id: str
    subject: str = ""
    body: str = ""
    from_name: str = ""
    references: list = field(default_factory=list)
    readable: bool = True
    card: object = None


def post(n, title, site="DR", url=None):
    url = url or f"https://example.dk/{n}"
    return Msg(f"<{n}@x>", title, f"**[{title}]({url})**\n\n*{site}*\n", f"newsbot · {site}")


def store(*heads):
    s = news.NewsStore()
    for i, (title, site) in enumerate(heads):
        s.ingest([post(i, title, site)], f"dk.newsbot.{site.lower()}@o", NOW)
    return s


def test_a_newsbot_post_is_a_headline_and_comments_are_not():
    h = news.headline_of(post(1, "Præsident i Polen suspenderet"), "dk.newsbot.dr@o", NOW)
    assert (h.title, h.site, h.url) == ("Præsident i Polen suspenderet", "DR", "https://example.dk/1")
    reply = Msg("<c@x>", "Re: x", "hej", references=["<1@x>"])
    assert news.headline_of(reply, "g", NOW) is None


def test_the_same_headline_twice_is_kept_once_and_old_ones_go():
    s = news.NewsStore()
    assert s.ingest([post(1, "A"), post(2, "A")], "g", NOW) == 1
    s.ingest([post(3, "B")], "g", NOW - timedelta(days=8))
    s.prune(NOW)
    assert [h.title for h in s.headlines] == ["A"]


def test_questions_about_the_news_are_understood():
    assert news.ask_of("Emkay, hvad sker der i verden i dag?").what == "today"
    assert news.ask_of("dagens nyheder tak").what == "today"
    assert news.ask_of("what's happening in the world today?").what == "today"
    a = news.ask_of("fortæl mere om Polen")
    assert (a.what, a.topic) == ("more", "Polen")
    a = news.ask_of("Hvad skriver de andre om benzinpriserne?")
    assert (a.what, a.topic) == ("compare", "benzinpriserne")
    assert news.ask_of("hvad er klokken?") is None
    assert news.ask_of("fortæl en vittighed") is None


def test_find_and_compare_across_media():
    s = store(("Præsident for forfatningsdomstol i Polen suspenderet", "DR"),
              ("Polen: dommer sat fra bestillingen", "Arbejderen"),
              ("Benzinpriser stiger igen", "B.T."),
              ("Olieindustrien scorer på høje benzinpriser", "Arbejderen"))
    assert s.find("Polen")[0].title.startswith(("Præsident", "Polen"))
    assert {h.site for h in s.compare("polen")} == {"DR", "Arbejderen"}
    assert len(s.compare("benzinpriser")) == 2
    assert s.find("vittighed") == []


def test_today_lists_dr_first_and_says_he_only_knows_headlines():
    s = store(("A", "Arbejderen"), ("B", "DR"), ("C", "DR"))
    text = news.today(s, "da", now=NOW)
    assert text.index("**DR**") < text.index("**Arbejderen**")
    assert "!dk.newsbot.dr" in text and "overskrifterne" in text
    assert news.today(news.NewsStore(), "da", now=NOW).startswith("Jeg har ikke set")


def test_the_waiting_line_has_coffee():
    assert all("☕" in w for w in news.WAIT["da"])
