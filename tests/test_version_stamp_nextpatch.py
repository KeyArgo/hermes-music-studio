"""Dev builds must sort after the release tag they follow.

Once ``v1.0.0`` is tagged, a later commit still reads ``1.0.0-dev.N``, which SemVer sorts
*older* than ``1.0.0``. These tests pin the fix: the patch is bumped before the dev suffix.

Every git call is stubbed — nothing here touches the real repository.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from music_studio import version_stamp as vs

SHA = "0123456789abcdef"


def _run(monkeypatch, *, declared="1.0.0", tag="", exact="", shallow=False,
         count=7, dev_base=0):
    monkeypatch.setattr(vs, "_find_build_info", lambda: {})
    monkeypatch.setattr(vs, "_declared_version", lambda: declared)
    monkeypatch.setattr(vs, "_catalog_pin", lambda: "")
    monkeypatch.setattr(vs, "_dev_base", lambda: dev_base)

    def fake_git(*args):
        if args == ("rev-parse", "HEAD"):
            return SHA
        if args == ("describe", "--tags", "--exact-match", "HEAD"):
            return exact
        if args[:2] == ("tag", "--list"):
            return tag
        if args == ("status", "--porcelain"):
            return ""
        if args == ("rev-parse", "--is-shallow-repository"):
            return "true" if shallow else "false"
        if args == ("rev-list", "--count", "HEAD"):
            return str(count)
        if args == ("log", "-1", "--format=%cI"):
            return "2026-11-14T00:00:00+00:00"
        return ""

    monkeypatch.setattr(vs, "_git", fake_git)
    return vs.as_dict()


def test_no_tag_keeps_declared_version(monkeypatch):
    info = _run(monkeypatch)
    assert info["version"] == "1.0.0-dev.7"
    assert info["release"] == "1.0.0"
    assert info["channel"] == "dev"


def test_tagged_release_bumps_patch_for_later_commits(monkeypatch):
    info = _run(monkeypatch, tag="v1.0.0")
    assert info["version"] == "1.0.1-dev.7"
    assert info["release"] == "1.0.0"


def test_head_exactly_tagged_is_the_release(monkeypatch):
    info = _run(monkeypatch, tag="v1.0.0", exact="v1.0.0")
    assert info["version"] == "1.0.0"
    assert info["channel"] == "release"


def test_bump_follows_the_declared_version(monkeypatch):
    info = _run(monkeypatch, declared="1.2.3", tag="v1.2.3")
    assert info["version"] == "1.2.4-dev.7"


def test_shallow_clone_with_tag_gets_bumped_plain_dev(monkeypatch):
    info = _run(monkeypatch, tag="v1.0.0", shallow=True)
    assert info["version"] == "1.0.1-dev"
