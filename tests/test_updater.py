"""Tests for the GitHub update check and the verified AppImage install.

Nothing here touches the network: every request goes through ``updater._open``,
which each test replaces with a canned answer.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import sys
import tomllib
import urllib.error
from pathlib import Path
from typing import Self

import pytest

import tuxflow
from tuxflow import updater

APPIMAGE_NAME = "TuxFlow-0.2.0-x86_64.AppImage"
APPIMAGE_URL = f"https://github.com/Robertg761/TuxFlow/releases/download/v0.2.0/{APPIMAGE_NAME}"
CHECKSUMS_URL = "https://github.com/Robertg761/TuxFlow/releases/download/v0.2.0/SHA256SUMS"


# --------------------------------------------------------------------------- #
# Stubs
# --------------------------------------------------------------------------- #


class FakeResponse(io.BytesIO):
    """Just enough of an ``http.client.HTTPResponse`` for the updater."""

    def __init__(self, body: bytes, *, content_length: bool = True) -> None:
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))} if content_length else {}

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exception: object) -> None:
        self.close()


def _http_error(status: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(updater.LATEST_RELEASE_URL, status, "no", {}, None)  # type: ignore[arg-type]


def _release(tag: str = "v0.2.0", *, assets: list[dict] | None = None) -> dict:
    if assets is None:
        assets = [
            {"name": APPIMAGE_NAME, "browser_download_url": APPIMAGE_URL},
            {"name": "SHA256SUMS", "browser_download_url": CHECKSUMS_URL},
        ]
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/Robertg761/TuxFlow/releases/tag/{tag}",
        "assets": assets,
    }


def _serve(monkeypatch, answers: dict[str, object]) -> list[str]:
    """Route ``_open`` through a table of canned answers, recording the calls."""
    requested: list[str] = []

    def fake_open(url: str, _timeout: float, _accept: str) -> object:
        requested.append(url)
        updater._require_https(url)
        answer = answers.get(url)
        if answer is None:
            raise _http_error(404)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, dict):
            return FakeResponse(json.dumps(answer).encode("utf-8"))
        if isinstance(answer, str):
            return FakeResponse(answer.encode("utf-8"))
        return FakeResponse(answer)

    monkeypatch.setattr(updater, "_open", fake_open)
    return requested


def _checksums(payload: bytes, name: str = APPIMAGE_NAME) -> str:
    return f"{hashlib.sha256(payload).hexdigest()}  {name}\n"


@pytest.fixture
def installed_appimage(tmp_path) -> Path:
    target = tmp_path / "apps" / APPIMAGE_NAME
    target.parent.mkdir()
    target.write_bytes(b"the version that is running")
    target.chmod(0o755)
    return target


# --------------------------------------------------------------------------- #
# Versions
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("candidate", "current", "newer"),
    [
        # The alpha suffix this project actually ships.
        ("0.1.0", "0.1.0a1", True),
        ("0.1.0a1", "0.1.0", False),
        ("0.1.0a2", "0.1.0a1", True),
        ("0.1.0a1", "0.1.0a2", False),
        ("0.1.1", "0.1.0", True),
        ("0.2.0a1", "0.1.0", True),
        # Equal, however it is spelled.
        ("0.1.0a1", "0.1.0a1", False),
        ("v0.1.0a1", "0.1.0a1", False),
        ("0.1", "0.1.0", False),
        ("0.1.0", "0.1", False),
        # Older.
        ("0.1.0", "0.1.1", False),
        ("0.9.0", "1.0.0", False),
        # Numbers, not text: 10 comes after 9.
        ("1.10.0", "1.9.0", True),
        ("1.9.0", "1.10.0", False),
        # Pre-release ordering: alpha < beta < rc < release.
        ("1.0b1", "1.0a9", True),
        ("1.0rc1", "1.0b9", True),
        ("1.0", "1.0rc1", True),
        ("1.0.post1", "1.0", True),
        # Unparseable on either side never counts as an update.
        ("nightly", "0.1.0", False),
        ("0.2.0", "not-a-version", False),
        ("", "0.1.0", False),
    ],
)
def test_version_comparison_places_alphas_before_their_release(candidate, current, newer):
    assert updater.is_newer(candidate, current) is newer


def test_a_tag_that_is_not_a_version_does_not_parse():
    assert updater.parse_version("latest") is None
    assert updater.parse_version("1.0.0-nightly") is None
    assert updater.parse_version("0.1.0a1") is not None


def test_the_module_version_and_the_packaged_version_agree():
    # current_version() prefers the installed distribution's metadata, which is
    # built from pyproject.toml, while everything else in TuxFlow reports
    # __version__. If those two ever drift, the update check compares the wrong
    # number and either nags forever or goes silent.
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    packaged = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    assert tuxflow.__version__ == packaged


def test_the_current_version_is_a_version():
    assert updater.parse_version(updater.current_version()) is not None


def test_a_tag_keeps_its_meaning_without_the_leading_v():
    assert updater.normalise_version(" v0.2.0 ") == "0.2.0"
    assert updater.normalise_version("0.2.0") == "0.2.0"


# --------------------------------------------------------------------------- #
# Checking
# --------------------------------------------------------------------------- #


def test_a_newer_release_is_reported_with_both_asset_urls(monkeypatch):
    _serve(monkeypatch, {updater.LATEST_RELEASE_URL: _release()})

    update = updater.check_for_update(current="0.1.0a1")

    assert update is not None
    assert update.version == "0.2.0"
    assert update.appimage_url == APPIMAGE_URL
    assert update.appimage_name == APPIMAGE_NAME
    assert update.checksums_url == CHECKSUMS_URL
    assert update.release_url.endswith("/tag/v0.2.0")
    assert update.has_appimage is True


def test_the_running_version_is_not_an_update(monkeypatch):
    _serve(monkeypatch, {updater.LATEST_RELEASE_URL: _release("v0.1.0a1")})

    assert updater.check_for_update(current="0.1.0a1") is None


def test_an_older_published_release_is_not_an_update(monkeypatch):
    _serve(monkeypatch, {updater.LATEST_RELEASE_URL: _release("v0.1.0")})

    assert updater.check_for_update(current="0.2.0") is None


def test_a_repository_with_only_draft_releases_answers_404_and_is_not_an_error(monkeypatch):
    # /releases/latest ignores drafts and prereleases, so a project whose only
    # release is still a draft looks exactly like a project with none at all.
    _serve(monkeypatch, {updater.LATEST_RELEASE_URL: _http_error(404)})

    assert updater.check_for_update(current="0.1.0a1") is None


def test_a_rate_limited_check_is_not_an_error(monkeypatch):
    _serve(monkeypatch, {updater.LATEST_RELEASE_URL: _http_error(403)})

    assert updater.check_for_update(current="0.1.0a1") is None


def test_a_strict_check_tells_the_user_why_it_failed(monkeypatch):
    # `tuxflow update` was asked for by hand, so it reports what went wrong
    # instead of shrugging the way the background check does.
    _serve(monkeypatch, {updater.LATEST_RELEASE_URL: _http_error(403)})
    with pytest.raises(updater.UpdateError) as caught:
        updater.latest_release(strict=True)
    assert "rate limit" in str(caught.value)

    _serve(monkeypatch, {updater.LATEST_RELEASE_URL: urllib.error.URLError("no route")})
    with pytest.raises(updater.UpdateError):
        updater.latest_release(strict=True)

    _serve(monkeypatch, {updater.LATEST_RELEASE_URL: _http_error(500)})
    with pytest.raises(updater.UpdateError) as caught:
        updater.latest_release(strict=True)
    assert "500" in str(caught.value)


def test_a_strict_check_still_treats_no_published_release_as_an_answer(monkeypatch):
    _serve(monkeypatch, {updater.LATEST_RELEASE_URL: _http_error(404)})

    assert updater.latest_release(strict=True) is None


def test_a_machine_with_no_network_is_not_an_error(monkeypatch):
    _serve(monkeypatch, {updater.LATEST_RELEASE_URL: urllib.error.URLError("no route to host")})

    assert updater.check_for_update(current="0.1.0a1") is None


def test_a_timeout_is_not_an_error(monkeypatch):
    _serve(monkeypatch, {updater.LATEST_RELEASE_URL: TimeoutError("timed out")})

    assert updater.check_for_update(current="0.1.0a1") is None


def test_a_body_that_is_not_a_release_document_is_ignored(monkeypatch):
    def fake_open(_url: str, _timeout: float, _accept: str) -> object:
        return FakeResponse(b"<html>we are down</html>")

    monkeypatch.setattr(updater, "_open", fake_open)

    assert updater.check_for_update(current="0.1.0a1") is None


def test_a_release_that_publishes_no_appimage_still_reports_the_page(monkeypatch):
    _serve(monkeypatch, {updater.LATEST_RELEASE_URL: _release(assets=[])})

    update = updater.check_for_update(current="0.1.0a1")

    assert update is not None
    assert update.has_appimage is False
    assert update.release_url


# --------------------------------------------------------------------------- #
# Am I an AppImage?
# --------------------------------------------------------------------------- #


def test_the_appimage_variable_has_to_point_at_a_real_file(monkeypatch, tmp_path):
    monkeypatch.delenv("APPIMAGE", raising=False)
    assert updater.running_appimage_path() is None

    monkeypatch.setenv("APPIMAGE", "")
    assert updater.running_appimage_path() is None

    monkeypatch.setenv("APPIMAGE", str(tmp_path / "gone.AppImage"))
    assert updater.running_appimage_path() is None

    bundle = tmp_path / "TuxFlow.AppImage"
    bundle.write_bytes(b"ELF")
    monkeypatch.setenv("APPIMAGE", str(bundle))
    assert updater.running_appimage_path() == bundle


# --------------------------------------------------------------------------- #
# Checksums
# --------------------------------------------------------------------------- #


def test_checksums_are_read_from_sha256sum_output():
    digest = "a" * 64
    parsed = updater.parse_checksums(
        f"{digest}  TuxFlow-0.2.0-x86_64.AppImage\n"
        f"{'b' * 64} *binary-mode.AppImage\n"
        f"{'c' * 64}  ./nested/path.AppImage\n"
        "not a checksum line\n"
        "\n"
        "zz  short.AppImage\n"
    )

    assert parsed["TuxFlow-0.2.0-x86_64.AppImage"] == digest
    assert parsed["binary-mode.AppImage"] == "b" * 64
    assert parsed["path.AppImage"] == "c" * 64
    assert "short.AppImage" not in parsed


# --------------------------------------------------------------------------- #
# Installing
# --------------------------------------------------------------------------- #


def _update(*, checksums: bool = True) -> updater.Update:
    return updater.Update(
        version="0.2.0",
        release_url="https://github.com/Robertg761/TuxFlow/releases/tag/v0.2.0",
        appimage_url=APPIMAGE_URL,
        appimage_name=APPIMAGE_NAME,
        checksums_url=CHECKSUMS_URL if checksums else "",
    )


def test_a_verified_download_replaces_the_appimage_and_stays_executable(
    monkeypatch, installed_appimage
):
    payload = b"the new AppImage" * 1000
    _serve(
        monkeypatch,
        {CHECKSUMS_URL: _checksums(payload), APPIMAGE_URL: payload},
    )
    seen: list[float] = []

    installed = updater.download_and_install(
        _update(), target=installed_appimage, progress=lambda f, _m: seen.append(f)
    )

    assert installed == installed_appimage
    assert installed_appimage.read_bytes() == payload
    assert installed_appimage.stat().st_mode & stat.S_IXUSR
    assert oct(installed_appimage.stat().st_mode & 0o777) == "0o755"
    # The temporary download is gone, whatever happened to it.
    assert list(installed_appimage.parent.iterdir()) == [installed_appimage]
    assert seen and seen[-1] == 1.0


def test_a_download_that_does_not_match_the_checksum_is_refused(monkeypatch, installed_appimage):
    original = installed_appimage.read_bytes()
    _serve(
        monkeypatch,
        {CHECKSUMS_URL: _checksums(b"what the release promised"), APPIMAGE_URL: b"something else"},
    )

    with pytest.raises(updater.UpdateError) as caught:
        updater.download_and_install(_update(), target=installed_appimage)

    assert "checksum" in str(caught.value).lower()
    assert installed_appimage.read_bytes() == original
    assert list(installed_appimage.parent.iterdir()) == [installed_appimage]


def test_a_release_without_a_checksums_file_is_refused(monkeypatch, installed_appimage):
    original = installed_appimage.read_bytes()
    requested = _serve(monkeypatch, {APPIMAGE_URL: b"unverifiable"})

    with pytest.raises(updater.UpdateError) as caught:
        updater.download_and_install(_update(checksums=False), target=installed_appimage)

    assert "SHA256SUMS" in str(caught.value)
    assert installed_appimage.read_bytes() == original
    # Refused before spending a few hundred megabytes finding out.
    assert requested == []


def test_a_checksums_file_missing_our_entry_is_refused(monkeypatch, installed_appimage):
    original = installed_appimage.read_bytes()
    _serve(
        monkeypatch,
        {
            CHECKSUMS_URL: _checksums(b"payload", name="SomeOtherApp.AppImage"),
            APPIMAGE_URL: b"payload",
        },
    )

    with pytest.raises(updater.UpdateError) as caught:
        updater.download_and_install(_update(), target=installed_appimage)

    assert APPIMAGE_NAME in str(caught.value)
    assert installed_appimage.read_bytes() == original


def test_a_checksums_file_that_cannot_be_fetched_is_refused(monkeypatch, installed_appimage):
    _serve(monkeypatch, {CHECKSUMS_URL: urllib.error.URLError("connection reset")})

    with pytest.raises(updater.UpdateError):
        updater.download_and_install(_update(), target=installed_appimage)

    assert installed_appimage.read_bytes() == b"the version that is running"


def test_a_download_that_breaks_off_leaves_the_original_alone(monkeypatch, installed_appimage):
    original = installed_appimage.read_bytes()
    _serve(
        monkeypatch,
        {
            CHECKSUMS_URL: _checksums(b"whole file"),
            APPIMAGE_URL: urllib.error.URLError("connection reset by peer"),
        },
    )

    with pytest.raises(updater.UpdateError) as caught:
        updater.download_and_install(_update(), target=installed_appimage)

    assert "Download failed" in str(caught.value)
    assert installed_appimage.read_bytes() == original
    assert list(installed_appimage.parent.iterdir()) == [installed_appimage]


def test_installing_without_an_appimage_explains_itself(monkeypatch):
    monkeypatch.delenv("APPIMAGE", raising=False)

    with pytest.raises(updater.UpdateError) as caught:
        updater.download_and_install(_update())

    assert "not started from an AppImage" in str(caught.value)


def test_the_target_defaults_to_the_running_appimage(monkeypatch, installed_appimage):
    payload = b"the new AppImage"
    monkeypatch.setenv("APPIMAGE", str(installed_appimage))
    _serve(monkeypatch, {CHECKSUMS_URL: _checksums(payload), APPIMAGE_URL: payload})

    assert updater.download_and_install(_update()) == installed_appimage
    assert installed_appimage.read_bytes() == payload


@pytest.mark.skipif(
    sys.platform != "linux" or os.geteuid() == 0,
    reason="root ignores directory permissions",
)
def test_a_read_only_directory_is_explained_rather_than_traced(monkeypatch, installed_appimage):
    _serve(monkeypatch, {CHECKSUMS_URL: _checksums(b"new"), APPIMAGE_URL: b"new"})
    directory = installed_appimage.parent
    directory.chmod(0o500)
    try:
        with pytest.raises(updater.UpdateError) as caught:
            updater.download_and_install(_update(), target=installed_appimage)
    finally:
        directory.chmod(0o700)

    message = str(caught.value)
    assert "not writable" in message
    assert str(directory) in message


def test_a_plain_http_url_is_never_fetched(installed_appimage):
    insecure = updater.Update(
        version="0.2.0",
        release_url="https://example.invalid",
        appimage_url="http://example.invalid/TuxFlow.AppImage",
        appimage_name=APPIMAGE_NAME,
        checksums_url="http://example.invalid/SHA256SUMS",
    )

    with pytest.raises(updater.UpdateError) as caught:
        updater.download_and_install(insecure, target=installed_appimage)

    assert "HTTPS" in str(caught.value)


# --------------------------------------------------------------------------- #
# Asking no more than once a day
# --------------------------------------------------------------------------- #

DAY = updater.CHECK_INTERVAL_SECONDS


def test_a_check_is_due_a_day_after_the_last_one():
    now = 1_000_000.0
    assert updater.due_for_check(now - DAY, now) is True
    assert updater.due_for_check(now - DAY - 1, now) is True
    assert updater.due_for_check(now - 60, now) is False
    assert updater.due_for_check(now - DAY + 60, now) is False


def test_a_first_run_or_a_clock_that_moved_backwards_checks_again():
    now = 1_000_000.0
    assert updater.due_for_check(0.0, now) is True
    assert updater.due_for_check(-5.0, now) is True
    assert updater.due_for_check(now + DAY, now) is True


def test_the_last_check_survives_a_round_trip(tmp_path):
    state = tmp_path / "cache" / "update-check.json"

    assert updater.read_last_check(state) == 0.0

    updater.write_last_check(1_234.5, state)
    assert updater.read_last_check(state) == 1_234.5
    assert updater.check_is_due(now=1_234.5 + 60, path=state) is False
    assert updater.check_is_due(now=1_234.5 + DAY, path=state) is True


def test_a_damaged_state_file_reads_as_never_checked(tmp_path):
    state = tmp_path / "update-check.json"
    state.write_text("{not json", encoding="utf-8")
    assert updater.read_last_check(state) == 0.0

    state.write_text('{"last_check": "yesterday"}', encoding="utf-8")
    assert updater.read_last_check(state) == 0.0

    state.write_text("[]", encoding="utf-8")
    assert updater.read_last_check(state) == 0.0


def test_a_state_file_that_cannot_be_written_is_not_an_error(tmp_path):
    # A read-only cache directory must not stop the app from starting.
    updater.write_last_check(1.0, tmp_path / "missing-parent" / "sub" / "state.json")
