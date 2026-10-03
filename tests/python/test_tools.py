# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Unit tests for eg.py's tooling. Run: python -m unittest discover tests/python"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(REPO / "tools"))

from eg import checks, config, dev, patches, prefs, prepare, release, sfxstub, upstream  # noqa: E402
from eg.config import EgError, ReleaseKey, Upstream  # noqa: E402


def make_upstream(**kw) -> Upstream:
    base = dict(
        version="157.0",
        sha512=None,
        archive="https://archive.example/pub/firefox/releases/",
        mirror="https://mirror.example/",
        release_key=ReleaseKey("A" * 40, ["B" * 40]),
    )
    base.update(kw)
    return Upstream(**base)


class PrefsTest(unittest.TestCase):
    def test_parses_strict_subset(self):
        text = (
            "/* licence\n * block */\n"
            "// comment\n"
            'pref("a.bool", true);\n'
            'pref("a.int", -3);  // trailing comment\n'
            'pref("a.str", "x\\"y");\n'
            'pref("a.dyn", false); // eg:dynamic created at runtime\n'
        )
        parsed = prefs.parse_prefs(text)
        self.assertEqual(
            [(p.name, p.value, p.dynamic) for p in parsed],
            [("a.bool", True, False), ("a.int", -3, False), ("a.str", 'x"y', False), ("a.dyn", False, True)],
        )
        self.assertEqual(
            prefs.to_autoconfig(parsed[:3]),
            'defaultPref("a.bool", true);\ndefaultPref("a.int", -3);\ndefaultPref("a.str", "x\\"y");',
        )

    def test_rejects_anything_else(self):
        for bad in (
            '#ifdef XP_WIN\npref("a", true);\n#endif',
            'pref("a", true);\npref("a", false);',
            'user_pref("a", true);',
            'pref("a", 1.5);',
            'pref("a", someVariable);',
        ):
            with self.subTest(bad=bad), self.assertRaises(EgError):
                prefs.parse_prefs(bad)

    def test_shipped_prefs_file_parses(self):
        names = [p.name for p in prefs.load_prefs(config.PREFS_FILE)]
        self.assertIn("dom.security.https_only_mode", names)
        self.assertEqual(len(names), len(set(names)))

    def test_known_in_sources(self):
        sources = {
            "modules/libpref/init/StaticPrefList.yaml": "- name: dom.security.https_only_mode\n  type: bool\n",
            "browser/app/profile/firefox.js": 'pref("sidebar.revamp", true);',
            "browser/components/urlbar/UrlbarPrefs.sys.mjs": '["trending.featureGate", false],',
        }
        known = lambda n: prefs.known_in_sources(n, sources)  # noqa: E731
        self.assertTrue(known("dom.security.https_only_mode"))
        self.assertFalse(known("dom.security.https_only"))  # no prefix matches
        self.assertTrue(known("sidebar.revamp"))
        self.assertTrue(known("browser.urlbar.trending.featureGate"))
        self.assertFalse(known("browser.urlbar.nope"))
        parsed = prefs.parse_prefs(
            'pref("sidebar.revamp", true);\npref("typo.pref", 1);\n'
            'pref("evergreen.x", 1);\npref("made.at.runtime", 1); // eg:dynamic\n'
        )
        self.assertEqual([p.name for p in prefs.unknown_prefs(parsed, sources)], ["typo.pref"])


PATCH_OK = """Evergreen-Patch: demo
Why: testing
Upstream: none
Drop-when: never
Owner: tests

--- a/browser/x.txt
+++ b/browser/x.txt
@@ -1,2 +1,2 @@
 keep
-old
+new
"""


class PatchesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def write(self, name, text, newline="\n"):
        (self.tmp / name).write_bytes(text.replace("\n", newline).encode())

    def test_parse(self):
        files, added, removed = patches.parse_diff(PATCH_OK)
        self.assertEqual((files, added, removed), (["browser/x.txt"], 1, 1))
        self.assertEqual(patches.parse_headers(PATCH_OK)["Owner"], "tests")

    def test_lint_clean_and_budget(self):
        self.write("0001-a.patch", PATCH_OK)
        self.write("series", "# comment\n0001-a.patch\n")
        self.assertEqual(patches.lint(self.tmp), [])
        self.assertEqual(patches.budget(patches.load_series(self.tmp)), (1, 2))

    def test_lint_problems(self):
        self.write("0001-a.patch", PATCH_OK.replace("Owner: tests\n", ""))
        self.write("0002-b.patch", PATCH_OK, newline="\r\n")
        self.write("0003-sec.patch", PATCH_OK.replace("browser/x.txt", "netwerk/x.txt"))
        self.write("0004-unlisted.patch", PATCH_OK)
        self.write("series", "0001-a.patch\n0002-b.patch\n0003-sec.patch\nmissing.patch\n")
        problems = "\n".join(patches.lint(self.tmp))
        self.assertIn("0001-a.patch: missing 'Owner:' header", problems)
        self.assertIn("0002-b.patch: has CRLF line endings", problems)
        self.assertIn("0003-sec.patch: touches security-sensitive paths", problems)
        self.assertIn("0004-unlisted.patch: not listed", problems)
        self.assertIn("missing.patch: listed in series but missing", problems)

    def test_repo_series_is_clean(self):
        self.assertEqual(patches.lint(config.PATCHES_DIR), [])


class UpstreamTest(unittest.TestCase):
    def test_parse_sums(self):
        sums = upstream.parse_sums("ABC  source/firefox-157.0.source.tar.xz\ndef *KEY\n\n")
        self.assertEqual(sums, {"source/firefox-157.0.source.tar.xz": "abc", "KEY": "def"})

    def test_gpg_path(self):
        self.assertEqual(upstream.gpg_path(Path("/tmp/x"), msys=False), str(Path("/tmp/x")))
        if sys.platform.startswith("win"):
            self.assertEqual(upstream.gpg_path(Path(r"C:\\Users\\a b\\k"), msys=True), "/c/Users/a b/k")

    def test_parse_validsig(self):
        status = (
            "[GNUPG:] GOODSIG 1234 Someone\n"
            "[GNUPG:] VALIDSIG " + "B" * 40 + " 2026-08-11 1786000000 0 4 0 1 10 00 " + "A" * 40 + "\n"
        )
        self.assertEqual(upstream.parse_validsig(status), ("B" * 40, "A" * 40))
        self.assertIsNone(upstream.parse_validsig("[GNUPG:] BADSIG 1234\n"))

    def test_upstream_names(self):
        up = make_upstream()
        self.assertEqual(up.mirror_tag, "FIREFOX_157_0_RELEASE")
        self.assertEqual(up.tarball_path_in_sums, "source/firefox-157.0.source.tar.xz")
        self.assertEqual(up.release_url, "https://archive.example/pub/firefox/releases/157.0/")

    def test_repo_pin_is_well_formed(self):
        up = config.load_upstream()
        self.assertRegex(up.release_key.primary, r"^[0-9A-F]{40}$")
        for k in up.release_key.signing_subkeys:
            self.assertRegex(k, r"^[0-9A-F]{40}$")


