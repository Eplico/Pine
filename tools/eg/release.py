# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Collect the packaged build into release files with checksums.

After `eg.py package`, Firefox's build leaves its packages in
obj-evergreen/dist/: the archive (evergreen-<version>.<locale>.<platform>.zip
on Windows) and, on Windows, the installer next to it
(<same name>.installer.exe). `eg.py collect` checks the archive's layout,
copies both under release names and writes SHA256SUMS.txt next to them.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import tarfile
import zipfile
from pathlib import Path

from .config import APP_NAME, EgError, Upstream
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


ARCHIVE_SUFFIXES = (".zip", ".tar.xz", ".tar.bz2", ".dmg")
# Firefox's package name: <app>-<version>.<locale>.<platform><suffix>
# (toolkit/mozapps/installer/package-name.mk).
PACKAGE_RE = re.compile(
    rf"^{APP_NAME}-[0-9][0-9A-Za-z.]*\.[A-Za-z-]+\.[a-z0-9_-]+(?:\.zip|\.tar\.xz|\.tar\.bz2|\.dmg)$"
)


def _strip_suffix(name: str) -> str:
    for suffix in ARCHIVE_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def find_packages(tree: Path) -> dict[str, Path]:
    """The portable archive and (on Windows) the installer in the build output."""
    dist = tree / OBJDIR_NAME / "dist"
    found: dict[str, Path] = {}
    # `make package` records the archive's name in dist/package_name.txt.
    name_file = dist / "package_name.txt"
    if name_file.is_file():
        archive = dist / name_file.read_text(encoding="utf-8").strip()
        if archive.is_file():
            found["archive"] = archive
    if "archive" not in found and dist.is_dir():
        archives = sorted(p for p in dist.iterdir() if p.is_file() and PACKAGE_RE.match(p.name))
        if archives:
            found["archive"] = archives[0]
    if "archive" in found:
        installer = dist / f"{_strip_suffix(found['archive'].name)}.installer.exe"
        if installer.is_file():
            found["installer"] = installer
    return found


def check_archive(archive: Path, platform: str) -> None:
    """The archive must hold <app>/<app>(.exe), as the release notes promise."""
    if archive.name.endswith(".dmg"):
        return
    binary = f"{APP_NAME}/{APP_NAME}" + (".exe" if platform == "windows" else "")
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
    else:
        with tarfile.open(archive) as t:
            names = [n.removeprefix("./") for n in t.getnames()]
    if binary not in names:
        top = sorted({n.split("/", 1)[0] for n in names})[:20]
        raise EgError(
            f"{archive.name} has no {binary} ({len(names)} entries; top level: {', '.join(top) or 'none'})"
        )


def collect(tree: Path, up: Upstream, platform: str, build: str, out: Path) -> list[Path]:
    packages = find_packages(tree)
    if "archive" not in packages:
        raise EgError(f"No packaged build in {tree / OBJDIR_NAME / 'dist'}; run `eg.py package`.")
    print(f"  package: {packages['archive']}")
    check_archive(packages["archive"], platform)
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
