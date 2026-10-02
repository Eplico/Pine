# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Fast checks that need Firefox sources but not a full tarball.

CI uses Mozilla's GitHub mirror to fetch only the files the patches touch
and the files that declare prefs. The signed tarball stays the source of
truth for real builds; these checks just catch rebase breakage and pref
typos within seconds.
"""

from __future__ import annotations

import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import patches as patchlib
from .config import PATCHES_DIR, PREFS_FILE, EgError, Upstream
from .prefs import PREF_SOURCES, load_prefs, unknown_prefs


def mirror_url(up: Upstream, ref: str, path: str) -> str:
    return f"{up.mirror.rstrip('/')}/{ref}/{path}"


def _retry_delay(error: urllib.error.HTTPError, attempt: int) -> float:
    """Seconds to wait before retrying: Retry-After if given, else 2, 4, 8, ... 60."""
    retry_after = error.headers.get("Retry-After") if error.headers else None
    if retry_after and retry_after.strip().isdigit():
        return min(int(retry_after), 120)
    return min(2 ** (attempt + 1), 60)


def _api_url(up: Upstream, ref: str, path: str) -> str | None:
    """The same file through GitHub's contents API, if the mirror is on GitHub."""
    url = urllib.parse.urlparse(up.mirror)
    parts = url.path.strip("/").split("/")
    if url.hostname != "raw.githubusercontent.com" or len(parts) < 2:
        return None
    return (f"https://api.github.com/repos/{parts[0]}/{parts[1]}/contents/{urllib.parse.quote(path)}"
            f"?ref={urllib.parse.quote(ref)}")


def _get(url: str, headers: dict[str, str]) -> str:
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as resp:
        return resp.read().decode("utf-8")


def fetch_mirror_file(up: Upstream, ref: str, path: str, attempts: int = 4) -> str | None:
    """Fetch one file from Mozilla's GitHub mirror (None if it does not exist).

    With EG_MIRROR_TOKEN (CI sets it), the file comes through GitHub's API
    under the token's own rate limit: raw.githubusercontent.com rate-limits
    shared CI runners hard. Otherwise, and as a fallback, requests are
    anonymous: raw.githubusercontent.com, then the API. A generic
    GITHUB_TOKEN is deliberately not used: it may belong to something else.
    """
    token = os.environ.get("EG_MIRROR_TOKEN")
    api_headers = {"Accept": "application/vnd.github.raw+json", "X-GitHub-Api-Version": "2022-11-28"}
    api = _api_url(up, ref, path)
    sources = []  # (url, headers, whether a 404 means the file does not exist)
    if token and api:
        sources.append((api, {**api_headers, "Authorization": f"Bearer {token}"}, False))
    sources.append((mirror_url(up, ref, path), {}, True))
    if api:
        sources.append((api, api_headers, True))
    last: Exception | None = None
    for url, headers, authoritative in sources:
        headers = {"User-Agent": "evergreen-eg", **headers}
        for attempt in range(attempts):
            try:
                return _get(url, headers)
            except urllib.error.HTTPError as e:
                last = e
                if e.code == 404 and authoritative:
                    return None
                if e.code in (429, 500, 502, 503) and attempt < attempts - 1:
                    time.sleep(_retry_delay(e, attempt))
                    continue
                break  # 401/403/404 with a token, or still rate-limited: next source
            except urllib.error.URLError as e:
                last = e
                break
    raise EgError(f"Mirror request failed for {path}: {last}")


def check_patches_against_mirror(up: Upstream, ref: str | None = None) -> None:
    ref = ref or up.mirror_tag
    series = patchlib.load_series(PATCHES_DIR)
    with tempfile.TemporaryDirectory(prefix="eg-mirror-") as tmp:
        tree = Path(tmp)
        for patch in series:
            for rel in patch.files:
                text = fetch_mirror_file(up, ref, rel)
                if text is None:
                    continue  # a file the patch creates
                dest = tree / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(text, encoding="utf-8", newline="")
        print(f"Checking {len(series)} patch(es) against {ref}")
        patchlib.apply_series(tree, PATCHES_DIR)


def read_pref_sources_from_mirror(up: Upstream, ref: str) -> dict[str, str]:
    sources = {}
    for path in PREF_SOURCES:
        text = fetch_mirror_file(up, ref, path)
        if text is None:
            raise EgError(f"{path} not found at {ref}; update PREF_SOURCES in tools/eg/prefs.py")
        sources[path] = text
    return sources


def read_pref_sources_from_tree(tree: Path) -> dict[str, str]:
    return {p: (tree / p).read_text(encoding="utf-8") for p in PREF_SOURCES}


def check_prefs(sources: dict[str, str]) -> None:
    prefs = load_prefs(PREFS_FILE)
    unknown = unknown_prefs(prefs, sources)
    if unknown:
        raise EgError(
            "These prefs are not declared anywhere in Firefox, so setting them does nothing "
            "(typo, or removed upstream?):\n  "
            + "\n  ".join(f"{p.name} (evergreen.js:{p.line})" for p in unknown)
            + "\nIf Firefox creates the pref at runtime, add `// eg:dynamic <reason>` to its line."
        )
    print(f"All {len(prefs)} Evergreen prefs exist in Firefox.")
