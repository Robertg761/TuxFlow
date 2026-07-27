"""Finding out whether a newer TuxFlow exists, and installing it when we can.

The whole mechanism is one anonymous HTTPS GET to the GitHub releases API. No
identifier, no version-of-your-desktop, no telemetry: the request carries a
user agent and nothing else, and it is only made when the user has left the
update check switched on.

Installing is deliberately narrow. Only a running AppImage can replace itself,
and it only does so after the downloaded file matches the digest published in
the release's ``SHA256SUMS``. No checksum, no install — there is no fallback
path that skips verification. Source installs are told a release exists and
left to their own package manager or to ``scripts/install.sh``.

Only the standard library is used, so the AppImage needs no extra wheels to
keep itself up to date.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tuxflow import __version__
from tuxflow.paths import update_state_file

REPOSITORY = "Robertg761/TuxFlow"
# /releases/latest returns the newest *published, non-prerelease* release and
# skips drafts entirely. That is exactly what we want: a draft release is one a
# maintainer is still writing up, and a prerelease is opt-in. Neither should
# ever be pushed at users through this check.
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{REPOSITORY}/releases/latest"
CHECKSUM_ASSET = "SHA256SUMS"

CHECK_TIMEOUT = 8.0
DOWNLOAD_TIMEOUT = 30.0
CHECK_INTERVAL_SECONDS = 24 * 60 * 60
_CHUNK = 256 * 1024

USER_AGENT = f"TuxFlow/{__version__} (+https://github.com/{REPOSITORY})"

# (fraction downloaded from 0.0 to 1.0, human-readable status)
ProgressCallback = Callable[[float, str], None]


class UpdateError(RuntimeError):
    """An install that was attempted and failed. Always carries a readable message."""


@dataclass(frozen=True, slots=True)
class Update:
    """A published release that is newer than the copy running right now."""

    version: str
    release_url: str
    appimage_url: str
    appimage_name: str
    checksums_url: str

    @property
    def has_appimage(self) -> bool:
        return bool(self.appimage_url and self.checksums_url and self.appimage_name)


# --------------------------------------------------------------------------- #
# Versions
# --------------------------------------------------------------------------- #

# A small PEP 440 subset: a release segment plus an optional pre-release and an
# optional post-release. That covers every tag this project can produce
# (0.1.0a1, 0.1.0, 0.1.1) without pulling packaging in as a dependency for the
# sake of one comparison. Anything else fails to parse and is treated as
# "cannot compare", which means no update is offered.
_VERSION_PATTERN = re.compile(
    r"""
    ^\s*v?
    (?P<release>\d+(?:\.\d+)*)
    (?:[-_.]?(?P<pre>a|b|c|rc|alpha|beta|pre|preview)[-_.]?(?P<pre_number>\d+)?)?
    (?:[-_.]?post[-_.]?(?P<post>\d+)?)?
    \s*$
    """,
    re.VERBOSE | re.IGNORECASE,
)
_PRE_RANKS = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "c": 2, "rc": 2, "pre": 2, "preview": 2}
# A final release sorts after every pre-release of the same number, so its
# pre-release key has to be larger than any (0, rank, number).
_FINAL = (1, 0, 0)

VersionKey = tuple[tuple[int, ...], tuple[int, int, int], int]


def parse_version(text: str) -> VersionKey | None:
    """Return a sortable key for ``text``, or None when it is not a version."""
    match = _VERSION_PATTERN.match(text or "")
    if match is None:
        return None
    release = tuple(int(part) for part in match["release"].split("."))
    # 0.1 and 0.1.0 are the same release, so trailing zeros carry no meaning.
    while len(release) > 1 and release[-1] == 0:
        release = release[:-1]
    pre = match["pre"]
    if pre is None:
        pre_key = _FINAL
    else:
        pre_key = (0, _PRE_RANKS[pre.lower()], int(match["pre_number"] or 0))
    return release, pre_key, int(match["post"] or 0)


def is_newer(candidate: str, current: str) -> bool:
    """True only when both parse and ``candidate`` is strictly newer."""
    left, right = parse_version(candidate), parse_version(current)
    if left is None or right is None:
        return False
    return left > right


def normalise_version(text: str) -> str:
    """Strip a leading ``v`` and surrounding space from a release tag."""
    stripped = (text or "").strip()
    return stripped[1:] if stripped[:1].lower() == "v" else stripped


def current_version() -> str:
    """The version of the TuxFlow that is running.

    The installed distribution's metadata comes from pyproject.toml, which is
    also what names the released AppImage, so it is the value that can be
    compared against a release tag. A plain source checkout has no metadata,
    and there ``tuxflow.__version__`` is the only answer available.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover - importlib.metadata is stdlib since 3.8
        return __version__
    try:
        return version("tuxflow")
    except PackageNotFoundError:
        return __version__


# --------------------------------------------------------------------------- #
# Talking to GitHub
# --------------------------------------------------------------------------- #


