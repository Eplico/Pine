# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Tests for packaging/linux/evergreen-install against a fake GitHub release.

A local HTTP server plays the GitHub API and the release downloads, serving a
tiny "Evergreen" tarball whose program is a copy of `sleep`, so a running
Evergreen can be told apart by its /proc/<pid>/exe.
"""

from __future__ import annotations

import hashlib
import http.server
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import zlib
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "packaging" / "linux" / "evergreen-install"

LINUX = sys.platform.startswith("linux")


def load_tool():
    loader = importlib.machinery.SourceFileLoader("evergreen_install", str(SCRIPT))
    spec = importlib.util.spec_from_loader("evergreen_install", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["evergreen_install"] = mod
    loader.exec_module(mod)
    return mod


def png(size: int) -> bytes:
    """A valid solid PNG of size x size."""
    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))
    raw = b"".join(b"\x00" + b"\x20\x80\x40" * size for _ in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def make_tarball(path: Path, marker: str, with_icons: bool = True) -> None:
    """evergreen/evergreen (a copy of `sleep`) plus the packaged icons."""
    with tarfile.open(path, "w:xz") as tar:
        def add(name, data: bytes, mode=0o644):
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            tar.addfile(info, io.BytesIO(data))
        add("evergreen/evergreen", Path(shutil.which("sleep")).read_bytes(), 0o755)
        add("evergreen/marker.txt", marker.encode())
        if with_icons:
            for size in (16, 32, 48, 64, 128):
                add(f"evergreen/browser/chrome/icons/default/default{size}.png", png(size))


class FakeGitHub:
    """Serves /repos/<repo>/releases and the release files from memory."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.releases: list[dict] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/repos/test/pine/releases"):
                    body = json.dumps(outer.releases).encode()
                elif self.path in outer.files:
                    body = outer.files[self.path]
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()

    def publish(self, version: str, tmp: Path, with_tool: bool = True, **extra) -> None:
        tar_name = f"Evergreen-{version}-linux-x86_64-portable.tar.xz"
        tar_path = tmp / tar_name
        make_tarball(tar_path, f"release {version}")
        data = tar_path.read_bytes()
        files = {tar_name: data}
        if with_tool:
            files["evergreen-install"] = SCRIPT.read_bytes()
        sums = "".join(f"{hashlib.sha256(d).hexdigest()}  {n}\n" for n, d in files.items())
        if extra.get("corrupt"):
            files[tar_name] = data[:-10] + b"x" * 10
        files["SHA256SUMS.txt"] = sums.encode()
        assets = []
        for name, content in files.items():
            self.files[f"/dl/v{version}/{name}"] = content
            assets.append({"name": name, "browser_download_url": f"{self.base}/dl/v{version}/{name}"})
        self.releases.insert(0, {"tag_name": f"v{version}-linux", "draft": False, "prerelease": False,
                                 "html_url": f"{self.base}/release/{version}", "assets": assets})


@unittest.skipUnless(LINUX, "the Linux installer")
class VersionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = load_tool()

    def test_ordering_is_numeric(self):
        t = self.tool
        self.assertTrue(t.is_newer("0.10", "0.9"))
        self.assertTrue(t.is_newer("0.2.1", "0.2"))
        self.assertFalse(t.is_newer("0.2", "0.2.0"))
        self.assertFalse(t.is_newer("0.1", "0.2"))
        self.assertTrue(t.is_newer("0.1", None))

    def test_rejects_garbage_versions(self):
        with self.assertRaises(self.tool.InstallError):
            self.tool.parse_version("0.2-beta")

    def test_picks_newest_linux_release_and_skips_others(self):
        t = self.tool

        def rel(tag, v=None, draft=False, pre=False, sums=True):
            v = v or tag.lstrip("v").removesuffix("-linux")
            assets = [{"name": f"Evergreen-{v}-linux-x86_64-portable.tar.xz", "browser_download_url": "https://x/t"}]
            if sums:
                assets.append({"name": "SHA256SUMS.txt", "browser_download_url": "https://x/s"})
            return {"tag_name": tag, "draft": draft, "prerelease": pre, "assets": assets, "html_url": "p"}

        releases = [
            rel("v0.3"),  # the Windows release: no -linux
            rel("v0.9-linux", draft=True),
            rel("v0.8-linux", pre=True),
            rel("v0.7-linux", sums=False),
            rel("v0.2-linux"),
            rel("v0.10-linux"),
            rel("v0.1-linux"),
        ]
        self.assertEqual(t.pick_release(releases).version, "0.10")
        self.assertEqual(t.pick_release(releases, "0.2").version, "0.2")
        with self.assertRaises(t.InstallError):
            t.pick_release(releases, "9.9")

    def test_checksum_file_parsing(self):
        h = "a" * 64
        self.assertEqual(self.tool.parse_sums(f"{h}  one.tar.xz\n{h.upper()} *two\nnot a line\n"),
                         {"one.tar.xz": h, "two": h})

    def test_plain_http_is_refused_except_to_this_computer(self):
        t = self.tool
        with self.assertRaises(t.InstallError):
            t._check_url("http://example.com/x")
        t._check_url("http://127.0.0.1:8000/x")
        t._check_url("https://api.github.com/x")

    def test_desktop_entry_quoting(self):
        t = self.tool
        self.assertEqual(t._desktop_quote("/home/u/.local/share/evergreen/evergreen"),
                         "/home/u/.local/share/evergreen/evergreen")
        self.assertEqual(t._desktop_quote("/home/my user/e"), '"/home/my user/e"')
        self.assertEqual(t._desktop_quote('/a"b$c'), '"/a\\"b\\$c"')


