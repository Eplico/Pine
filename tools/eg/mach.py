# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Run Firefox's `mach` in the prepared tree with Evergreen's mozconfig."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .config import EgError
from .extensions import bundled_xpis

OBJDIR_NAME = "obj-evergreen"


def _check_windows_shell() -> None:
    if sys.platform.startswith("win") and "MOZILLABUILD" not in os.environ:
        raise EgError(
            "On Windows, run eg.py from the MozillaBuild shell "
            "(C:\\mozilla-build\\start-shell.bat). See docs/development.md."
        )


def run_mach(tree: Path, args: list[str]) -> None:
    if not (tree / "mach").exists():
        raise EgError(f"{tree} is not a prepared Firefox tree; run `eg.py prepare`.")
    _check_windows_shell()
    env = dict(os.environ, MOZCONFIG=str(tree / "mozconfig"))
    cmd = [sys.executable, str(tree / "mach"), *args]
    print("+ mach " + " ".join(args))
    result = subprocess.run(cmd, cwd=tree, env=env)
    if result.returncode != 0:
        raise EgError(f"mach {args[0]} failed (exit {result.returncode})")


def bootstrap(tree: Path) -> None:
    run_mach(tree, ["--no-interactive", "bootstrap", "--application-choice", "browser"])


def build(tree: Path, faster: bool = False) -> None:
    run_mach(tree, ["build", "faster"] if faster else ["build"])


def stage_distribution(tree: Path) -> None:
    """Copy bundled extensions into the build output before packaging."""
    dest = tree / OBJDIR_NAME / "dist" / "bin" / "distribution" / "extensions"
    xpis = bundled_xpis()
    if not xpis:
        print("  (no pinned extensions to bundle; run `eg.py fetch-extensions --pin`)")
        return
    dest.mkdir(parents=True, exist_ok=True)
    for ext_id, path in xpis:
        shutil.copyfile(path, dest / f"{ext_id}.xpi")
        print(f"  bundled {ext_id}")


def package(tree: Path, installer: bool) -> None:
    """The portable package (zip on Windows) and, optionally, the installer."""
    stage_distribution(tree)
    run_mach(tree, ["package"])
    if installer and sys.platform.startswith("win"):
        build_installer(tree)


def build_installer(tree: Path) -> None:
    """The Windows installer (an NSIS setup .exe), from an already packaged build."""
    if not sys.platform.startswith("win"):
        raise EgError("The installer can only be built on Windows.")
    run_mach(tree, ["build", "installer"])


def run(tree: Path, extra: list[str]) -> None:
    run_mach(tree, ["run", *extra])