def _require_https(url: str) -> str:
    # The asset URLs come out of a JSON document. Refusing anything but https
    # means a doctored response cannot talk this into a plaintext download.
    if not url.lower().startswith("https://"):
        raise UpdateError(f"Refusing to fetch a non-HTTPS URL: {url or '(empty)'}")
    return url


def _open(url: str, timeout: float, accept: str) -> Any:
    """The single seam every network call goes through; stubbed in tests."""
    request = urllib.request.Request(
        _require_https(url),
        headers={"User-Agent": USER_AGENT, "Accept": accept},
    )
    return urllib.request.urlopen(request, timeout=timeout)


def latest_release(
    *, timeout: float = CHECK_TIMEOUT, url: str = LATEST_RELEASE_URL, strict: bool = False
) -> dict | None:
    """The latest published release as GitHub describes it, or None.

    None means "there is nothing to compare against": GitHub answers 404 when a
    project has no published release at all, which is also what a project whose
    only release is still a draft looks like from outside.

    Everything else — a used-up anonymous rate limit, a machine with no network,
    a body that is not the JSON object we expect — is a failed check rather than
    an answer. In the default fail-soft mode those return None too, because a
    background check must never put an error in front of someone who only
    wanted to dictate. ``strict=True`` raises UpdateError instead, which is what
    ``tuxflow update`` wants: the user asked, so they deserve the reason.
    """
    try:
        with _open(url, timeout, "application/vnd.github+json") as response:
            payload = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        if strict:
            reason = (
                "GitHub's anonymous API rate limit is used up. Try again in an hour."
                if error.code == 403
                else f"GitHub answered {error.code} for {url}."
            )
            raise UpdateError(reason) from error
        return None
    except (urllib.error.URLError, OSError, ValueError, UpdateError) as error:
        # URLError covers every DNS and TCP failure; ValueError covers a body
        # that did not parse as JSON.
        if strict:
            raise UpdateError(f"Could not reach {url}: {error}") from error
        return None
    if isinstance(payload, dict):
        return payload
    if strict:
        raise UpdateError(f"{url} did not answer with a release document.")
    return None


def _asset_url(assets: object, matches: Callable[[str], bool]) -> tuple[str, str]:
    """Return (download url, file name) for the first matching asset."""
    if not isinstance(assets, list):
        return "", ""
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if name and url and matches(name):
            return url, name
    return "", ""


def update_from_release(payload: dict, *, current: str | None = None) -> Update | None:
    """Turn a release document into an Update, or None when it is not newer."""
    tag = str(payload.get("tag_name") or payload.get("name") or "")
    version = normalise_version(tag)
    if not is_newer(version, current if current is not None else current_version()):
        return None
    assets = payload.get("assets")
    appimage_url, appimage_name = _asset_url(assets, lambda name: name.endswith(".AppImage"))
    checksums_url, _ = _asset_url(assets, lambda name: name == CHECKSUM_ASSET)
    return Update(
        version=version,
        release_url=str(payload.get("html_url") or "") or RELEASES_PAGE_URL,
        appimage_url=appimage_url,
        appimage_name=appimage_name,
        checksums_url=checksums_url,
    )


def check_for_update(
    *, timeout: float = CHECK_TIMEOUT, url: str = LATEST_RELEASE_URL, current: str | None = None
) -> Update | None:
    """Fail-soft check. Returns an Update only when a newer release exists."""
    payload = latest_release(timeout=timeout, url=url)
    if payload is None:
        return None
    try:
        return update_from_release(payload, current=current)
    except (TypeError, ValueError):
        # A release document shaped in a way this build does not expect is not
        # a reason to break the app's startup.
        return None


# --------------------------------------------------------------------------- #
# Are we an AppImage?
# --------------------------------------------------------------------------- #


def running_appimage_path() -> Path | None:
    """The .AppImage file this process was started from, or None.

    The AppImage runtime exports ``APPIMAGE`` with the absolute path of the
    bundle. It is left unresolved on purpose: if the user launched a symlink,
    that symlink is the thing they run, and following it would rewrite a file
    they may not have meant to touch.
    """
    value = os.environ.get("APPIMAGE", "").strip()
    if not value:
        return None
    path = Path(value)
    return path if path.is_file() else None


# --------------------------------------------------------------------------- #
# Installing
# --------------------------------------------------------------------------- #


_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def parse_checksums(text: str) -> dict[str, str]:
    """Parse ``sha256sum`` output into {file name: digest}."""
    digests: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        digest = parts[0].lower()
        # sha256sum marks binary mode with a '*' in front of the name.
        name = os.path.basename(parts[1].strip().lstrip("*").strip())
        if name and _DIGEST_PATTERN.match(digest):
            digests[name] = digest
    return digests


def _fetch_text(url: str, timeout: float) -> str:
    try:
        with _open(url, timeout, "*/*") as response:
            return response.read(1024 * 1024).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as error:
        raise UpdateError(f"Could not download {os.path.basename(url)}: {error}") from error


