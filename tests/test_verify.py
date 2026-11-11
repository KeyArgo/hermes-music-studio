"""The proof page must measure, not print a claim."""

import re

from music_studio import verify


def test_every_row_is_measured_and_named():
    info = verify.summary()
    assert info["rows"], "the page must show at least one measurement"
    for row in info["rows"]:
        assert set(row) == {"name", "ok", "detail"}
        assert row["name"].strip()
        assert isinstance(row["ok"], bool)
        assert row["detail"].strip()


def test_the_summary_agrees_with_the_rows():
    info = verify.summary()
    assert info["ok"] == all(row["ok"] for row in info["rows"])


def test_the_page_carries_the_build_identity_and_the_verdict():
    info = verify.summary()
    page = verify.render_html(info)
    assert info["stamp"]["version"] in page
    assert "Measured at" in page
    assert "whether sound leaves the speakers" in page, "the page says what it cannot check"


def test_a_failing_check_makes_the_page_say_so(monkeypatch):
    monkeypatch.setattr(verify, "checks", lambda: [
        {"name": "a thing this build promises", "ok": False, "detail": "it did not work"},
    ])
    info = verify.summary()
    assert info["ok"] is False
    page = verify.render_html(info)
    assert "does not work on this machine" in page
    assert re.search(r'class="bad"', page), "the failing row is visibly marked"
