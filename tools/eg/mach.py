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
    """Copy bundled extensions into the build output before packaging.

    Patch 0002 adds distribution/extensions/* to Firefox's package manifest,
    which fails packaging if nothing is there, so this requires them.
    """
    dest = tree / OBJDIR_NAME / "dist" / "bin" / "distribution" / "extensions"
    xpis = bundled_xpis()
    if not xpis:
        raise EgError("No bundled extensions are pinned; run `eg.py fetch-extensions --pin` first.")
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    for ext_id, path in xpis:
        shutil.copyfile(path, dest / f"{ext_id}.xpi")
        print(f"  bundled {ext_id}", flush=True)


def package(tree: Path) -> None:
    """Package the build: a zip (Windows) or tarball, and on Windows the installer.

    On Windows, Firefox's `make package` builds the NSIS installer from the zip
    as part of packaging (toolkit/mozapps/installer/packager.mk), so both land
    in obj-evergreen/dist/.
    """
    stage_distribution(tree)
    run_mach(tree, ["package"])


def run(tree: Path, extra: list[str]) -> None:
    run_mach(tree, ["run", *extra])
