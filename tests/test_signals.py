from pathlib import Path

from coldforge.companies import Company
from coldforge.signals import CATALOG, confirm, confirmed_signals, profile_company

FIXTURES = Path(__file__).parent / "fixtures"


def test_each_detector_has_positive_and_negative_fixture():
    positive = (FIXTURES / "signals_positive_fr.html").read_text(encoding="utf-8")
    negative = (FIXTURES / "signals_negative_en.html").read_text(encoding="utf-8")
    assert CATALOG["NO_ONLINE_BOOKING"]["detect"](positive, "https://x.fr")
    assert not CATALOG["NO_ONLINE_BOOKING"]["detect"](negative, "https://x.fr")
    assert CATALOG["NO_SCHEMA_MARKUP"]["detect"](positive, "https://x.fr")
    assert not CATALOG["NO_SCHEMA_MARKUP"]["detect"](negative, "https://x.fr")
    assert CATALOG["BLOG_STALE"]["detect"](positive, "https://x.fr")
    assert not CATALOG["BLOG_STALE"]["detect"](negative, "https://x.fr")
    assert CATALOG["TECH_STACK"]["detect"](positive, "https://x.fr")
    assert not CATALOG["TECH_STACK"]["detect"](negative, "https://x.fr")
    assert CATALOG["RUNNING_GOOGLE_ADS"]["detect"](positive, "https://x.fr")
    assert not CATALOG["RUNNING_GOOGLE_ADS"]["detect"](negative, "https://x.fr")
    assert CATALOG["HIRING_ROLE"]["detect"](positive, "https://x.fr")
    assert not CATALOG["HIRING_ROLE"]["detect"](negative, "https://x.fr")
    assert CATALOG["CONTACT_FORM_URL"]["detect"](positive, "https://x.fr")
    assert not CATALOG["CONTACT_FORM_URL"]["detect"](negative, "https://x.fr")
    robots = "User-agent: GPTBot\nDisallow: /\nUser-agent: ClaudeBot\nDisallow: /private"
    assert CATALOG["BLOCKS_AI_CRAWLERS"]["detect"](robots, "https://x.fr/robots.txt")
    assert not CATALOG["BLOCKS_AI_CRAWLERS"]["detect"]("User-agent: *\nDisallow:", "x")
    assert CATALOG["NO_WEBSITE"]["detect"](None, "")
    assert not CATALOG["NO_WEBSITE"]["detect"]("<html>", "https://x.fr")


def test_confirmation_and_three_request_budget(tmp_path):
    confirm(["free"], tmp_path)
    assert confirmed_signals(tmp_path) == set(CATALOG)
    calls = []

    def fetch(url):
        calls.append(url)
        if url.endswith("robots.txt"):
            return "User-agent: *\nDisallow:"
        return '<a href="/contact">Contact</a>'

    found = profile_company(Company("X", website="https://x.fr"), fetch=fetch, confirmed=set(CATALOG))
    assert len(calls) <= 3
    assert found