@unittest.skipUnless(upstream.find_gpg(), "gpg not installed")
class GpgVerifyTest(unittest.TestCase):
    """Real signatures with a throwaway key: the pins decide, not the KEY file."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        home = cls.tmp / "gnupg"
        home.mkdir(mode=0o700)
        gpg = upstream.find_gpg()
        msys = upstream.is_msys_gpg(gpg)
        cls.p = staticmethod(lambda path: upstream.gpg_path(path, msys))

        def run(*args):
            result = subprocess.run([gpg, "--batch", "--yes", "--homedir", cls.p(home), *args],
                                    capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(f"gpg {' '.join(args)} failed:\n{result.stderr}")
            return result

        # Loopback pinentry: gpg-agent must not try to open a passphrase dialog
        # (it does on Windows).
        nopin = ("--pinentry-mode", "loopback", "--passphrase", "")
        run(*nopin, "--quick-gen-key", "Test Release <test@example.invalid>", "ed25519", "cert", "1d")
        listing = run("--with-colons", "--list-keys").stdout
        cls.primary = [ln.split(":")[9] for ln in listing.splitlines() if ln.startswith("fpr")][0]
        run(*nopin, "--quick-add-key", cls.primary, "ed25519", "sign", "1d")
        listing = run("--with-colons", "--list-keys").stdout
        cls.subkey = [ln.split(":")[9] for ln in listing.splitlines() if ln.startswith("fpr")][1]
        cls.data = cls.tmp / "SHA512SUMS"
        cls.data.write_text("abc  source/x.tar.xz\n")
        run(*nopin, "--armor", "--detach-sign",
            "-o", cls.p(cls.tmp / "SHA512SUMS.asc"), cls.p(cls.data))
        (cls.tmp / "KEY").write_text(run("--armor", "--export", cls.primary).stdout)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def verify(self, primary, subkeys, data=None):
        up = make_upstream(release_key=ReleaseKey(primary, subkeys))
        return upstream.gpg_verify(upstream.find_gpg(), self.tmp / "KEY", self.tmp / "SHA512SUMS.asc",
                                   data or self.data, up)

    def test_pinned_key_and_subkey_pass(self):
        self.assertEqual(self.verify(self.primary, [self.subkey]), self.subkey)

    def test_wrong_primary_fails(self):
        with self.assertRaisesRegex(EgError, "primary key"):
            self.verify("F" * 40, [self.subkey])

    def test_unpinned_subkey_fails(self):
        with self.assertRaisesRegex(EgError, "not pinned"):
            self.verify(self.primary, ["E" * 40])

    def test_tampered_data_fails(self):
        tampered = self.tmp / "tampered"
        tampered.write_text("abd  source/x.tar.xz\n")
        with self.assertRaisesRegex(EgError, "FAILED"):
            self.verify(self.primary, [self.subkey], tampered)


class PrepareTest(unittest.TestCase):
    """The whole prepare pipeline on a tiny fake Firefox tarball (offline)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        env = mock.patch.dict(os.environ, {"EG_WORK_DIR": str(self.tmp / "work")})
        env.start()
        self.addCleanup(env.stop)
        self.up = make_upstream()
        self.tarball = self.tmp / "firefox-157.0.source.tar.xz"
        files = {
            "mach": "#!/usr/bin/env python3\n",
            "browser/components/moz.build": (FIXTURES / "browser-components-moz.build").read_text(),
            "browser/installer/package-manifest.in": (FIXTURES / "browser-installer-package-manifest.in").read_text(),
            "browser/installer/windows/Makefile.in": (FIXTURES / "browser-installer-windows-Makefile.in").read_text(),
            "browser/installer/windows/app.tag": (FIXTURES / "browser-installer-windows-app.tag").read_text(),
            "browser/branding/unofficial/configure.sh": "MOZ_APP_DISPLAYNAME=Nightly\n",
            "browser/branding/unofficial/locales/en-US/brand.ftl": "-brand-short-name = Nightly\n",
            "browser/branding/unofficial/firefox.ico": "upstream icon",
        }
        # Firefox's installer stub, with its icon slots' real sizes.
        self.stub = make_pe(
            {1: b"\1" * 1320, 2: b"\2" * 5160, 3: b"\3" * 11560, 4: b"\4" * 43467},
            [(16, 1), (32, 2), (48, 3), (256, 4)],
            version=(FIXTURES / "7zSD-version-info.bin").read_bytes(),
        )
        files = {name: text.encode() for name, text in files.items()}
        files["other-licenses/7zstub/firefox/7zSD.Win32.sfx"] = self.stub
        with tarfile.open(self.tarball, "w:xz") as tar:
            for name, data in files.items():
                info = tarfile.TarInfo(f"firefox-157.0/{name}")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))

    def test_prepare_and_refresh(self):
        tree = prepare.prepare(self.up, self.tarball, "windows")
        self.assertIn('"evergreen",', (tree / "browser/components/moz.build").read_text())
        self.assertTrue((tree / "browser/installer/package-manifest.in").read_text()
                        .endswith("@RESPATH@/distribution/extensions/*\n"))
        self.assertIn("SFX_MODULE = $(topsrcdir)/$(MOZ_BRANDING_DIRECTORY)/7zSD.Win32.sfx",
                      (tree / "browser/installer/windows/Makefile.in").read_text())
        self.assertIn('Title="Evergreen"', (tree / "browser/installer/windows/app.tag").read_text())
        branded = (tree / "browser/branding/evergreen/7zSD.Win32.sfx").read_bytes()
        self.assertEqual(len(branded), len(self.stub))
        self.assertNotEqual(branded, self.stub)
        strings = version_strings(branded)
        self.assertEqual((strings["FileDescription"], strings["ProductVersion"]), ("Evergreen", config.load_version()))
        self.assertTrue((tree / "browser/branding/evergreen/wizWatermark.bmp").exists())
        comp = tree / "browser/components/evergreen"
        self.assertTrue((comp / "EvergreenWindow.sys.mjs").exists())
        branding_prefs = (tree / prepare.BRANDING_PREFS).read_text()
        self.assertTrue(branding_prefs.startswith((config.BRANDING_DIR / "pref/firefox-branding.js").read_text().rstrip()))
        self.assertTrue(branding_prefs.endswith(config.PREFS_FILE.read_text()))
        self.assertFalse((comp / "evergreen.js").exists())
        self.assertTrue((tree / "browser/locales/en-US/browser/evergreen.ftl").exists())
        brand = tree / "browser/branding/evergreen"
        self.assertIn("Evergreen", (brand / "configure.sh").read_text())
        self.assertIn("Evergreen", (brand / "locales/en-US/brand.ftl").read_text())
        self.assertNotEqual((brand / "firefox.ico").read_bytes(), b"upstream icon")
        self.assertFalse((brand / "source").exists(), "icon sources are not shipped")
        mozconfig = (tree / "mozconfig").read_text()
        self.assertIn("--with-branding=browser/branding/evergreen", mozconfig)
        self.assertIn("x86_64-pc-windows-msvc", mozconfig)
        options = [ln for ln in mozconfig.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
        for forbidden in ("without-wasm-sandboxed-libraries", "disable-sandbox", "MOZ_REQUIRE_SIGNING=0", "MOZILLA_OFFICIAL"):
            self.assertFalse([o for o in options if forbidden in o], forbidden)
        state = prepare.read_state(tree)
        self.assertIn("browser/components/evergreen/EvergreenWindow.sys.mjs", state["owned"])
        # The fast dev loop may overwrite Evergreen's own files.
        prepare.refresh(self.up, "linux")
        self.assertIn("x86_64-pc-linux-gnu", (tree / "mozconfig").read_text())

    def test_overlay_refuses_to_overwrite_upstream_files(self):
        tree = self.tmp / "tree"
        src = self.tmp / "src"
        (tree / "browser").mkdir(parents=True)
        (tree / "browser/upstream.js").write_text("upstream")
        (src / "browser").mkdir(parents=True)
        (src / "browser/upstream.js").write_text("sneaky change")
        (src / "browser/new.js").write_text("new")
        with self.assertRaisesRegex(EgError, "overwrite upstream"):
            prepare.overlay(src, tree, set())
        self.assertEqual((tree / "browser/upstream.js").read_text(), "upstream")
        self.assertFalse((tree / "browser/new.js").exists(), "nothing is copied on conflict")
        self.assertEqual(prepare.overlay(src, tree, {"browser/upstream.js"}),
                         ["browser/new.js", "browser/upstream.js"])


class DevHarnessTest(unittest.TestCase):
    def test_render_cfg(self):
        cfg = dev.render_cfg()
        self.assertTrue(cfg.startswith("//"), "Firefox skips the first line of a .cfg")
        self.assertNotIn("@", cfg.replace("@ecosia", ""), "unreplaced placeholder")
        self.assertIn('defaultPref("dom.security.https_only_mode", true);', cfg)
        self.assertIn('defaultPref("app.update.auto", false);', cfg)
        self.assertIn((config.SRC_DIR / "browser/components/evergreen").resolve().as_uri(), cfg)

    def test_layouts(self):
        root = Path("/opt/ff")
        self.assertEqual(dev.Layout(root, "windows").binary, root / "firefox.exe")
        self.assertEqual(dev.Layout(root, "linux").pref_dir, root / "defaults" / "pref")
        mac = dev.Layout(Path("/Applications/Firefox.app"), "macos")
        self.assertEqual(mac.cfg, Path("/Applications/Firefox.app/Contents/Resources/evergreen-dev.cfg"))


class MirrorTest(unittest.TestCase):
    def http_error(self, code, retry_after=None):
        import email.message
        import urllib.error

        headers = email.message.Message()
        if retry_after is not None:
            headers["Retry-After"] = retry_after
        return urllib.error.HTTPError("https://mirror.example/x", code, "error", headers, None)

    def test_retries_rate_limits(self):
        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        replies = [self.http_error(429, "7"), self.http_error(429), Resp(b"ok")]

        def urlopen(request, timeout):
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply

        with mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch("urllib.request.urlopen", urlopen), \
                mock.patch.object(checks.time, "sleep") as sleep:
            os.environ.pop("EG_MIRROR_TOKEN", None)
            self.assertEqual(checks.fetch_mirror_file(make_upstream(), "TAG", "a/b.js"), "ok")
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [7, 4])

    def test_token_uses_the_api_first(self):
        up = make_upstream(mirror="https://raw.githubusercontent.com/mozilla-firefox/firefox/")
        seen = []

        def urlopen(request, timeout):
            seen.append((request.full_url, request.get_header("Authorization")))
            raise self.http_error(404)

        with mock.patch.dict(os.environ, {"EG_MIRROR_TOKEN": "t0ken"}), \
                mock.patch("urllib.request.urlopen", urlopen):
            # 404 with a token is not trusted; anonymous 404 means "no such file".
            self.assertIsNone(checks.fetch_mirror_file(up, "FIREFOX_157_0_RELEASE", "browser/app/moz.build"))
        self.assertEqual(seen, [
            ("https://api.github.com/repos/mozilla-firefox/firefox/contents/browser/app/moz.build"
             "?ref=FIREFOX_157_0_RELEASE", "Bearer t0ken"),
            ("https://raw.githubusercontent.com/mozilla-firefox/firefox/FIREFOX_157_0_RELEASE/browser/app/moz.build",
             None),
        ])

    def test_gives_up_when_every_source_is_rate_limited(self):
        def urlopen(request, timeout):
            raise self.http_error(429)

        up = make_upstream(mirror="https://raw.githubusercontent.com/o/r/")
        with mock.patch.dict(os.environ, {"EG_MIRROR_TOKEN": "t"}), \
                mock.patch("urllib.request.urlopen", urlopen), \
                mock.patch.object(checks.time, "sleep") as sleep, \
                self.assertRaisesRegex(EgError, "429"):
            checks.fetch_mirror_file(up, "TAG", "a.js")
        self.assertEqual(sleep.call_count, 9)  # three sources, three waits each

    def test_retry_delay_is_capped(self):
        self.assertEqual(checks._retry_delay(self.http_error(429, "3600"), 0), 120)
        self.assertEqual(checks._retry_delay(self.http_error(503), 9), 60)


