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

import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from . import patches as patchlib
from .config import PATCHES_DIR, PREFS_FILE, EgError, Upstream
from .prefs import PREF_SOURCES, load_prefs, unknown_prefs


def mirror_url(up: Upstream, ref: str, path: str) -> str:
    return f"{up.mirror.rstrip('/')}/{ref}/{path}"


def fetch_mirror_file(up: Upstream, ref: str, path: str) -> str | None:
    req = urllib.request.Request(mirror_url(up, ref, path), headers={"User-Agent": "evergreen-eg"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise EgError(f"Mirror request failed for {path}: {e}") from e


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
