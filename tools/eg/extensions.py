# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Extensions bundled as distribution add-ons (design doc §8.5).

distribution/extensions.json pins each XPI by URL and SHA-256. Integrity in
the browser comes from Firefox itself: release builds refuse any add-on not
signed by addons.mozilla.org (MOZ_REQUIRE_SIGNING). The pin makes builds
reproducible and stops a changed file from slipping in unnoticed.
"""

from __future__ import annotations

import hashlib
import json
import urllib.request
from pathlib import Path

from .config import DISTRIBUTION_DIR, EgError, work_dir
from .upstream import download

MANIFEST = DISTRIBUTION_DIR / "extensions.json"


def _cache(ext_id: str) -> Path:
    return work_dir() / "cache" / "extensions" / f"{ext_id}.xpi"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def fetch_extensions(pin: bool = False) -> None:
    manifest = load_manifest()
    changed = False
    for ext_id, info in manifest.items():
        dest = _cache(ext_id)
        url = info["url"]
        if pin and not info.get("sha256"):
            # Resolve "latest" to a concrete, versioned file URL before pinning.
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "evergreen-eg"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                url = resp.geturl()
        print(f"Downloading {info['name']} from {url}")
        download(url, dest)
        digest = _sha256(dest)
        if info.get("sha256"):
            if digest != info["sha256"]:
                dest.unlink()
                raise EgError(f"{ext_id}: SHA-256 mismatch (pinned {info['sha256']}, got {digest})")
            print(f"  {ext_id}: SHA-256 OK")
        elif pin:
            info.update(url=url, sha256=digest)
            changed = True
            print(f"  pinned {ext_id} at {digest}")
        else:
            print(f"  {ext_id} is not pinned; run with --pin to pin it")
    if changed:
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def bundled_xpis() -> list[tuple[str, Path]]:
    """Pinned extensions whose cached XPI matches its pin."""
    out = []
    for ext_id, info in load_manifest().items():
        path = _cache(ext_id)
        if info.get("sha256") and path.exists() and _sha256(path) == info["sha256"]:
            out.append((ext_id, path))
    return out
