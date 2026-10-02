# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Collect the packaged build into release files with checksums.

After `eg.py package` (and, on Windows, `eg.py installer`), Firefox's build
leaves its packages in obj-evergreen/dist/. `eg.py collect` copies them under
release names and writes SHA256SUMS.txt next to them.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from .config import EgError, Upstream
from .mach import OBJDIR_NAME

PLATFORM_SUFFIX = {"windows": "win64", "linux": "linux-x86_64", "macos": "mac"}


def release_version(up: Upstream, build: str) -> str:
    """Evergreen version: Firefox version plus Evergreen build number."""
    return f"{up.version}-{build}"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_packages(tree: Path) -> dict[str, Path]:
    """The portable archive and (if built) the installer in the build output."""
    dist = tree / OBJDIR_NAME / "dist"
    found: dict[str, Path] = {}
    archives = sorted(
        p for pattern in ("evergreen-*.zip", "evergreen-*.tar.xz", "evergreen-*.tar.bz2", "evergreen-*.dmg")
        for p in dist.glob(pattern)
    )
    if archives:
        found["archive"] = archives[0]
    installers = sorted((dist / "install" / "sea").glob("*.exe"))
    if installers:
        found["installer"] = installers[0]
    return found


def collect(tree: Path, up: Upstream, platform: str, build: str, out: Path) -> list[Path]:
    packages = find_packages(tree)
    if "archive" not in packages:
        raise EgError(f"No packaged build in {tree / OBJDIR_NAME / 'dist'}; run `eg.py package`.")
    version = release_version(up, build)
    suffix = PLATFORM_SUFFIX[platform]
    out.mkdir(parents=True, exist_ok=True)
    written = []
    archive = packages["archive"]
    ext = "".join(archive.suffixes[-2:]) if archive.name.endswith((".tar.xz", ".tar.bz2")) else archive.suffix
    names = {"archive": f"Evergreen-{version}-{suffix}-portable{ext}"}
    if "installer" in packages:
        names["installer"] = f"Evergreen-{version}-{suffix}-setup.exe"
    for kind, name in names.items():
        dest = out / name
        shutil.copyfile(packages[kind], dest)
        written.append(dest)
        print(f"  {kind}: {dest}")
    sums = out / "SHA256SUMS.txt"
    sums.write_text("".join(f"{_sha256(p)}  {p.name}\n" for p in written), encoding="utf-8")
    written.append(sums)
    print(f"  checksums: {sums}")
    return written