def make_pe(icons: dict[int, bytes], group: list[tuple[int, int]], version: bytes | None = None,
            after_version: bytes = b"") -> bytes:
    """A minimal PE32 file whose only section holds resources.

    icons: resource id -> image bytes; group: (width, icon id) entries of the
    one icon group; version: a VS_VERSIONINFO block, followed in the file by
    `after_version` (unreferenced bytes) and a last resource.
    """
    rva, raw = 0x1000, 0x200

    def directory(entries):  # [(id, offset, is_subdirectory)]
        out = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, len(entries))
        for rid, off, sub in entries:
            out += struct.pack("<II", rid, off | (0x80000000 if sub else 0))
        return out

    grp = struct.pack("<HHH", 0, 1, len(group)) + b"".join(
        struct.pack("<BBBBHHIH", w % 256, w % 256, 0, 0, 1, 32, len(icons[i]), i) for w, i in group
    )
    resources = {3: dict(icons), 14: {1: grp}}
    if version is not None:
        resources[16] = {1: version}
        resources[24] = {1: b"<manifest/>"}
    leaves = [(t, i) for t in sorted(resources) for i in sorted(resources[t])]
    # Layout: root, a directory per type, one language directory per leaf,
    # data entries, then the data (in leaf order).
    off = 16 + 8 * len(resources)
    type_off = {}
    for t in sorted(resources):
        type_off[t] = off
        off += 16 + 8 * len(resources[t])
    lang_off, entry_off, data_off = {}, {}, {}
    for leaf in leaves:
        lang_off[leaf] = off
        off += 16 + 8
    for leaf in leaves:
        entry_off[leaf] = off
        off += 16
    blobs = b""
    for leaf in leaves:
        data_off[leaf] = off + len(blobs)
        blobs += resources[leaf[0]][leaf[1]]
        if leaf == (16, 1):
            blobs += after_version
    rsrc = directory([(t, type_off[t], True) for t in sorted(resources)])
    for t in sorted(resources):
        rsrc += directory([(i, lang_off[(t, i)], True) for i in sorted(resources[t])])
    for leaf in leaves:
        rsrc += directory([(1033, entry_off[leaf], False)])
    for leaf in leaves:
        rsrc += struct.pack("<IIII", rva + data_off[leaf], len(resources[leaf[0]][leaf[1]]), 0, 0)
    rsrc += blobs

    pe_off = 0x40
    head = bytearray(raw)
    head[0:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, pe_off)
    head[pe_off:pe_off + 4] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", head, pe_off + 4, 0x14C, 1, 0, 0, 0, 224, 0x102)
    opt = pe_off + 24
    struct.pack_into("<H", head, opt, 0x10B)
    struct.pack_into("<II", head, opt + 96 + 2 * 8, rva, len(rsrc))
    table = opt + 224
    head[table:table + 8] = b".rsrc\0\0\0"
    struct.pack_into("<IIII", head, table + 8, len(rsrc), rva, len(rsrc), raw)
    return bytes(head) + rsrc


