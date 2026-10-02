# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Turn a verified Firefox source tarball into an Evergreen source tree.

Steps, in order (design doc §5.2):
  1. extract the tarball into a fresh directory
  2. apply patches/series
  3. overlay src/ (new files only: overwriting an upstream file is refused,
     because that would be an undocumented patch)
  4. create browser/branding/evergreen from Firefox's unofficial branding plus
     branding/evergreen/
  5. install prefs/evergreen.js into the Evergreen component
  6. write the mozconfig for the target platform
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tarfile
from pathlib import Path

from . import patches as patchlib
from .config import (
    BRANDING_DIR,
    MOZCONFIGS_DIR,
    PREFS_FILE,
    SRC_DIR,
    EgError,
    Upstream,
    work_dir,
)
from .prefs import load_prefs

STATE_FILE = ".eg-state.json"
EVERGREEN_COMPONENT = Path("browser/components/evergreen")


def tree_dir(up: Upstream) -> Path:
    return work_dir() / "src" / f"firefox-{up.version}"


def _safe_members(tar: tarfile.TarFile, dest: Path):
    root = dest.resolve()
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        if root not in target.parents and target != root:
            raise EgError(f"Refusing to extract {member.name}: outside the destination")
        if member.issym() or member.islnk():
            link = (target.parent / member.linkname).resolve()
            if root not in link.parents:
                raise EgError(f"Refusing to extract link {member.name} -> {member.linkname}")
        yield member


def extract(tarball: Path, up: Upstream) -> Path:
    """Extract into work/src/firefox-<version>, replacing any previous tree."""
    dest = tree_dir(up)
    if dest.exists():
        print(f"Removing previous tree {dest}")
        shutil.rmtree(dest)
    staging = dest.with_name(dest.name + ".extracting")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    print(f"Extracting {tarball.name} (this takes a few minutes)")
    tar_tool = shutil.which("tar")
    if tar_tool:
        # System tar (bsdtar on Windows 10+, GNU tar elsewhere) is much faster.
        result = subprocess.run([tar_tool, "-xf", str(tarball), "-C", str(staging)])
        if result.returncode != 0:
            raise EgError("tar failed to extract the source tarball")
    else:
        with tarfile.open(tarball) as tar:
            tar.extractall(staging, members=_safe_members(tar, staging))
    tops = [p for p in staging.iterdir()]
    if len(tops) != 1 or not tops[0].is_dir():
        raise EgError(f"Unexpected tarball layout: {[p.name for p in tops]}")
    tops[0].rename(dest)
    staging.rmdir()
    return dest


def overlay(src_root: Path, tree: Path, owned: set[str]) -> list[str]:
    """Copy every file under src_root into tree.

    A destination that already exists is only overwritten if Evergreen put it
    there before (it is in `owned`); anything else is an upstream file.
    """
    files = sorted(p for p in src_root.rglob("*") if p.is_file())
    rels = [f.relative_to(src_root).as_posix() for f in files]
    # Check everything before copying anything, so a conflict leaves the tree untouched.
    conflicts = [rel for rel in rels if (tree / rel).exists() and rel not in owned]
    if conflicts:
        raise EgError(
            "src/ would overwrite upstream Firefox files:\n  "
            + "\n  ".join(conflicts)
            + "\nChange upstream files with a documented patch in patches/ instead."
        )
    for src, rel in zip(files, rels):
        dest = tree / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    return rels


def make_branding(tree: Path) -> list[str]:
    """browser/branding/evergreen = Firefox's unofficial branding + our files."""
    dest = tree / "browser/branding/evergreen"
    base = tree / "browser/branding/unofficial"
    if not dest.exists():
        if not base.exists():
            raise EgError(f"{base} is missing; cannot derive Evergreen branding")
        shutil.copytree(base, dest)
    copied = []
    for src in sorted(p for p in BRANDING_DIR.rglob("*") if p.is_file()):
        rel = src.relative_to(BRANDING_DIR)
        if rel.parts[0] == "source":  # icon sources, not shipped
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        copied.append((Path("browser/branding/evergreen") / rel).as_posix())
    return copied


def install_prefs(tree: Path) -> str:
    load_prefs(PREFS_FILE)  # validate syntax before shipping it
    rel = (EVERGREEN_COMPONENT / "evergreen.js").as_posix()
    shutil.copyfile(PREFS_FILE, tree / rel)
    return rel


def mozconfig_for(platform: str) -> str:
    parts = []
    for name in ("common.mozconfig", f"{platform}.mozconfig"):
        path = MOZCONFIGS_DIR / name
        if not path.exists():
            raise EgError(f"No mozconfig for platform '{platform}' ({path})")
        parts.append(f"# --- {name} ---\n" + path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def write_mozconfig(tree: Path, platform: str) -> Path:
    path = tree / "mozconfig"
    path.write_text(mozconfig_for(platform), encoding="utf-8")
    return path


def read_state(tree: Path) -> dict:
    path = tree / STATE_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def write_state(tree: Path, state: dict) -> None:
    (tree / STATE_FILE).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def sync_evergreen_files(tree: Path, platform: str, owned: set[str]) -> set[str]:
    owned = set(owned)
    owned.update(overlay(SRC_DIR, tree, owned))
    owned.update(make_branding(tree))
    owned.add(install_prefs(tree))
    write_mozconfig(tree, platform)
    return owned


def prepare(up: Upstream, tarball: Path, platform: str) -> Path:
    tree = extract(tarball, up)
    print("Applying patches")
    patchlib.apply_series(tree)
    print("Adding Evergreen files")
    owned = sync_evergreen_files(tree, platform, set())
    write_state(tree, {"version": up.version, "platform": platform, "owned": sorted(owned)})
    print(f"Prepared {tree}")
    return tree


def refresh(up: Upstream, platform: str) -> Path:
    """Re-copy Evergreen files into an already prepared tree (fast dev loop)."""
    tree = tree_dir(up)
    state = read_state(tree)
    if state.get("version") != up.version:
        raise EgError("No prepared tree for this version; run `eg.py prepare` first.")
    owned = sync_evergreen_files(tree, platform, set(state.get("owned", [])))
    state.update(platform=platform, owned=sorted(owned))
    write_state(tree, state)
    print(f"Refreshed Evergreen files in {tree}")
    return tree
