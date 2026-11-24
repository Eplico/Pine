#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Does a packaged Evergreen look right to a Linux desktop?

Installs the tarball with evergreen-install into a throwaway home folder, then
checks what a desktop checks when it shows a launcher or a window:

  * the app-menu entry (evergreen.desktop) is valid, its Exec program starts
    Evergreen and its Icon is a real PNG;
  * a real window (on a virtual X display) carries a class that the entry's
    StartupWMClass matches, which is what ties the window to the pinned
    launcher and its icon, and it has a window icon of its own.

  python tests/linux/check_desktop_integration.py \\
      --tarball release/Evergreen-0.2-linux-x86_64-portable.tar.xz \\
      --installer packaging/linux/evergreen-install

Needs Xvfb and xprop/xwininfo (x11-utils). desktop-file-validate is used if present.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

DISPLAY = ":97"


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"ok:   {msg}")


def parse_desktop(text: str) -> dict[str, str]:
    main = text.split("\n[Desktop Action")[0]
    return dict(l.split("=", 1) for l in main.splitlines() if "=" in l and not l.startswith("["))


def x(*cmd: str, env) -> str:
    r = subprocess.run(cmd, env=env, capture_output=True, text=True)
    return r.stdout


def windows(env) -> list[str]:
    ids = re.findall(r"^\s+(0x[0-9a-f]+) ", x("xwininfo", "-root", "-tree", env=env), re.M)
    return ids


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tarball", required=True)
    ap.add_argument("--installer", required=True)
    ap.add_argument("--timeout", type=int, default=120)
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="eg-desktop-check-"))
    home = tmp / "home"
    home.mkdir()
    env = dict(os.environ, HOME=str(home), XDG_DATA_HOME=str(home / ".local/share"), DISPLAY=DISPLAY,
               MOZ_CRASHREPORTER_DISABLE="1")
    env.pop("WAYLAND_DISPLAY", None)
    procs: list[subprocess.Popen] = []
    try:
        r = subprocess.run([sys.executable, args.installer, "install", "--tarball", args.tarball], env=env)
        if r.returncode:
            fail("evergreen-install could not install the tarball")
        root = home / ".local/share/evergreen"
        ok(f"installed into {root}")

        # --- the app-menu entry ---
        desktop_file = home / ".local/share/applications/evergreen.desktop"
        fields = parse_desktop(desktop_file.read_text())
        exe = fields["Exec"].split(" %u")[0].strip('"')
        if not os.access(exe, os.X_OK):
            fail(f"Exec program is not executable: {exe}")
        icon = Path(fields["Icon"])
        if not icon.is_absolute() or icon.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
            fail(f"Icon is not a PNG file: {fields['Icon']}")
        ok(f"Exec {exe}; Icon {icon.name} ({icon.stat().st_size} bytes); StartupWMClass={fields['StartupWMClass']}")
        validator = shutil.which("desktop-file-validate")
        if validator:
            r = subprocess.run([validator, str(desktop_file)], capture_output=True, text=True)
            if r.returncode:
                fail(f"desktop-file-validate: {r.stdout}{r.stderr}")
            ok("desktop-file-validate is happy")

        # --- a real window ---
        procs.append(subprocess.Popen(["Xvfb", DISPLAY, "-screen", "0", "1280x800x24"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        time.sleep(2)
        profile = tmp / "profile"
        profile.mkdir()
        log = open(tmp / "evergreen.log", "w")
        procs.append(subprocess.Popen([exe, "-profile", str(profile), "-no-remote", "about:blank"],
                                      env=env, stdout=log, stderr=subprocess.STDOUT))
        want = fields["StartupWMClass"].lower()
        deadline = time.time() + args.timeout
        seen: dict[str, str] = {}
        found = None
        while time.time() < deadline and not found:
            time.sleep(2)
            for wid in windows(env):
                props = x("xprop", "-id", wid, "WM_CLASS", "_NET_WM_ICON", env=env)
                cls = re.search(r"WM_CLASS\(STRING\) = (.*)", props)
                if not cls:
                    continue
                names = [n.lower() for n in re.findall(r'"([^"]*)"', cls.group(1))]
                seen[wid] = cls.group(1)
                if want in names:
                    found = (wid, props)
                    break
        if not found:
            print(open(tmp / "evergreen.log").read()[-2000:])
            fail(f"no window with class {want!r} appeared; windows seen: {seen or 'none'}")
        wid, props = found
        ok(f"window {wid} has class {seen[wid]} (matches StartupWMClass)")
        # The class is set when the window is created and the icon a moment
        # later (a run on a loaded machine saw it missing): wait for it.
        icon_deadline = time.time() + 30
        while "_NET_WM_ICON(CARDINAL)" not in props and time.time() < icon_deadline:
            time.sleep(1)
            props = x("xprop", "-id", wid, "_NET_WM_ICON", env=env)
        if "_NET_WM_ICON(CARDINAL)" not in props:
            fail(f"window {wid} has no icon (_NET_WM_ICON): the taskbar would show a blank page")
        ok("the window has its own icon")
        print("\nAll desktop-integration checks passed")
        return 0
    finally:
        for p in procs:
            p.send_signal(signal.SIGTERM)
        for p in procs:
            try:
                p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                p.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