def version_strings(exe: bytes) -> dict[str, str]:
    """The StringFileInfo values of an executable's version information."""
    pe = sfxstub._Pe(bytearray(exe))
    (_, entry), = pe.resources(sfxstub.RT_VERSION)
    rva, size = struct.unpack_from("<II", exe, entry)
    root = sfxstub.parse_version_info(exe[pe.offset(rva):pe.offset(rva) + size])
    (strings,), = [info[3] for info in root[3] if info[0] == "StringFileInfo"]
    return {e[0]: e[2].decode("utf-16-le").rstrip("\0") for e in strings[3]}


def make_ico(images: dict[int, bytes]) -> bytes:
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for w, data in images.items():
        out += struct.pack("<BBBBHHII", w % 256, w % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return out + b"".join(images.values())


class SfxStubTest(unittest.TestCase):
    def test_replaces_icons_in_place(self):
        stub = make_pe({1: b"A" * 40, 2: b"B" * 90}, [(16, 1), (256, 2)])
        out = sfxstub.replace_icons(stub, make_ico({16: b"x" * 30, 256: b"y" * 90}))
        self.assertEqual(len(out), len(stub))
        pe = sfxstub._Pe(bytearray(out))
        icons = {}
        for rid, leaf in pe.resources(sfxstub.RT_ICON):
            rva, size = struct.unpack_from("<II", out, leaf)
            icons[rid] = out[pe.offset(rva):pe.offset(rva) + size]
        self.assertEqual(icons, {1: b"x" * 30, 2: b"y" * 90})
        (_, group_leaf), = pe.resources(sfxstub.RT_GROUP_ICON)
        rva, _ = struct.unpack_from("<II", out, group_leaf)
        sizes = [struct.unpack_from("<I", out, pe.offset(rva) + 6 + 14 * i + 8)[0] for i in range(2)]
        self.assertEqual(sizes, [30, 90])

    def test_refuses_larger_or_missing_images(self):
        stub = make_pe({1: b"A" * 40}, [(16, 1)])
        with self.assertRaisesRegex(EgError, "room for 40"):
            sfxstub.replace_icons(stub, make_ico({16: b"x" * 41}))
        with self.assertRaisesRegex(EgError, "no 16 px image"):
            sfxstub.replace_icons(stub, make_ico({32: b"x"}))
        with self.assertRaisesRegex(EgError, "not a Windows executable"):
            sfxstub.replace_icons(b"nope" * 100, make_ico({16: b"x"}))

    def test_reads_the_stubs_version_information(self):
        # Mozilla's 7-Zip stub counts string lengths in bytes, not UTF-16 units.
        blob = (FIXTURES / "7zSD-version-info.bin").read_bytes()
        root = sfxstub.parse_version_info(blob)
        self.assertEqual(sfxstub.parse_version_info(sfxstub.build_version_info(root)), root)
        stub = make_pe({1: b"A" * 40}, [(16, 1)], version=blob)
        self.assertEqual(version_strings(stub)["FileDescription"], "Firefox")
        self.assertEqual(version_strings(stub)["ProductVersion"], "18.05")

    def test_replaces_version_strings_in_place(self):
        blob = (FIXTURES / "7zSD-version-info.bin").read_bytes()
        # As in the real stub, the bytes after the block are not free.
        stub = make_pe({1: b"A" * 40}, [(16, 1)], version=blob, after_version=b"\0\x28\x03\0")
        strings = dict(prepare.STUB_VERSION_STRINGS, ProductVersion="0.1")
        out = sfxstub.replace_version_strings(stub, strings, optional=("ProductVersion",))
        self.assertEqual(len(out), len(stub))
        got = version_strings(out)
        self.assertEqual((got["FileDescription"], got["ProductName"], got["ProductVersion"]),
                         ("Evergreen", "Evergreen", "0.1"))
        self.assertEqual(got["LegalCopyright"], "Mozilla")  # the stub's own, kept
        self.assertEqual(out[-len(b"<manifest/>"):], b"<manifest/>")
        # A version too long to fit leaves the product version blank...
        out = sfxstub.replace_version_strings(stub, dict(strings, ProductVersion="10.10.10"),
                                              optional=("ProductVersion",))
        self.assertEqual(version_strings(out)["ProductVersion"], "")
        # ...and required strings that cannot fit are an error.
        with self.assertRaisesRegex(EgError, "room for 628"):
            sfxstub.replace_version_strings(stub, {"FileDescription": "Evergreen, a browser" * 2})
        with self.assertRaisesRegex(EgError, "no version information"):
            sfxstub.replace_version_strings(make_pe({1: b"A"}, [(16, 1)]), strings)

    def test_repo_icon_fits_its_own_formats(self):
        images = sfxstub.read_ico((config.BRANDING_DIR / "source" / "installer-stub.ico").read_bytes())
        self.assertEqual(sorted(images), [16, 32, 48, 256])
        self.assertEqual(images[256][:4], b"\x89PNG")
        self.assertEqual(struct.unpack_from("<IiiHH", images[48]), (40, 48, 96, 1, 32))


class ReleaseTest(unittest.TestCase):
    """Collecting `mach package` output, laid out as Firefox 157 does on Windows."""

    def make_dist(self, root: Path, members=("evergreen/evergreen.exe", "evergreen/xul.dll")) -> Path:
        dist = root / "tree" / "obj-evergreen" / "dist"
        dist.mkdir(parents=True)
        base = "evergreen-157.0.en-US.win64"
        with zipfile.ZipFile(dist / f"{base}.zip", "w") as z:
            for name in members:
                z.writestr(name, "x")
        (dist / f"{base}.installer.exe").write_bytes(b"MZ")
        (dist / f"{base}.installer-stub.exe").write_bytes(b"MZ")
        (dist / "package_name.txt").write_text(f"{base}.zip\n", encoding="utf-8")
        return dist

    def test_find_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = self.make_dist(Path(tmp))
            found = release.find_packages(Path(tmp) / "tree")
            self.assertEqual(found["archive"], dist / "evergreen-157.0.en-US.win64.zip")
            self.assertEqual(found["installer"], dist / "evergreen-157.0.en-US.win64.installer.exe")
            # Without package_name.txt, only a Firefox-style package name matches.
            (dist / "package_name.txt").unlink()
            (dist / "evergreen-157.0.en-US.win64.xpt_artifacts.zip").write_bytes(b"")
            self.assertEqual(release.find_packages(Path(tmp) / "tree")["archive"].name,
                             "evergreen-157.0.en-US.win64.zip")

    def test_collect(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_dist(Path(tmp))
            out = Path(tmp) / "release"
            with contextlib.redirect_stdout(io.StringIO()):
                written = release.collect(Path(tmp) / "tree", "0.1", "windows", out)
            self.assertEqual(
                [p.name for p in written],
                ["Evergreen-0.1-win64-portable.zip", "Evergreen-0.1-win64-setup.exe", "SHA256SUMS.txt"],
            )
            sums = (out / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(sums), 2)
            self.assertTrue(sums[1].endswith("  Evergreen-0.1-win64-setup.exe"))

    def test_collect_rejects_wrong_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.make_dist(Path(tmp), members=("firefox/firefox.exe",))
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(EgError, "top level: firefox"):
                release.collect(Path(tmp) / "tree", "0.1", "windows", Path(tmp) / "release")
            self.assertFalse((Path(tmp) / "release").exists())



class VersionTest(unittest.TestCase):
    def test_repository_version(self):
        self.assertRegex(config.load_version(), r"^\d+\.\d+(\.\d+)?$")

    def test_rejects_other_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            for text in ("v0.1", "157.0-3", "", "0.1 beta"):
                path = Path(tmp) / "VERSION"
                path.write_text(text + "\n", encoding="utf-8")
                with self.assertRaises(EgError):
                    config.load_version(path)
            path.write_text("0.1.2\n", encoding="utf-8")
            self.assertEqual(config.load_version(path), "0.1.2")

if __name__ == "__main__":
    unittest.main()
