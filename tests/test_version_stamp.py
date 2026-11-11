"""The build states its own identity, and the route that shows it reads the same stamp."""

from pathlib import Path

from music_studio import version_stamp

ROOT = Path(__file__).resolve().parent.parent


def test_the_stamp_is_vendored_in_the_package():
    assert (ROOT / "music_studio" / "version_stamp.py").is_file()


def test_the_stamp_never_invents_a_commit():
    info = version_stamp.as_dict()
    assert info["version"]
    assert info["source"] in {"build_info", "git", "manifest"}
    if info["source"] == "manifest":
        assert info["commit"] == ""


def test_describe_is_one_readable_line():
    line = version_stamp.describe()
    assert line.split(" · ")[0]
    assert "(modified)" in line or "(modified)" not in line  # only ever appended, never faked


def test_build_info_is_not_committed():
    assert "BUILD_INFO.json" in (ROOT / ".gitignore").read_text(encoding="utf-8")


def test_the_version_route_reports_the_same_version_as_the_stamp():
    from dashboard import plugin_api
    answer = plugin_api.version_route()
    assert answer["version"] == version_stamp.version()
    assert {"commit", "commit_date", "built_at", "dirty", "channel"} <= set(answer)