def _expected_digest(update: Update, timeout: float) -> str:
    if not update.checksums_url:
        raise UpdateError(
            f"Release {update.version} publishes no {CHECKSUM_ASSET} file, so the "
            "download cannot be verified. Refusing to install it."
        )
    digests = parse_checksums(_fetch_text(update.checksums_url, timeout))
    digest = digests.get(update.appimage_name)
    if not digest:
        raise UpdateError(
            f"{CHECKSUM_ASSET} has no entry for {update.appimage_name}, so the "
            "download cannot be verified. Refusing to install it."
        )
    return digest


def _writable_directory(target: Path) -> Path:
    directory = target.parent
    if not os.access(directory, os.W_OK | os.X_OK):
        raise UpdateError(
            f"{directory} is not writable by this user, so TuxFlow cannot replace "
            f"{target.name} in place. Move the AppImage somewhere you own (for "
            "example ~/Applications) and try again, or download the new version "
            "from the release page."
        )
    return directory


def _download_to(
    url: str,
    handle: Any,
    timeout: float,
    progress: ProgressCallback | None,
) -> str:
    """Stream ``url`` into an open file, returning its sha256 hex digest."""
    digest = hashlib.sha256()
    received = 0
    try:
        with _open(url, timeout, "application/octet-stream") as response:
            total = 0
            try:
                total = int(response.headers.get("Content-Length") or 0)
            except (AttributeError, TypeError, ValueError):
                total = 0
            while chunk := response.read(_CHUNK):
                handle.write(chunk)
                digest.update(chunk)
                received += len(chunk)
                if progress is not None:
                    fraction = min(received / total, 1.0) if total > 0 else 0.0
                    progress(fraction, f"Downloading TuxFlow ({received // (1024 * 1024)} MB)")
        handle.flush()
        os.fsync(handle.fileno())
    except (urllib.error.URLError, OSError) as error:
        raise UpdateError(f"Download failed: {error}") from error
    return digest.hexdigest()


def download_and_install(
    update: Update,
    *,
    target: Path | None = None,
    timeout: float = DOWNLOAD_TIMEOUT,
    progress: ProgressCallback | None = None,
) -> Path:
    """Replace the running AppImage with ``update`` after verifying its digest.

    Raises UpdateError, with a sentence fit to show a user, for every failure:
    not running as an AppImage, a missing or mismatched checksum, an
    unwritable directory, or a download that broke off. The existing AppImage
    is only touched by the final atomic rename, so a failure at any earlier
    point leaves the working copy exactly as it was.
    """
    target = target or running_appimage_path()
    if target is None:
        raise UpdateError(
            "This copy of TuxFlow was not started from an AppImage, so it cannot "
            f"replace itself. Install {update.version} from {update.release_url}."
        )
    if not update.appimage_url:
        raise UpdateError(f"Release {update.version} publishes no AppImage to install.")

    directory = _writable_directory(target)
    if progress is not None:
        progress(0.0, "Fetching checksums")
    # Fetched before the AppImage itself: if the release has no usable checksum
    # there is no point spending a few hundred megabytes to find out.
    expected = _expected_digest(update, timeout)

    # The temporary file has to live in the target's own directory: os.replace
    # is only atomic within one filesystem, and /tmp is very often another one.
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".tuxflow-update-", suffix=".AppImage", dir=directory
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            actual = _download_to(update.appimage_url, handle, timeout, progress)
        if actual != expected:
            raise UpdateError(
                "The downloaded AppImage does not match the checksum published "
                f"with release {update.version} (expected {expected}, got {actual}). "
                "Nothing was installed."
            )
        if progress is not None:
            progress(1.0, "Installing")
        os.chmod(temporary, 0o755)
        # The running bundle keeps its own inode open, so swapping the file
        # underneath it is safe; the new version is picked up on next start.
        os.replace(temporary, target)
    except OSError as error:
        raise UpdateError(f"Could not install the update: {error}") from error
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return target


# --------------------------------------------------------------------------- #
# "Have we asked recently?"
# --------------------------------------------------------------------------- #


def due_for_check(last: float, now: float, interval: float = CHECK_INTERVAL_SECONDS) -> bool:
    """True when the last check is old enough — or implausible — to redo.

    A last-check stamp in the future means the clock moved, not that the check
    happened; waiting a day for a machine that booted with a wrong date would
    be the wrong answer.
    """
    if last <= 0 or last > now:
        return True
    return (now - last) >= interval


def read_last_check(path: Path | None = None) -> float:
    """The unix time of the last check, or 0.0 when there has never been one."""
    path = path or update_state_file()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return float(raw["last_check"]) if isinstance(raw, dict) else 0.0
    except (OSError, ValueError, TypeError, KeyError):
        return 0.0


def write_last_check(when: float | None = None, path: Path | None = None) -> None:
    """Record that a check just happened. Never raises; this is only a throttle."""
    path = path or update_state_file()
    payload = json.dumps({"last_check": float(when if when is not None else time.time())}) + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
    except OSError:
        pass


def check_is_due(*, now: float | None = None, path: Path | None = None) -> bool:
    return due_for_check(read_last_check(path), now if now is not None else time.time())
