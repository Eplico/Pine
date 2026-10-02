# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Unit tests for eg.py's tooling. Run: python -m unittest discover tests/python"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(REPO / "tools"))

from eg import config, dev, patches, prefs, prepare, upstream  # noqa: E402
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
            "browser/branding/unofficial/configure.sh": "MOZ_APP_DISPLAYNAME=Nightly\n",
            "browser/branding/unofficial/locales/en-US/brand.ftl": "-brand-short-name = Nightly\n",
            "browser/branding/unofficial/firefox.ico": "upstream icon",
        }
        with tarfile.open(self.tarball, "w:xz") as tar:
            for name, text in files.items():
                data = text.encode()
                info = tarfile.TarInfo(f"firefox-157.0/{name}")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))

    def test_prepare_and_refresh(self):
        tree = prepare.prepare(self.up, self.tarball, "windows")
        self.assertIn('"evergreen",', (tree / "browser/components/moz.build").read_text())
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


if __name__ == "__main__":
    unittest.main()
