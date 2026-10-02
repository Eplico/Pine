# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Dev harness: run Evergreen's UI on a stock Firefox without a full build.

A full Firefox build takes hours. For UI work, `eg.py dev` instead:
  1. copies an installed Firefox into .eg/dev/firefox (the user's own
     install is never modified),
  2. adds an autoconfig file to that copy which applies prefs/evergreen.js
     and loads src/browser/components/evergreen straight from the repo,
  3. starts it with a dedicated profile in .eg/dev/profile.

Edit code, restart the dev browser, see the change. The same files are
compiled into real builds by `eg.py prepare` + `eg.py build`.

DEV ONLY. Autoconfig runs with full privileges and the harness disables its
sandbox; never ship this setup.
"""

from __future__ import annotations

import configparser
import os
import shutil
import subprocess
from pathlib import Path

from .config import DEV_DIR, PREFS_FILE, SRC_DIR, EgError, host_platform, work_dir
from .prefs import load_prefs, to_autoconfig

CFG_NAME = "evergreen-dev.cfg"
AUTOCONFIG_PREF_FILE = "evergreen-dev-autoconfig.js"

# Extra prefs for the dev copy only.
DEV_PREFS = {
    "app.update.auto": False,
    "app.update.checkInstallTime": False,
    "browser.shell.checkDefaultBrowser": False,
    "devtools.chrome.enabled": True,
    "devtools.debugger.remote-enabled": True,
    "devtools.debugger.prompt-connection": True,
}

DEFAULT_LOCATIONS = {
    "windows": [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Mozilla Firefox",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Mozilla Firefox",
    ],
    "linux": [Path("/usr/lib/firefox"), Path("/usr/lib64/firefox"), Path("/opt/firefox")],
    "macos": [Path("/Applications/Firefox.app")],
}


class Layout:
    """Where things live inside a Firefox installation on each platform."""

    def __init__(self, root: Path, platform: str):
        self.root = root
        self.platform = platform
        if platform == "macos":
            self.binary = root / "Contents/MacOS/firefox"
            self.resources = root / "Contents/Resources"
        else:
            self.binary = root / ("firefox.exe" if platform == "windows" else "firefox")
            self.resources = root

    @property
    def application_ini(self) -> Path:
        return self.resources / "application.ini"

    @property
    def pref_dir(self) -> Path:
        return self.resources / "defaults" / "pref"

    @property
    def cfg(self) -> Path:
        return self.resources / CFG_NAME


def dev_root() -> Path:
    return work_dir() / "dev"


def _find_install(explicit: str | None, platform: str) -> Path:
    candidates = [Path(explicit).expanduser()] if explicit else DEFAULT_LOCATIONS[platform]
    for c in candidates:
        if c.is_file():  # a path to the binary
            c = c.parent if platform != "macos" else c.parents[2]
        if Layout(c, platform).binary.exists():
            return c.resolve()
    raise EgError(
        "Could not find a Firefox installation"
        + (f" at {explicit}" if explicit else "")
        + ".\nPass --firefox <install dir>, e.g. --firefox \"C:\\Program Files\\Mozilla Firefox\"."
    )


def build_info(layout: Layout) -> tuple[str, str]:
    ini = configparser.ConfigParser()
    ini.read(layout.application_ini, encoding="utf-8")
    return ini.get("App", "Version", fallback="?"), ini.get("App", "BuildID", fallback="?")


def ensure_copy(source: Path, platform: str) -> Layout:
    """Copy the Firefox install into .eg/dev unless an identical build is there."""
    src = Layout(source, platform)
    dest_root = dev_root() / ("Firefox.app" if platform == "macos" else "firefox")
    dest = Layout(dest_root, platform)
    if dest.application_ini.exists() and build_info(dest) == build_info(src):
        return dest
    version, build_id = build_info(src)
    print(f"Copying Firefox {version} ({build_id}) from {source} into {dest_root}")
    if dest_root.exists():
        shutil.rmtree(dest_root)
    shutil.copytree(source, dest_root, symlinks=True)
    # Partner/distro customisations and enterprise policies of the source
    # install do not belong in the dev copy.
    shutil.rmtree(dest.resources / "distribution", ignore_errors=True)
    return dest


def _file_url(path: Path) -> str:
    url = path.resolve().as_uri()
    return url if url.endswith("/") else url + "/"


def render_cfg() -> str:
    template = (DEV_DIR / "evergreen-dev.cfg.in").read_text(encoding="utf-8")
    prefs = load_prefs(PREFS_FILE)
    dev_lines = "\n".join(
        f'defaultPref("{k}", {str(v).lower() if isinstance(v, bool) else v});'
        for k, v in DEV_PREFS.items()
    )
    return (
        template.replace("@EVERGREEN_PREFS@", to_autoconfig(prefs))
        .replace("@DEV_PREFS@", dev_lines)
        .replace("@COMPONENT_URL@", _file_url(SRC_DIR / "browser/components/evergreen"))
        .replace("@LOCALES_URL@", _file_url(SRC_DIR / "browser/locales"))
        .replace("@DEV_URL@", _file_url(DEV_DIR))
    )


def install_harness(layout: Layout) -> None:
    layout.pref_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DEV_DIR / "autoconfig.js", layout.pref_dir / AUTOCONFIG_PREF_FILE)
    layout.cfg.write_text(render_cfg(), encoding="utf-8")


def launch_command(layout: Layout, profile: Path, extra: list[str]) -> list[str]:
    return [str(layout.binary), "-profile", str(profile), "-no-remote", *extra]


def dev(firefox: str | None, fresh: bool, extra: list[str], dry_run: bool = False) -> list[str]:
    platform = host_platform()
    layout = ensure_copy(_find_install(firefox, platform), platform)
    install_harness(layout)
    profile = dev_root() / "profile"
    if fresh and profile.exists():
        shutil.rmtree(profile)
    profile.mkdir(parents=True, exist_ok=True)
    cmd = launch_command(layout, profile, extra)
    version, _ = build_info(layout)
    print(f"Evergreen dev harness on Firefox {version}; profile {profile}")
    if not dry_run:
        subprocess.run(cmd)
    return cmd