@unittest.skipUnless(LINUX, "the Linux installer")
class InstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = load_tool()
        cls.gh = FakeGitHub()

    @classmethod
    def tearDownClass(cls):
        cls.gh.close()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="eg-install-test-"))
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.gh.files.clear()
        self.gh.releases.clear()
        self.env = dict(os.environ, HOME=str(self.home), XDG_DATA_HOME=str(self.home / ".local/share"),
                        EVERGREEN_API=self.gh.base, EVERGREEN_REPO="test/pine")
        self.root = self.home / ".local/share/evergreen"
        self.procs: list[subprocess.Popen] = []

    def tearDown(self):
        for p in self.procs:
            p.kill()
            p.wait()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_tool(self, *args, check=True):
        r = subprocess.run([sys.executable, str(SCRIPT), *args], env=self.env, capture_output=True, text=True)
        if check and r.returncode not in (0,):
            self.fail(f"evergreen-install {' '.join(args)} exited {r.returncode}:\n{r.stdout}\n{r.stderr}")
        return r

    def start_evergreen(self):
        """Run the installed program (a copy of sleep) so /proc shows it."""
        p = subprocess.Popen([str(self.root / "app/evergreen"), "60"])
        self.procs.append(p)
        time.sleep(0.2)
        return p

    # --- install -------------------------------------------------------------

    def test_install_puts_everything_where_the_desktop_looks(self):
        self.gh.publish("0.2", self.tmp)
        self.run_tool("install")
        self.assertEqual((self.root / "VERSION").read_text().strip(), "0.2")
        self.assertTrue((self.root / "app/evergreen").is_file())
        self.assertTrue((self.root / "evergreen-install").is_file())

        desktop = (self.home / ".local/share/applications/evergreen.desktop").read_text()
        main_section = desktop.split("\n[Desktop Action")[0]
        fields = dict(line.split("=", 1) for line in main_section.splitlines() if "=" in line and not line.startswith("["))
        self.assertIn(f"Exec={self.root}/evergreen --private-window %u", desktop)
        self.assertEqual(fields["Exec"], f"{self.root}/evergreen %u")
        self.assertEqual(fields["StartupWMClass"], "evergreen")
        self.assertIn("x-scheme-handler/https", fields["MimeType"])
        # The icon is an absolute path to a file that exists: no icon cache needed.
        icon = Path(fields["Icon"])
        self.assertTrue(icon.is_absolute() and icon.is_file(), fields["Icon"])
        self.assertTrue(icon.read_bytes().startswith(b"\x89PNG"))
        self.assertTrue((self.home / ".local/share/icons/hicolor/48x48/apps/evergreen.png").is_file())

        # The launcher the entry points at exists, is executable and starts the app.
        launcher = self.root / "evergreen"
        self.assertTrue(os.access(launcher, os.X_OK))
        self.assertEqual((self.home / ".local/bin/evergreen").resolve(), launcher.resolve())
        r = subprocess.run([str(launcher), "0"], env=self.env)
        self.assertEqual(r.returncode, 0)

    def test_install_from_a_local_tarball_needs_no_network(self):
        tar = self.tmp / "Evergreen-0.5-linux-x86_64-portable.tar.xz"
        make_tarball(tar, "local")
        r = self.run_tool("install", "--tarball", str(tar), "--sha256", hashlib.sha256(tar.read_bytes()).hexdigest(),
                          "--api", "http://127.0.0.1:1")
        self.assertEqual((self.root / "VERSION").read_text().strip(), "0.5")
        bad = self.run_tool("install", "--tarball", str(tar), "--sha256", "0" * 64, check=False)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("checksum", bad.stderr)

    def test_a_corrupted_download_is_not_installed(self):
        self.gh.publish("0.2", self.tmp, corrupt=True)
        r = self.run_tool("install", check=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("checksum", r.stderr)
        self.assertFalse((self.root / "app").exists())

    def test_the_install_path_is_the_same_after_an_update(self):
        """Firefox ties the profile to this path, so it must never change."""
        self.gh.publish("0.2", self.tmp)
        self.run_tool("install")
        before = (self.root / "app").resolve()
        self.gh.publish("0.3", self.tmp)
        self.run_tool("update")
        self.assertEqual((self.root / "app").resolve(), before)
        self.assertEqual((self.root / "VERSION").read_text().strip(), "0.3")
        self.assertEqual((self.root / "app/marker.txt").read_text(), "release 0.3")
        self.assertFalse((self.root / "app.old").exists())
        self.assertFalse((self.root / "app.staged").exists())

    # --- check, stage, apply -------------------------------------------------

    def test_check_reports_newer_releases(self):
        self.gh.publish("0.2", self.tmp)
        self.run_tool("install")
        r = self.run_tool("check", "--json", check=False)
        self.assertEqual(r.returncode, 10)  # up to date
        self.assertFalse(json.loads(r.stdout)["update"])
        self.gh.publish("0.3", self.tmp)
        r = self.run_tool("check", "--json")
        info = json.loads(r.stdout)
        self.assertEqual((info["installed"], info["latest"], info["update"]), ("0.2", "0.3", True))

    def test_stage_downloads_without_touching_the_running_copy(self):
        self.gh.publish("0.2", self.tmp)
        self.run_tool("install")
        self.gh.publish("0.3", self.tmp)
        r = self.run_tool("stage", "--json")
        self.assertEqual(json.loads(r.stdout)["staged"], "0.3")
        self.assertEqual((self.root / "app/marker.txt").read_text(), "release 0.2")
        self.assertEqual((self.root / "app.staged/marker.txt").read_text(), "release 0.3")
        self.assertEqual((self.root / "VERSION").read_text().strip(), "0.2")

    def test_apply_waits_while_evergreen_is_running(self):
        self.gh.publish("0.2", self.tmp)
        self.run_tool("install")
        self.gh.publish("0.3", self.tmp)
        self.run_tool("stage")
        proc = self.start_evergreen()
        r = self.run_tool("apply", "--json", check=False)
        self.assertEqual(r.returncode, 11)
        self.assertEqual(json.loads(r.stdout)["result"], "running")
        self.assertEqual((self.root / "app/marker.txt").read_text(), "release 0.2")
        # The browser applies its own update and restarts at once.
        r = self.run_tool("apply", "--json", "--while-running")
        self.assertEqual(json.loads(r.stdout)["result"], "applied")
        self.assertEqual((self.root / "app/marker.txt").read_text(), "release 0.3")
        # The copy the running process uses is kept until it has gone.
        self.assertTrue((self.root / "app.old").is_dir())
        proc.kill()
        proc.wait()
        self.run_tool("apply")
        self.assertFalse((self.root / "app.old").exists())

    def test_apply_can_wait_for_a_quitting_evergreen(self):
        """After "Restart to update" the old browser is still exiting for a moment."""
        self.gh.publish("0.2", self.tmp)
        self.run_tool("install")
        self.gh.publish("0.3", self.tmp)
        self.run_tool("stage")
        p = subprocess.Popen([str(self.root / "app/evergreen"), "1"])  # exits after a second
        self.procs.append(p)
        time.sleep(0.2)
        started = time.monotonic()
        r = self.run_tool("apply", "--json", "--wait", "10")
        self.assertEqual(json.loads(r.stdout)["result"], "applied")
        self.assertGreater(time.monotonic() - started, 0.5)  # it did wait
        self.assertEqual((self.root / "VERSION").read_text().strip(), "0.3")

    def test_the_launcher_waits_only_when_told_the_browser_is_quitting(self):
        launcher = (self.root / "evergreen")
        self.gh.publish("0.2", self.tmp)
        self.run_tool("install")
        text = launcher.read_text()
        self.assertIn("EVERGREEN_UPDATE_WAIT", text)
        # Without the variable the wait is 0: opening a second window never stalls.
        self.assertIn("wait=0;", text)
        self.assertIn('unset EVERGREEN_UPDATE_WAIT', text)

    def test_the_launcher_switches_to_a_staged_release_when_closed(self):
        self.gh.publish("0.2", self.tmp)
        self.run_tool("install")
        self.gh.publish("0.3", self.tmp)
        self.run_tool("stage")
        subprocess.run([str(self.root / "evergreen"), "0"], env=self.env, check=True)
        self.assertEqual((self.root / "VERSION").read_text().strip(), "0.3")
        self.assertEqual((self.root / "app/marker.txt").read_text(), "release 0.3")

    def test_update_does_nothing_when_current(self):
        self.gh.publish("0.2", self.tmp)
        self.run_tool("install")
        r = self.run_tool("update")
        self.assertIn("newest", r.stderr)

    def test_update_refreshes_the_tool_itself(self):
        self.gh.publish("0.2", self.tmp)
        self.run_tool("install")
        self.gh.publish("0.3", self.tmp)
        newer = SCRIPT.read_bytes() + b"\n# a newer tool\n"
        self.gh.files["/dl/v0.3/evergreen-install"] = newer
        sums = self.gh.files["/dl/v0.3/SHA256SUMS.txt"].decode().splitlines()
        sums = [l for l in sums if not l.endswith("evergreen-install")]
        sums.append(f"{hashlib.sha256(newer).hexdigest()}  evergreen-install")
        self.gh.files["/dl/v0.3/SHA256SUMS.txt"] = ("\n".join(sums) + "\n").encode()
        self.run_tool("update")
        self.assertTrue((self.root / "evergreen-install").read_bytes().endswith(b"# a newer tool\n"))

    # --- integration and removal ---------------------------------------------

    def test_integrate_repairs_a_deleted_launcher_and_entry(self):
        self.gh.publish("0.2", self.tmp)
        self.run_tool("install")
        (self.home / ".local/share/applications/evergreen.desktop").unlink()
        (self.root / "evergreen").unlink()
        self.run_tool("integrate")
        self.assertTrue((self.home / ".local/share/applications/evergreen.desktop").is_file())
        self.assertTrue((self.root / "evergreen").is_file())

    def test_uninstall_removes_the_program_and_leaves_the_profile(self):
        self.gh.publish("0.2", self.tmp)
        self.run_tool("install")
        profile = self.home / ".mozilla/evergreen"
        profile.mkdir(parents=True)
        (profile / "prefs.js").write_text("keep me")
        self.run_tool("uninstall")
        self.assertFalse((self.root / "app").exists())
        self.assertFalse((self.home / ".local/share/applications/evergreen.desktop").exists())
        self.assertFalse((self.home / ".local/bin/evergreen").exists())
        self.assertEqual((profile / "prefs.js").read_text(), "keep me")

    def test_uninstall_refuses_while_running(self):
        self.gh.publish("0.2", self.tmp)
        self.run_tool("install")
        self.start_evergreen()
        r = self.run_tool("uninstall", check=False)
        self.assertEqual(r.returncode, 1)
        self.assertTrue((self.root / "app").is_dir())

    # --- unpacking -----------------------------------------------------------

    def test_archives_cannot_write_outside_the_folder(self):
        t = self.tool
        evil = self.tmp / "evil.tar"
        with tarfile.open(evil, "w") as tar:
            info = tarfile.TarInfo("../escaped.txt")
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
        with self.assertRaises(t.InstallError):
            t.unpack_tarball(evil, self.tmp / "out")
        self.assertFalse((self.tmp / "escaped.txt").exists())

    def test_archives_without_the_program_are_refused(self):
        t = self.tool
        odd = self.tmp / "odd.tar"
        with tarfile.open(odd, "w") as tar:
            info = tarfile.TarInfo("other/file")
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
        with self.assertRaises(t.InstallError):
            t.unpack_tarball(odd, self.tmp / "out2")


if __name__ == "__main__":
    unittest.main()
