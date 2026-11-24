#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""End-to-end smoke test: Evergreen's UI running inside a real Firefox.

Either loads Evergreen into a copy of an installed Firefox with the dev
harness (`eg.py dev`), or tests a real Evergreen build (--binary). Drives the
browser over Marionette and checks Spaces, containers, archiving, history
wiping, persistence across a restart, private windows and the default prefs.

  python tests/smoke/smoke_test.py --firefox "C:\\Program Files\\Mozilla Firefox"
  python tests/smoke/smoke_test.py --firefox /usr/lib/firefox --screenshots out/
  python tests/smoke/smoke_test.py --binary C:\\eg\\pkg\\evergreen\\evergreen.exe

Exit status is non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(HERE))

from eg import dev as egdev  # noqa: E402
from eg.config import PREFS_FILE, host_platform  # noqa: E402
from eg.prefs import load_prefs  # noqa: E402
from marionette import Marionette, MarionetteError  # noqa: E402

# Runs in Firefox's chrome context before every check: shared helpers.
PRELUDE = """
const w = Services.wm.getMostRecentWindow("navigator:browser");
const { EvergreenWindow } = ChromeUtils.importESModule("@BASE@EvergreenWindow.sys.mjs");
const { ArchiveStore } = ChromeUtils.importESModule("@BASE@ArchiveStore.sys.mjs");
const { SpacesStore } = ChromeUtils.importESModule("@BASE@SpacesStore.sys.mjs");
const c = EvergreenWindow.controllerFor(w);
const gB = w.gBrowser;
const sleep = ms => new Promise(r => w.setTimeout(r, ms));
const until = async (fn, ms = 10000) => {
  let end = Date.now() + ms;
  while (Date.now() < end) { let v = await fn(); if (v) return v; await sleep(100); }
  throw new Error("timed out waiting for " + fn);
};
const tabByTitle = t => gB.tabs.find(x => x.label == t);
const nextTabOpen = () => new Promise(r =>
  gB.tabContainer.addEventListener("TabOpen", e => r(e.target), { once: true }));
const space = name => SpacesStore.state.spaces.find(s => (s.name || c.defaultName) == name);
// For failure messages: every tab with its Space, URL and state.
const tabList = () => gB.tabs.map(t => [t.label, t.linkedBrowser.currentURI.spec,
  t.getAttribute("evergreen-space"), t.selected ? "selected" : "", t.hidden ? "hidden" : ""].join(" | "));
// Errors from Evergreen code: script errors and console.error() calls.
const evergreenConsoleErrors = () => {
  let scriptErrors = Services.console.getMessageArray()
    .filter(m => m instanceof Ci.nsIScriptError && !(m.flags & Ci.nsIScriptError.warningFlag))
    .map(m => `${m.sourceName}:${m.lineNumber} ${m.errorMessage}`);
  let apiErrors = Cc["@mozilla.org/consoleAPI-storage;1"].getService(Ci.nsIConsoleAPIStorage)
    .getEvents().filter(e => e.level == "error")
    .map(e => `${e.filename}:${e.lineNumber} ` + e.arguments.map(a => String(a?.message ?? a)).join(" "));
  // Evergreen's modules: moz-src:///browser/components/evergreen/ in a build,
  // resource://evergreen/ in the dev harness. (Not just /evergreen/: a build's
  // install directory is itself called evergreen.)
  return [...scriptErrors, ...apiErrors]
    .filter(s => s.includes("components/evergreen/") || s.includes("resource://evergreen/"));
};
"""


def fake_releases(version: str) -> list[dict]:
    """GitHub's release list with a Linux and a Windows release of `version`."""
    def release(tag, files):
        return {"tag_name": tag, "html_url": f"https://github.com/Eplico/Pine/releases/tag/{tag}",
                "draft": False, "prerelease": False, "assets": [{"name": n} for n in files]}
    return [
        release(f"v{version}-linux", [f"Evergreen-{version}-linux-x86_64-portable.tar.xz", "SHA256SUMS.txt"]),
        release(f"v{version}", [f"Evergreen-{version}-win64-setup.exe", "SHA256SUMS.txt"]),
    ]


class PageServer:
    """Serves /a.html, /b.html, ... with predictable titles on 127.0.0.1.

    /releases.json plays GitHub's release list, for the update-notice check;
    /v9.9/... plays that release's files."""

    corrupt = False  # serve an installer that does not match its checksum

    def __init__(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/v9.9/"):
                    # A release's files: a fake installer and its checksums.
                    # /bad/ publishes a checksum that does not match.
                    name = self.path.split("/")[-1]
                    setup = b"MZ fake installer " * 1000
                    if name == "SHA256SUMS.txt":
                        digest = hashlib.sha256(setup).hexdigest()
                        body = f"{digest}  Evergreen-9.9-win64-setup.exe\n".encode()
                    elif name == "Evergreen-9.9-win64-setup.exe":
                        body = setup if not PageServer.corrupt else setup + b"x"
                    else:
                        self.send_error(404)
                        return
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path.startswith("/releases.json"):
                    body = json.dumps(fake_releases("9.9")).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                name = self.path.strip("/").split(".")[0].upper() or "INDEX"
                body = f"<!doctype html><title>Page {name}</title><h1>Page {name}</h1>".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, page: str) -> str:
        return f"http://127.0.0.1:{self.port}/{page}.html"


class DownloadServer:
    """A file server for the accelerated-download checks, on 127.0.0.1.

    Files (all application/octet-stream attachments, each `size` bytes of the
    same repeatable pseudo-random data, throttled per connection so that
    connections overlap):
      /big.bin     byte ranges, 24 MiB
      /pause.bin   byte ranges, 32 MiB, slower (the pause and resume check)
      /flaky.bin   byte ranges, but the first few ranged requests are cut short
      /norange.bin no Accept-Ranges
      /lie.bin     says it accepts ranges, then answers a range request with the whole file
      /small.bin   byte ranges, 1 MiB
    Counters per path: ranged requests seen, most at once, requests cut short.
    """

    MiB = 1024 * 1024
    FILES = {
        "/big.bin": dict(size=24 * MiB, delay=0.008),
        "/pause.bin": dict(size=32 * MiB, delay=0.02),
        "/flaky.bin": dict(size=24 * MiB, delay=0.008, cut=3),
        "/norange.bin": dict(size=12 * MiB, delay=0.002, ranges=False),
        "/lie.bin": dict(size=12 * MiB, delay=0.002, lie=True),
        "/small.bin": dict(size=1 * MiB, delay=0),
    }
    CHUNK = 64 * 1024

    def __init__(self):
        import hashlib
        import random

        self.data = random.Random(20260123).randbytes(32 * self.MiB)
        self.sha = {p: hashlib.sha256(self.data[: f["size"]]).hexdigest() for p, f in self.FILES.items()}
        self.lock = threading.Lock()
        self.reset()
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                f = server.FILES.get(self.path.split("?")[0])
                if f is None:
                    self.send_error(404)
                    return
                server.serve(self, self.path.split("?")[0], f)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def reset(self):
        with self.lock:
            self.stats = {p: dict(requests=0, ranged=[], active=0, peak=0, cut=0) for p in self.FILES}

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def serve(self, h, path, f):
        size = f["size"]
        st = self.stats[path]
        start, end = 0, size - 1
        ranged = False
        rng = h.headers.get("Range")
        if rng and f.get("ranges", True):
            m = re.fullmatch(r"bytes=(\d+)-(\d*)", rng.strip())
            if m:
                start = int(m.group(1))
                end = min(int(m.group(2)) if m.group(2) else size - 1, size - 1)
                ranged = not f.get("lie")
                if f.get("lie"):
                    start, end = 0, size - 1
        # A changed validator means: send the whole file.
        if ranged and h.headers.get("If-Range") not in (None, '"v1"'):
            start, end, ranged = 0, size - 1, False
        with self.lock:
            st["requests"] += 1
            if rng:
                st["ranged"].append(rng)
            if ranged:
                st["active"] += 1
                st["peak"] = max(st["peak"], st["active"])
            cut = ranged and start > 0 and st["cut"] < f.get("cut", 0)
            if cut:
                st["cut"] += 1
        try:
            length = end - start + 1
            h.send_response(206 if ranged else 200)
            h.send_header("Content-Type", "application/octet-stream")
            h.send_header("Content-Disposition", f'attachment; filename="{path[1:]}"')
            h.send_header("ETag", '"v1"')
            if f.get("ranges", True):
                h.send_header("Accept-Ranges", "bytes")
            if ranged:
                h.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            h.send_header("Content-Length", str(length))
            h.end_headers()
            sent = 0
            while sent < length:
                n = min(self.CHUNK, length - sent)
                h.wfile.write(self.data[start + sent : start + sent + n])
                sent += n
                if cut and sent >= self.MiB:
                    h.close_connection = True
                    h.wfile.flush()
                    h.connection.shutdown(socket.SHUT_RDWR)
                    return
                if f["delay"]:
                    time.sleep(f["delay"])
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            if ranged:
                with self.lock:
                    st["active"] -= 1


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Run:
    def __init__(self, args):
        self.args = args
        self.failures: list[str] = []
        self.errors_before_restart: list[str] = []
        self.shots = Path(args.screenshots).resolve() if args.screenshots else None
        if self.shots:
            self.shots.mkdir(parents=True, exist_ok=True)
        platform = host_platform()
        if args.binary:
            # A real Evergreen build: its modules are compiled in at moz-src:///.
            self.binary = Path(args.binary).resolve()
            if not self.binary.is_file():
                raise SystemExit(f"No browser binary at {self.binary}")
            self.module_base = "moz-src:///browser/components/evergreen/"
            self.version = egdev.build_info(egdev.Layout(self.binary.parent, "linux"))[0]
            self.product = "Evergreen"
        else:
            # The dev harness: a copy of an installed Firefox loading src/ from the repo.
            install = egdev._find_install(args.firefox, platform)
            layout = egdev.ensure_copy(install, platform)
            egdev.install_harness(layout)
            self.binary = layout.binary
            self.module_base = "resource://evergreen/"
            self.version = egdev.build_info(layout)[0]
            self.product = "Firefox (dev harness)"
        self.profile = egdev.dev_root() / "smoke-profile"
        shutil.rmtree(self.profile, ignore_errors=True)
        self.profile.mkdir(parents=True)
        self.port = free_port()
        (self.profile / "user.js").write_text(
            "\n".join(
                [
                    f'user_pref("marionette.port", {self.port});',
                    # Marionette's "recommended" automation prefs turn tracking
                    # protection off as user values, which makes Firefox
                    # classify the profile as "custom" ETP before Evergreen's
                    # first run. Keep the profile like a real user's.
                    'user_pref("remote.prefs.recommended", false);',
                    # ...so set the automation prefs this test does need.
                    'user_pref("browser.warnOnQuit", false);',
                    'user_pref("browser.tabs.warnOnClose", false);',
                    'user_pref("browser.startup.couldRestoreSession.count", -1);',
                    'user_pref("browser.startup.homepage_override.mstone", "ignore");',
                    'user_pref("toolkit.startup.max_resumed_crashes", -1);',
                    'user_pref("app.update.disabledForTesting", true);',
                    'user_pref("extensions.update.enabled", false);',
                    # Test-only: the local page server is plain HTTP on localhost.
                    'user_pref("dom.security.https_only_mode", false);',
                    'user_pref("network.trr.mode", 5);',
                    'user_pref("network.proxy.type", 0);',
                    'user_pref("browser.shell.checkDefaultBrowser", false);',
                    # The first-run import window would cover the browser;
                    # the "import offer" check opens it on purpose.
                    'user_pref("evergreen.firstrun.importPrompt", false);',
                    # No real profile list belongs in a test (the offer is checked below).
                    'user_pref("evergreen.firstrun.profilePrompt", false);',
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        self.server = PageServer()
        self.files = DownloadServer()
        self.proc = None
        self.m = None

    # --- process control ---------------------------------------------------------

    def launch(self):
        cmd = [str(self.binary), "-profile", str(self.profile), "-no-remote",
               "--marionette", "-remote-allow-system-access"]
        # Firefox 160 only allows Marionette's chrome context (system access)
        # when this variable is set; older versions take the flag above.
        env = dict(os.environ, MOZ_CRASHREPORTER_DISABLE="1", MOZ_REMOTE_ALLOW_SYSTEM_ACCESS="1")
        if self.args.headless:
            cmd.append("-headless")
            env.update(MOZ_HEADLESS_WIDTH="1400", MOZ_HEADLESS_HEIGHT="900")
        elif sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
            cmd = ["xvfb-run", "-a", "-s", "-screen 0 1400x900x24", *cmd]
        log = open(egdev.dev_root() / "smoke-firefox.log", "a", encoding="utf-8")
        self.proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)
        self.m = Marionette(port=self.port)
        self.m.connect(wait=120)
        self.m.set_context("chrome")
        try:
            self.js(
                """
                await until(() => w && w.document.documentElement.hasAttribute("evergreen"), 30000);
                await until(() => Services.wm.getMostRecentWindow("navigator:browser").gBrowserInit?.delayedStartupFinished, 30000);
                """
            )
        except MarionetteError:
            # Say why Evergreen did not load: the browser's own error messages.
            try:
                errors = self.m.execute(
                    """let script = Services.console.getMessageArray()
                         .filter(m => m instanceof Ci.nsIScriptError && !(m.flags & Ci.nsIScriptError.warningFlag))
                         .map(m => `${m.sourceName}:${m.lineNumber} ${m.errorMessage}`);
                       let api = Cc["@mozilla.org/consoleAPI-storage;1"].getService(Ci.nsIConsoleAPIStorage)
                         .getEvents().filter(e => e.level == "error")
                         .map(e => `${e.filename}:${e.lineNumber} ` + e.arguments.map(a => String(a?.stack ?? a?.message ?? a)).join(" "));
                       return [...script, ...api].slice(0, 15);""",
                    [],
                )
                print("Evergreen did not load. Browser errors:\n  " + "\n  ".join(errors))
            except Exception as e:  # noqa: BLE001
                print("Evergreen did not load, and the browser's errors could not be read:", e)
            raise

    def quit(self):
        if self.m:
            self.m.quit()
            self.m = None
        if self.proc:
            try:
                self.proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                print("  Firefox did not quit within 60 s; killing it")
                self.proc.kill()
                self.proc.wait(timeout=30)
            self.proc = None
            if sys.platform.startswith("win"):
                # Child processes can outlive the parent briefly and keep the
                # profile locked; parent.lock can be deleted once none is left.
                lock = self.profile / "parent.lock"
                deadline = time.time() + 30
                while lock.exists() and time.time() < deadline:
                    try:
                        lock.unlink()
                    except OSError:
                        time.sleep(0.5)

    def js(self, body: str, *args):
        code = (PRELUDE + "\n" + body).replace("@BASE@", self.module_base)
        script = "return (async () => {\n" + code + "\n})();"
        return self.m.execute(script, list(args), timeout_ms=60000)

    def shot(self, name: str):
        if self.shots:
            self.m.screenshot(str(self.shots / f"{name}.png"))
        if self.args.log_screenshots:
            # A small JPEG of the whole window (page content included), as one
            # log line: "EVERGREEN-SHOT <name> data:image/jpeg;base64,...".
            data = self.js(
                """
                let r = new w.DOMRect(0, 0, w.innerWidth, w.innerHeight);
                let bmp = await w.browsingContext.currentWindowGlobal.drawSnapshot(r, 0.75, "white");
                let canvas = w.document.createElementNS("http://www.w3.org/1999/xhtml", "canvas");
                canvas.width = bmp.width;
                canvas.height = bmp.height;
                canvas.getContext("2d").drawImage(bmp, 0, 0);
                return canvas.toDataURL("image/jpeg", 0.8);
                """
            )
            print(f"EVERGREEN-SHOT {name} {data}", flush=True)

    # --- checks ------------------------------------------------------------------

    def check(self, name: str, fn):
        try:
            detail = fn()
            print(f"PASS  {name}" + (f"  {detail}" if detail else ""))
        except AssertionError as e:
            self.failures.append(name)
            print(f"FAIL  {name}: {e}")
        except MarionetteError as e:
            self.failures.append(name)
            print(f"FAIL  {name}: script error: {e}")

    def run(self):
        print(f"{self.product} {self.version}, profile {self.profile}")
        self.launch()
        a, b, c_, d = (self.server.url(p) for p in "abcd")

        def loads():
            r = self.js(
                """return {
                  header: !!w.document.getElementById("evergreen-space-header"),
                  switcher: !!w.document.getElementById("evergreen-space-switcher"),
                  name: w.document.getElementById("evergreen-space-name").textContent,
                  dots: w.document.querySelectorAll(".evergreen-space-dot").length,
                  expanded: gB.tabContainer.hasAttribute("expanded"),
                };"""
            )
            assert r["header"] and r["switcher"], r
            assert r["name"] == "Personal" and r["dots"] == 1, r
            assert r["expanded"], "sidebar should start expanded on first run"
            return r

        def layout():
            r = self.js(
                """
                let d = w.document;
                let box = id => {
                  let e = d.getElementById(id);
                  if (!e || !e.isConnected) return null;
                  let b = e.getBoundingClientRect();
                  return b.width || b.height ? [Math.round(b.left), Math.round(b.top), Math.round(b.width), Math.round(b.height)] : "hidden";
                };
                let ids = ["navigator-toolbox", "nav-bar", "PanelUI-button", "sidebar-button", "downloads-button",
                  "back-button", "forward-button", "stop-reload-button", "urlbar-container", "urlbar",
                  "unified-extensions-button", "sidebar-container", "sidebar-main", "sidebar-launcher-splitter",
                  "tabbrowser-tabbox", "TabsToolbar", "titlebar"];
                let out = { window: [w.innerWidth, w.innerHeight],
                  navOrder: [...d.getElementById("nav-bar").querySelectorAll(":scope > *, #nav-bar-customization-target > *")]
                    .filter(e => e.id && e.getBoundingClientRect().width).map(e => e.id),
                  sidebar: { visibility: Services.prefs.getStringPref("sidebar.visibility", ""),
                    expanded: w.SidebarController?._state?.launcherExpanded,
                    width: w.SidebarController?._state?.launcherWidth,
                    expandedWidth: w.SidebarController?._state?.expandedLauncherWidth,
                    nova: Services.prefs.getBoolPref("browser.nova.enabled", false),
                    density: Services.prefs.getIntPref("browser.uidensity", -1) } };
                for (let id of ids) out[id] = box(id);
                return out;
                """
            )
            for k, v in r.items():
                print(f"      {k}: {v}")
            self.shot("layout-first-run")

        def prefs():
            expected = {p.name: p.value for p in load_prefs(PREFS_FILE)}
            if not self.args.binary:
                # The dev harness overrides a few prefs on purpose (egdev.DEV_PREFS).
                expected = {k: v for k, v in expected.items() if k not in egdev.DEV_PREFS}
            actual = self.js(
                """
                let b = Services.prefs.getDefaultBranch("");
                let out = {};
                for (let name of arguments[0]) {
                  let t = b.getPrefType(name);
                  out[name] = t == b.PREF_BOOL ? b.getBoolPref(name)
                    : t == b.PREF_INT ? b.getIntPref(name)
                    : t == b.PREF_STRING ? b.getStringPref(name) : null;
                }
                return out;
                """,
                list(expected),
            )
            wrong = {k: (v, actual.get(k)) for k, v in expected.items() if actual.get(k) != v}
            assert not wrong, f"default prefs differ (expected, actual): {wrong}"
            return f"{len(expected)} default prefs applied"

        def first_run():
            r = self.js(
                """
                await until(() => Services.prefs.getBoolPref("evergreen.defaults.searchEngineApplied2", false), 20000)
                  .catch(e => { throw new Error(e.message + " / " + evergreenConsoleErrors().join(" / ")); });
                await until(() => Services.prefs.getBoolPref("evergreen.defaults.strictTrackingProtectionApplied", false), 20000);
                await sleep(500);
                let { getSearchService } = ChromeUtils.importESModule("@BASE@EvergreenStartup.sys.mjs");
                let search = getSearchService().service;
                let engine = await search.getDefault();
                return {
                  etp: Services.prefs.getStringPref("browser.contentblocking.category", ""),
                  trackingProtection: Services.prefs.getBoolPref("privacy.trackingprotection.enabled"),
                  engine: engine.name,
                  managed: Services.prefs.getStringPref("evergreen.search.managedDefaultEngineId", "") == engine.id,
                  google: !!search.getEngineByName("Google"),
                  ddg: !!search.getEngineByName("DuckDuckGo"),
                };
                """
            )
            assert r["etp"] == "strict" and r["trackingProtection"], r
            assert r["engine"] == "Ecosia" and r["managed"], r
            assert r["google"] and r["ddg"], f"Google and DuckDuckGo should stay available: {r}"
            return r

        def import_offer():
            r = self.js(
                """
                let { EvergreenStartup } = ChromeUtils.importESModule("@BASE@EvergreenStartup.sys.mjs");
                let isImport = win => win.document.documentURI.includes("migration-dialog-window");
                let result = await EvergreenStartup.offerImport();
                let opened = false;
                if (result == "opened") {
                  let win = await until(() => [...Services.wm.getEnumerator(null)].find(isImport));
                  await until(() => win.document.readyState == "complete");
                  opened = true;
                  win.close();
                }
                return { result, opened };
                """
            )
            assert r["result"] in ("opened", "nothing-found"), r
            assert r["result"] != "opened" or r["opened"], r
            return ("import window opened" if r["opened"]
                    else "no other browser's data on this machine, so no prompt")

        def spaces_and_containers():
            r = self.js(
                """
                let [a, b, c2] = arguments;
                let personal = SpacesStore.state.spaces[0];
                let ta = gB.addTrustedTab(a); let tb = gB.addTrustedTab(b);
                gB.selectedTab = ta;
                await until(() => tabByTitle("Page A") && tabByTitle("Page B"));
                let work = c.createSpace({ name: "Work", color: "blue", separate: true });
                let opened = nextTabOpen();
                w.openTrustedLinkIn(c2, "tab");
                let tc = await opened;
                await until(() => tc.label == "Page C");
                return {
                  active: c.activeSpaceId, work: work.id, workCtx: work.userContextId,
                  tcCtx: tc.userContextId, tcSpace: tc.getAttribute("evergreen-space"),
                  personalHidden: ta.hidden && tb.hidden, tcVisible: !tc.hidden,
                  personalSpaceOfA: ta.getAttribute("evergreen-space") == personal.id,
                  dots: w.document.querySelectorAll(".evergreen-space-dot").length,
                };
                """,
                a, b, c_,
            )
            assert r["active"] == r["work"], r
            assert r["workCtx"] > 0, f"Work should have its own container: {r}"
            assert r["tcCtx"] == r["workCtx"] and r["tcSpace"] == r["work"], f"new tab not in Work's container: {r}"
            assert r["personalHidden"] and r["tcVisible"] and r["personalSpaceOfA"], r
            assert r["dots"] == 2, r
            return f"Work uses container {r['workCtx']}"

        def new_tab_command():
            r = self.js(
                """
                if (!w.BrowserCommands?.openTab) return { skipped: true };
                let d = w.document;
                let box = d.getElementById("evergreen-search");
                let input = d.getElementById("evergreen-search-input");
                let key = k => input.dispatchEvent(new w.KeyboardEvent("keydown", { key: k, bubbles: true }));
                // From a page (over the start page, Enter loads in place: empty_space).
                gB.selectedTab = tabByTitle("Page C") ?? gB.selectedTab;
                await sleep(50);
                let count = gB.tabs.length;
                // Ctrl+T (the new-tab command) opens the search box, not a tab.
                let command = d.getElementById("cmd_newNavigatorTab");
                command ? command.doCommand() : w.BrowserCommands.openTab();
                await sleep(150);
                let shown = { hidden: box.hidden, mode: box.getAttribute("mode"), opened: gB.tabs.length - count,
                  focused: d.activeElement == input, windowFocused: d.hasFocus() };
                // Escape closes it without opening one.
                key("Escape");
                await sleep(50);
                let escaped = { mode: box.getAttribute("mode"), opened: gB.tabs.length - count };
                // The sidebar's + button does the same. An address and Enter: a
                // new tab, at the top of the Space's tabs.
                let plus = ["vertical-tabs-newtab-button", "tabs-newtab-button"]
                  .map(id => d.getElementById(id)).find(b => b?.checkVisibility());
                plus ? plus.click() : w.BrowserCommands.openTab();
                await sleep(100);
                let fromButton = { plus: plus?.id ?? null, mode: box.getAttribute("mode") };
                input.value = "about:robots";
                input.dispatchEvent(new w.Event("input", { bubbles: true }));
                let opened = nextTabOpen();
                key("Enter");
                let t = await opened;
                await until(() => t.linkedBrowser.currentURI.spec == "about:robots");
                let unpinned = gB.visibleTabs.filter(x => !x.pinned);
                let result = { ctx: t.userContextId, sp: t.getAttribute("evergreen-space"), first: unpinned[0] == t,
                  selected: gB.selectedTab == t, boxHidden: box.hidden, tabs: tabList() };
                gB.removeTab(t);
                return { shown, escaped, fromButton, result, workCtx: space("Work").userContextId, work: space("Work").id };
                """
            )
            if r.get("skipped"):
                return "BrowserCommands.openTab not present; skipped"
            shown, escaped, res = r["shown"], r["escaped"], r["result"]
            assert not shown["hidden"] and shown["mode"] == "launcher" and shown["opened"] == 0, r
            assert shown["focused"] or not shown["windowFocused"], f"the search box should have focus: {r}"
            assert escaped["mode"] != "launcher" and escaped["opened"] == 0, r
            assert r["fromButton"]["mode"] == "launcher", f"the + button should open the search box: {r}"
            assert res["ctx"] == r["workCtx"] and res["sp"] == r["work"], f"not in Work's container: {r}"
            assert res["first"] and res["selected"] and res["boxHidden"], f"new tab should be first and selected: {r}"
            rows = self.js(
                """
                gB.selectedTab = tabByTitle("Page C") ?? gB.selectedTab;
                await sleep(100);
                w.BrowserCommands.openTab();
                await sleep(100);
                let input = w.document.getElementById("evergreen-search-input");
                input.value = "page";
                input.dispatchEvent(new w.Event("input", { bubbles: true }));
                await until(() => w.document.querySelector('.evergreen-search-result[data-kind="page"]'), 3000)
                  .catch(() => null);
                return [...w.document.querySelectorAll(".evergreen-search-result")]
                  .map(row => row.dataset.kind + ": " + row.textContent);
                """
            )
            self.shot("search-box")
            assert any(row.startswith("page:") and "Page" in row for row in rows), (
                f"typing should suggest visited pages: {rows}"
            )
            self.js(
                """
                let input = w.document.getElementById("evergreen-search-input");
                input.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
                """
            )
            plus = f"and the + button, #{r['fromButton']['plus']}" if r["fromButton"]["plus"] else "+ button not shown"
            return f"Ctrl+T ({plus}) shows the search box and suggests visited pages; Enter opens a tab at the top, in the Space's container"

        def command_bar():
            r = self.js(
                """
                let d = w.document;
                let input = d.getElementById("evergreen-search-input");
                let rowsFor = async text => {
                  w.BrowserCommands.openTab();
                  await sleep(100);
                  input.value = text;
                  input.dispatchEvent(new w.Event("input", { bubbles: true }));
                  await until(() => d.querySelector('.evergreen-search-result[data-kind="command"]'), 3000).catch(() => null);
                  return [...d.querySelectorAll(".evergreen-search-result")].map(r => r.dataset.kind + ": " + r.textContent);
                };
                let enter = () => input.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
                let personal = SpacesStore.state.spaces[0].id;
                let work = space("Work").id;
                await c.applySpace(work);
                // A command typed after ">" runs on Enter.
                let list = await rowsFor("> switch to personal");
                enter();
                await sleep(200);
                let switched = c.activeSpaceId;
                // Without ">" the commands come after what is typed.
                let mixed = await rowsFor("switch work");
                input.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
                // Move the current tab to another Space.
                let t = c.openTabInSpace(c.activeSpace(), "about:robots");
                gB.selectedTab = t;
                await until(() => t.linkedBrowser.currentURI.spec == "about:robots");
                let from = c.activeSpaceId;
                let moveRows = await rowsFor("> move tab to work");
                enter();
                let isMoved = () => gB.tabs.some(x => x.linkedBrowser.currentURI.spec == "about:robots"
                  && x.getAttribute("evergreen-space") == work);
                await until(isMoved, 5000).catch(() => null);
                let moved = gB.tabs.filter(x => x.linkedBrowser.currentURI.spec == "about:robots")
                  .map(x => x.getAttribute("evergreen-space"));
                let afterMove = c.activeSpaceId;
                // An open tab in another Space is found by its title and selected.
                let others = await rowsFor("> page");
                enter();
                await sleep(300);
                let picked = gB.selectedTab.label;
                // Split view, where the window has it.
                let split = { available: typeof w.BrowserCommands.addTabSplitView == "function"
                  && Services.prefs.getBoolPref("browser.tabs.splitView.enabled", false) };
                if (split.available) {
                  await rowsFor("> split view");
                  enter();
                  await sleep(500);
                  split.on = !!gB.selectedTab.splitview;
                  split.rows = await rowsFor("> close split view");
                  enter();
                  await until(() => !gB.selectedTab.splitview, 4000).catch(() => null);
                  split.off = !gB.selectedTab.splitview;
                  split.tabs = tabList();
                  let sv = gB.selectedTab;
                  if (sv.label != "Page C") { gB.removeTab(sv); }
                }
                for (let x of gB.tabs.filter(x => x.linkedBrowser.currentURI.spec == "about:robots")) {
                  gB.removeTab(x);
                }
                await c.applySpace(work);
                return { list, mixed, switched, personal, from, moveRows, moved, afterMove, work, others, picked, split };
                """
            )
            assert r["list"] and r["list"][0].startswith("command:") and "Switch to" in r["list"][0], r
            assert r["switched"] == r["personal"], f"the command should switch to Personal: {r}"
            assert r["mixed"][0].startswith("search:") and any(x.startswith("command:") for x in r["mixed"]), r
            assert r["from"] == r["personal"] and r["moved"] == [r["work"]] and r["afterMove"] == r["work"], f"the tab should move to Work: {r}"
            assert any(x.startswith("command:") and "Switch to tab" in x for x in r["others"]), r
            sp = r["split"]
            if sp["available"]:
                assert sp.get("on") and sp.get("off"), f"split view should open and close: {r}"
            self.shot("command-bar")
            return "> commands run, mixed with search, open tabs found" + (", split view" if sp["available"] else "")

        def link_routing():
            r = self.js(
                """
                let [pageA, pageB] = arguments;
                let personal = SpacesStore.state.spaces[0];
                let work = space("Work");
                await c.applySpace(work.id);
                // A rule: 127.0.0.1 (the test pages) opens in Personal.
                c.setRouting("127.0.0.1", personal.id);
                let routed = c.routedSpace(pageA)?.id;
                // A new tab from the browser's own UI goes to that Space and takes over the window.
                let opened = nextTabOpen();
                w.openTrustedLinkIn(pageA, "tab");
                let t = await opened;
                await sleep(200);
                let viaUi = { space: t.getAttribute("evergreen-space"), ctx: t.userContextId,
                  active: c.activeSpaceId, selected: gB.selectedTab == t };
                gB.removeTab(t);
                // Navigation inside a tab is never routed.
                await c.applySpace(work.id);
                let inTab = c.openTabInSpace(work, "about:robots");
                gB.selectedTab = inTab;
                w.openTrustedLinkIn(pageB, "current");
                await sleep(300);
                let stayed = { space: inTab.getAttribute("evergreen-space"), active: c.activeSpaceId };
                gB.removeTab(inTab);
                // A link from another app (Firefox opens it as a tab whose load is
                // flagged "from external") follows the rule too.
                await c.applySpace(work.id);
                let ext = gB.addTab(pageB, { fromExternal: true,
                  triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal() });
                gB.selectedTab = ext;
                await until(() => [...gB.tabs].some(t => !t.closing && t.getAttribute("evergreen-space") == personal.id
                  && t.linkedBrowser.currentURI.spec == pageB), 8000).catch(() => null);
                let found = gB.tabs.filter(t => !t.closing && t.linkedBrowser.currentURI.spec == pageB);
                let external = { count: found.length, space: found[0]?.getAttribute("evergreen-space"),
                  active: c.activeSpaceId, selected: found[0] == gB.selectedTab };
                for (let t of found) { gB.removeTab(t); }
                // With the rule gone nothing is routed.
                c.setRouting("127.0.0.1", null);
                await c.applySpace(work.id);
                let none = c.routedSpace(pageA);
                return { routed, personal: personal.id, work: work.id, viaUi, stayed, external, none,
                  rules: Services.prefs.getStringPref("evergreen.routing.rules") };
                """,
                self.server.url("a"), self.server.url("b"),
            )
            assert r["routed"] == r["personal"], r
            vu = r["viaUi"]
            assert vu["space"] == r["personal"] and vu["active"] == r["personal"] and vu["selected"], r
            assert r["stayed"]["space"] == r["work"] and r["stayed"]["active"] == r["work"], f"navigation was routed: {r}"
            ex = r["external"]
            assert ex["count"] == 1 and ex["space"] == r["personal"] and ex["active"] == r["personal"] and ex["selected"], r
            assert r["none"] is None and r["rules"] == "[]", r
            return "new tabs and links from other apps follow a rule; navigation in a tab never does"

        def peek_and_mini_window():
            r = self.js(
                """
                let [pageA] = arguments;
                let mini = () => [...Services.wm.getEnumerator("navigator:browser")]
                  .filter(x => x.document.documentElement.hasAttribute("mini-window"));
                let result = { available: c.canPopOut() };
                if (!result.available) { return result; }
                let closeMinis = async () => {
                  for (let m of mini()) { m.close(); }
                  await sleep(300);
                };
                // A Favorite, and a link from it to another site.
                let fav = c.openTabInSpace(c.activeSpace(), "about:robots");
                gB.selectedTab = fav;
                await until(() => fav.linkedBrowser.currentURI.spec == "about:robots");
                gB.pinTab(fav);
                let count = gB.tabs.length;
                let link = gB.addTab(pageA, { openerBrowser: fav.linkedBrowser, triggeringPrincipal:
                  Services.scriptSecurityManager.getSystemPrincipal() });
                gB.selectedTab = link;
                await until(() => mini().length > 0, 8000).catch(() => null);
                result.peek = { minis: mini().length, tabGone: !link.isConnected || !gB.tabs.includes(link),
                  tabs: gB.tabs.length - count };
                await closeMinis();
                // With Peek off the link is an ordinary tab.
                Services.prefs.setBoolPref("evergreen.peek.enabled", false);
                let plain = gB.addTab(pageA, { openerBrowser: fav.linkedBrowser, triggeringPrincipal:
                  Services.scriptSecurityManager.getSystemPrincipal() });
                gB.selectedTab = plain;
                await sleep(1500);
                result.off = { minis: mini().length, inList: gB.tabs.includes(plain) };
                Services.prefs.clearUserPref("evergreen.peek.enabled");
                gB.removeTab(plain);
                gB.unpinTab(fav);
                gB.removeTab(fav);
                // The command pops the current tab out too.
                let t = c.openTabInSpace(c.activeSpace(), pageA);
                gB.selectedTab = t;
                await until(() => t.linkedBrowser.currentURI.spec == pageA);
                c.runCommand({ type: "mini-window" });
                await until(() => mini().length > 0, 8000).catch(() => null);
                result.command = { minis: mini().length, tabGone: !gB.tabs.includes(t) };
                await closeMinis();
                return result;
                """,
                self.server.url("a"),
            )
            if not r["available"]:
                return "mini windows not available in this Firefox; skipped"
            assert r["peek"]["minis"] == 1 and r["peek"]["tabGone"], f"a link from a Favorite should open in a mini window: {r}"
            assert r["off"]["minis"] == 0 and r["off"]["inList"], f"with Peek off the link should be a tab: {r}"
            assert r["command"]["minis"] == 1 and r["command"]["tabGone"], f"the command should pop the tab out: {r}"
            return "a link from a Favorite to another site opens in a mini window; off switch; command"

        def new_tab_at_top():
            r = self.js(
                """
                let opened = nextTabOpen();
                w.openTrustedLinkIn("about:robots", "tab");
                let t = await opened;
                await sleep(100);
                let unpinned = gB.visibleTabs.filter(x => !x.pinned);
                let first = unpinned[0] == t, count = unpinned.length;
                gB.removeTab(t);
                return { first, count, tabs: tabList() };
                """
            )
            assert r["first"], f"a new tab should open at the top: {r}"
            return f"first of {r['count']}"

        def start_page():
            r = self.js(
                """
                let d = w.document;
                let box = d.getElementById("evergreen-search");
                let input = d.getElementById("evergreen-search-input");
                let tab = gB.addTrustedTab("about:home");
                gB.selectedTab = tab;
                await until(() => tab.linkedBrowser.currentURI.spec == "about:home" && !box.hidden);
                await until(() => input.placeholder);
                let rect = el => el.getBoundingClientRect();
                let page = rect(d.getElementById("tabbrowser-tabbox")), cover = rect(box);
                let shown = { mode: box.getAttribute("mode"), solid: box.hasAttribute("solid"),
                  title: d.querySelector(".evergreen-search-title").textContent, placeholder: input.placeholder,
                  covers: Math.abs(page.left - cover.left) < 1 && Math.abs(page.width - cover.width) < 1
                    && Math.abs(page.height - cover.height) < 1,
                  background: w.getComputedStyle(box).backgroundColor };
                
                // Typed words search with the default engine.
                let { resolveInput } = ChromeUtils.importESModule("@BASE@EvergreenSearch.sys.mjs");
                let { getSearchService } = ChromeUtils.importESModule("@BASE@EvergreenStartup.sys.mjs");
                let engine = (await getSearchService().service.getDefault()).name;
                let search = resolveInput("evergreen trees");
                // Enter loads in this tab, and the page goes.
                input.value = "about:robots";
                input.dispatchEvent(new w.Event("input", { bubbles: true }));
                input.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
                await until(() => tab.linkedBrowser.currentURI.spec == "about:robots");
                await sleep(100);
                let after = { hidden: box.hidden, sameTab: gB.selectedTab == tab };
                // Back to the start page: it shows again.
                tab.linkedBrowser.loadURI(Services.io.newURI("about:home"),
                  { triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal() });
                await until(() => tab.linkedBrowser.currentURI.spec == "about:home");
                await sleep(100);
                let again = !box.hidden && box.getAttribute("mode") == "start";
                return { shown, engine, search, after, again, tabId: gB.tabs.indexOf(tab) };
                """
            )
            shown = r["shown"]
            assert shown["mode"] == "start" and shown["solid"] and shown["covers"], r
            assert shown["title"] == "Evergreen", r
            assert r["engine"] in shown["placeholder"], f"the box should name the default engine: {r}"
            assert r["search"] and r["search"]["search"], f"words should search: {r}"
            assert r["after"]["hidden"] and r["after"]["sameTab"], r
            assert r["again"], r
            self.shot("start-page")
            self.js("gB.removeTab(gB.tabs[arguments[0]]);", r["tabId"])
            return f"searches with {r['engine']}: {r['search']['url'][:40]}…"

        def switching():
            r = self.js(
                """
                let personal = SpacesStore.state.spaces[0].id;
                c.applySpace(personal);
                let s1 = { active: c.activeSpaceId, selected: gB.selectedTab.label,
                  aVisible: !tabByTitle("Page A").hidden, cHidden: tabByTitle("Page C").hidden };
                gB.selectedTab = tabByTitle("Page C");   // selecting a Work tab switches Space
                await sleep(50);
                let s2 = { active: c.activeSpaceId == space("Work").id, aHidden: tabByTitle("Page A").hidden };
                c.switchToIndex(0);
                let s3 = { active: c.activeSpaceId };
                return { personal, s1, s2, s3,
                  keys: !!w.document.getElementById("evergreen-key-space-9"), tabs: tabList() };
                """
            )
            assert r["s1"]["active"] == r["personal"] and r["s1"]["aVisible"] and r["s1"]["cHidden"], r
            assert r["s1"]["selected"] in ("Page A", "Page B"), r
            assert r["s2"]["active"] and r["s2"]["aHidden"], r
            assert r["s3"]["active"] == r["personal"] and r["keys"], r

        def keyboard_shortcut():
            # Ctrl/Cmd+Shift+2 through the real key handling, in chrome.
            accel = "\ue03d" if sys.platform == "darwin" else "\ue009"
            actions = [{"type": "key", "id": "kbd", "actions": [
                {"type": "keyDown", "value": accel}, {"type": "keyDown", "value": "\ue008"},
                {"type": "keyDown", "value": "2"}, {"type": "keyUp", "value": "2"},
                {"type": "keyUp", "value": "\ue008"}, {"type": "keyUp", "value": accel}]}]
            self.m.command("WebDriver:PerformActions", {"actions": actions})
            self.m.command("WebDriver:ReleaseActions")
            r = self.js("await sleep(200); return c.activeSpaceId == space('Work').id;")
            assert r, "Ctrl+Shift+2 did not switch to the second Space"
            self.js("c.switchToIndex(0);")

        def move_tab():
            r = self.js(
                """
                let tb = tabByTitle("Page B");
                c.moveTabToSpace(tb, space("Work").id);
                let moved = await until(() => gB.tabs.find(t => t.label == "Page B"));
                return { ctx: moved.userContextId, workCtx: space("Work").userContextId,
                  sp: moved.getAttribute("evergreen-space"), work: space("Work").id,
                  oldGone: !tb.isConnected };
                """
            )
            assert r["ctx"] == r["workCtx"] and r["sp"] == r["work"] and r["oldGone"], r
            return "reopened in Work's container"

        def archive():
            r = self.js(
                """
                let [d] = arguments;
                let personal = SpacesStore.state.spaces[0].id;
                c.applySpace(personal);
                let td = gB.addTrustedTab(d);
                await until(() => td.label == "Page D");
                let ta = tabByTitle("Page A");
                gB.selectedTab = td;
                let old = Date.now() - 13 * 3600 * 1000;
                c.setKept(td, false);
                c.setKept(ta, true);                       // kept: never archived
                ta.updateLastAccessed(old);
                let tc = tabByTitle("Page C"); tc.updateLastAccessed(old);  // Work tab
                gB.selectedTab = ta; td.updateLastAccessed(old);
                let n = EvergreenWindow.archiveIdleTabsEverywhere();
                await ArchiveStore.ready();
                await until(() => ArchiveStore.entries.length == n);
                await ArchiveStore.flush();
                let file = PathUtils.join(PathUtils.profileDir, "evergreen", "archive.jsonlz4");
                return { n, titles: ArchiveStore.entries.map(e => e.title).sort(),
                  keptStillOpen: !!tabByTitle("Page A"), fileExists: await IOUtils.exists(file) };
                """,
                d,
            )
            assert r["n"] == 2 and r["titles"] == ["Page C", "Page D"], r
            assert r["keptStillOpen"] and r["fileExists"], r
            return "idle tabs archived, kept tab left open"

        def archive_panel():
            r = self.js(
                """
                await c.openArchivePanel(w.document.getElementById("evergreen-archive-button"));
                let panel = w.document.getElementById("evergreen-archive-panel");
                await until(() => panel.state == "open");
                return w.document.querySelectorAll("#evergreen-archive-list button").length;
                """
            )
            assert r == 2, f"archive panel lists {r} entries"
            time.sleep(0.5)
            self.shot("archive-panel")
            r = self.js(
                """
                let entry = ArchiveStore.entries.find(e => e.title == "Page D");
                c.reopenArchived(entry);
                w.document.getElementById("evergreen-archive-panel").hidePopup();
                await until(() => tabByTitle("Page D"));
                return { left: ArchiveStore.entries.length, selected: gB.selectedTab.label, tabs: tabList() };
                """
            )
            assert r["left"] == 1 and r["selected"] == "Page D", r

        def purge():
            r = self.js(
                """
                let file = PathUtils.join(PathUtils.profileDir, "evergreen", "archive.jsonlz4");
                Services.obs.notifyObservers(null, "browser:purge-session-history-for-domain", "example.org");
                await sleep(100);
                let afterOtherDomain = ArchiveStore.entries.length;
                Services.obs.notifyObservers(null, "browser:purge-session-history-for-domain", "127.0.0.1");
                await until(() => ArchiveStore.entries.length == 0);
                let td = tabByTitle("Page D"); gB.selectedTab = tabByTitle("Page A");
                td.updateLastAccessed(Date.now() - 13 * 3600 * 1000);
                EvergreenWindow.archiveIdleTabsEverywhere();
                await until(() => ArchiveStore.entries.length == 1);
                Services.obs.notifyObservers(null, "browser:purge-session-history");
                await until(() => ArchiveStore.entries.length == 0);
                await ArchiveStore.flush();
                return { afterOtherDomain, fileGone: !(await IOUtils.exists(file)) };
                """
            )
            assert r["afterOtherDomain"] == 1 and r["fileGone"], r
            return "domain and full history clearing empty the archive"

        def editor():
            self.js(
                """
                c.openEditor(w.document.getElementById("evergreen-new-space-button"), { mode: "new" });
                await until(() => w.document.getElementById("evergreen-space-editor").state == "open");
                w.document.getElementById("evergreen-editor-name").value = "Reading";
                c.selectSwatch("pink");
                """
            )
            time.sleep(0.5)
            self.shot("space-editor")
            r = self.js(
                """
                w.document.querySelector("#evergreen-space-editor form").requestSubmit();
                await until(() => space("Reading"));
                let s = space("Reading");
                return { color: s.color, ctx: s.userContextId, active: c.activeSpaceId == s.id,
                  dots: w.document.querySelectorAll(".evergreen-space-dot").length };
                """
            )
            assert r == {"color": "pink", "ctx": 0, "active": True, "dots": 3}, r
            self.js(
                """
                c.applySpace(SpacesStore.state.spaces[0].id);
                gB.selectedTab = tabByTitle("Page A");
                """
            )
            time.sleep(0.8)
            self.shot("sidebar-spaces")

        def persistence():
            before = self.js(
                """
                await SpacesStore.flush();
                return { spaces: SpacesStore.state.spaces.map(s => s.name || c.defaultName),
                  active: c.activeSpaceId,
                  tabs: gB.tabs.filter(t => !t.pinned).map(t => [t.label, t.getAttribute("evergreen-space")]).sort() };
                """
            )
            # The console starts empty after a restart; keep what it had.
            self.errors_before_restart += self.js("return evergreenConsoleErrors();")
            self.quit()
            self.launch()
            after = self.js(
                """
                await until(() => tabByTitle("Page A") && tabByTitle("Page B"), 20000);
                return { spaces: SpacesStore.state.spaces.map(s => s.name || c.defaultName),
                  active: c.activeSpaceId,
                  tabs: gB.tabs.filter(t => !t.pinned).map(t => [t.label, t.getAttribute("evergreen-space")]).sort(),
                  workHidden: gB.tabs.filter(t => t.getAttribute("evergreen-space") == space("Work").id).every(t => t.hidden),
                  kept: tabByTitle("Page A").hasAttribute("evergreen-keep"),
                  engine: (await ChromeUtils.importESModule("@BASE@EvergreenStartup.sys.mjs")
                    .getSearchService().service.getDefault()).name };
                """
            )
            assert after["spaces"] == before["spaces"], (before, after)
            assert after["active"] == before["active"], (before, after)
            # Restored tabs keep their Space (blank new-tab pages may differ in label).
            want = [t for t in before["tabs"] if t[0].startswith("Page")]
            got = [t for t in after["tabs"] if t[0].startswith("Page")]
            assert got == want, (want, got)
            assert after["workHidden"] and after["kept"], after
            assert after["engine"] == "Ecosia", f"Ecosia should still be the default: {after}"
            return f"{len(after['spaces'])} Spaces and {len(got)} pages restored"

        def delete_space():
            r = self.js(
                """
                let work = space("Work"), reading = space("Reading");
                await EvergreenWindow.deleteSpace(reading.id);
                await EvergreenWindow.deleteSpace(work.id);
                await until(() => SpacesStore.state.spaces.length == 1);
                return {
                  spaces: SpacesStore.state.spaces.length,
                  workTabsLeft: gB.tabs.filter(t => t.getAttribute("evergreen-space") == work.id).length,
                  archived: ArchiveStore.entries.map(e => e.title).sort(),
                  containerGone: !w.ContextualIdentityService.getPublicIdentityFromId(work.userContextId),
                  active: c.activeSpaceId == SpacesStore.state.spaces[0].id,
                };
                """
            )
            assert r["spaces"] == 1 and r["workTabsLeft"] == 0 and r["active"], r
            assert "Page B" in r["archived"], r
            assert r["containerGone"], r
            return "tabs archived, container removed"

        def private_window():
            r = self.js(
                """
                let pw = w.OpenBrowserWindow({ private: true });
                await until(() => pw.gBrowserInit?.delayedStartupFinished, 20000);
                await sleep(300);
                let res = { evergreen: pw.document.documentElement.hasAttribute("evergreen"),
                  header: !!pw.document.getElementById("evergreen-space-header") };
                pw.close();
                return res;
                """
            )
            assert r == {"evergreen": False, "header": False}, r
            return "no Spaces UI or persistence in private windows"

        def toolbar():
            r = self.js(
                """
                await sleep(300);
                let d = w.document;
                let rect = id => d.getElementById(id)?.getBoundingClientRect();
                let visible = [...d.getElementById("nav-bar").querySelectorAll(":scope > *, #nav-bar-customization-target > *")]
                  .filter(e => e.id && e.id != "nav-bar-customization-target" && e.getBoundingClientRect().width > 0)
                  .map(e => e.id);
                return { visible, back: rect("back-button")?.left, page: rect("tabbrowser-tabbox").left,
                  icon: w.getComputedStyle(d.querySelector("#PanelUI-menu-button .toolbarbutton-icon")).listStyleImage.slice(0, 30) };
                """
            )
            v = r["visible"]
            assert v[:3] == ["PanelUI-button", "sidebar-button", "downloads-button"], r
            assert v.index("back-button") == 3, f"navigation should follow Downloads: {r}"
            assert v.index("unified-extensions-button") > v.index("urlbar-container"), r
            assert abs(r["back"] - r["page"]) <= 2, f"Back should line up with the page edge: {r}"
            assert "data:image/svg" in r["icon"], r
            return f"aligned at x={round(r['page'])}"

        def sizes():
            r = self.js(
                """
                let d = w.document, root = d.documentElement;
                let measure = async () => { await sleep(150); return {
                  nav: d.getElementById("nav-bar").getBoundingClientRect().height,
                  sidebar: c.layout.sidebarContainer.getBoundingClientRect().width }; };
                root.setAttribute("evergreen-natural-sizes", "true");
                let natural = await measure();
                root.removeAttribute("evergreen-natural-sizes");
                let evergreen = await measure();
                // What holds the toolbar at its height, for failure messages:
                // the toolbar's items at their own height (not stretched).
                let nav = d.getElementById("nav-bar");
                let sheet = "data:text/css," + encodeURIComponent(
                  "#nav-bar, #nav-bar-customization-target, .titlebar-buttonbox-container, .titlebar-buttonbox" +
                  " { align-items: flex-start !important; }");
                w.windowUtils.loadSheetUsingURIString(sheet, w.windowUtils.AUTHOR_SHEET);
                await sleep(150);
                let tallest = [...nav.querySelectorAll("*")]
                  .filter(e => e.getBoundingClientRect().width > 0)
                  .map(e => [e, e.getBoundingClientRect().height, w.getComputedStyle(e).minHeight])
                  .sort((a, b) => b[1] - a[1])
                  .slice(0, 8)
                  .map(([e, h, min]) => `${e.localName}${e.id ? "#" + e.id : ""}.${[...e.classList].join(".")}=${Math.round(h)} (min ${min})`);
                w.windowUtils.removeSheetUsingURIString(sheet, w.windowUtils.AUTHOR_SHEET);
                tallest.unshift(`nav-bar min-height ${w.getComputedStyle(nav).minHeight}, padding ${w.getComputedStyle(nav).paddingBlock}`);
                return { natural, evergreen, nova: Services.prefs.getBoolPref("browser.nova.enabled", false), tallest,
                  window: ["sizemode", "customtitlebar", "tabsintitlebar", "inFullscreen"]
                    .map(a => `${a}=${root.getAttribute(a)}`).join(" ") };
                """
            )
            nav = r["evergreen"]["nav"] / r["natural"]["nav"]
            side = r["evergreen"]["sidebar"] / r["natural"]["sidebar"]
            detail = f"toolbar {r['natural']['nav']:.0f} -> {r['evergreen']['nav']:.0f}px ({nav:.0%}), " \
                     f"sidebar {r['natural']['sidebar']:.0f} -> {r['evergreen']['sidebar']:.0f}px ({side:.0%})"
            if not r["nova"]:
                # Evergreen's sizes target Firefox's current design (Nova, 157+).
                return detail + " (older Firefox design; not checked)"
            assert 0.74 <= nav <= 0.86, f"{detail}; {r['window']}; tallest in the toolbar: {r['tallest']}"
            assert 0.60 <= side <= 0.72, detail
            return detail

        def address_bar():
            r = self.js(
                """
                await sleep(300);
                let d = w.document;
                let rect = e => e.getBoundingClientRect();
                let shown = e => e && !e.hidden && rect(e).width > 0;
                let bar = d.getElementById("urlbar-container");
                let before = bar.previousElementSibling, after = bar.nextElementSibling;
                while (before && !shown(before)) before = before.previousElementSibling;
                while (after && !shown(after)) after = after.nextElementSibling;
                let nav = rect(d.getElementById("nav-bar")), box = rect(bar);
                // -moz-window-dragging cannot be read back from computed style, so check
                // what is under the middle of each gap instead: bare toolbar, not a button.
                let under = x => { let e = d.elementFromPoint(x, nav.top + nav.height / 2);
                  return e ? (e.id || e.localName) : null; };
                let res = { nav: nav.width, bar: box.width,
                  gapStart: before ? box.left - rect(before).right : null,
                  gapEnd: after ? rect(after).left - box.right : null,
                  neighbours: [before?.id, after?.id],
                  gapItems: [under(box.left - 40), under(box.right + 40)],
                  navClass: d.getElementById("nav-bar").className };
                // While it has focus the pill opens up to a wider bar.
                w.gURLBar.focus();
                await sleep(300);
                res.focused = bar.matches(":focus-within");
                res.open = rect(bar).width;
                w.gURLBar.blur();
                w.gURLBar.view?.close?.();
                await sleep(200);
                res.closed = rect(bar).width;
                return res;
                """
            )
            assert r["gapStart"] is not None and r["gapEnd"] is not None, r
            detail = (f"pill {r['bar']:.0f}px of a {r['nav']:.0f}px toolbar, empty space "
                      f"{r['gapStart']:.0f}px / {r['gapEnd']:.0f}px either side")
            assert r["bar"] < r["nav"] * 0.5, f"the address bar should be a compact pill: {detail}: {r}"
            assert r["gapStart"] >= 60 and r["gapEnd"] >= 60, f"empty toolbar space either side of the pill: {r}"
            assert abs(r["gapStart"] - r["gapEnd"]) <= 4, f"the pill should be centred between its neighbours: {r}"
            bare = {"nav-bar", "nav-bar-customization-target"}
            assert set(r["gapItems"]) <= bare, f"the toolbar on both sides of the pill should be empty: {r}"
            assert abs(r["closed"] - r["bar"]) <= 2, f"the pill should close again after focus: {r}"
            if r["focused"]:
                assert r["open"] > r["bar"] + 100, f"the pill should open wider while focused: {r}"
            else:
                detail += " (no focus in this environment: opening not checked)"
            self.shot("address-bar")
            return detail

        def favorites():
            pages = ["about:robots", "about:logo", "about:rights"]
            self.js(
                """
                for (let url of arguments[0]) {
                  let opened = nextTabOpen();
                  w.openTrustedLinkIn(url, "tab");
                  gB.pinTab(await opened);
                }
                await sleep(500);
                """,
                pages,
            )
            self.shot("favorites")
            r = self.js(
                """
                let tabs = gB.tabs.filter(t => t.pinned);
                let rect = e => { let b = e.getBoundingClientRect(); return [b.left, b.top, b.width, b.height].map(Math.round); };
                let icon = tabs.map(t => parseFloat(w.getComputedStyle(t.querySelector(".tab-icon-image")).width));
                let res = { tiles: tabs.map(rect), sidebar: rect(c.layout.sidebarContainer), icon,
                  container: rect(w.document.getElementById("pinned-tabs-container")) };
                for (let t of tabs) gB.unpinTab(t);
                for (let t of tabs) gB.removeTab(t);
                await sleep(200);
                return res;
                """
            )
            assert len(r["tiles"]) == 3, r
            (l0, t0, w0, h0), (l1, t1, w1, h1), (l2, t2, w2, h2) = r["tiles"]
            detail = f"tiles {w0}x{h0}px, sidebar {r['sidebar'][2]}px wide"
            assert t0 == t1 and l1 > l0, f"the first two favorites should share a row: {r}"
            assert t2 > t0 and l2 == l0, f"the third favorite should start the next row: {r}"
            assert w0 >= 56 and h0 >= 48, f"favorites should be big tiles: {r}"
            assert abs(w0 * 2 - r["sidebar"][2]) <= r["sidebar"][2] * 0.2, f"two tiles should span the sidebar: {r}"
            assert all(i >= 24 for i in r["icon"] if i == i), f"favorites should have big icons: {r}"
            return detail

        def popup():
            r = self.js(
                """
                // As Firefox opens a page's popup: a browser window without a toolbar.
                let args = Cc["@mozilla.org/supports-string;1"].createInstance(Ci.nsISupportsString);
                args.data = "about:blank";
                let pw = Services.ww.openWindow(w, "chrome://browser/content/browser.xhtml", "_blank",
                  "chrome,dialog=no,location,resizable,width=480,height=560,left=10,top=10", args);
                // The new window is not scriptable until its browser document loads.
                await until(() => { try { return pw.gBrowserInit?.delayedStartupFinished; } catch { return false; } }, 20000);
                await sleep(600);
                let d = pw.document, root = d.documentElement;
                let rect = id => { let e = d.getElementById(id); return e ? e.getBoundingClientRect() : null; };
                let res = {
                  popupWindow: root.hasAttribute("popup-window"),
                  styled: root.hasAttribute("evergreen-popup"),
                  spaces: root.hasAttribute("evergreen") || !!d.getElementById("evergreen-space-header"),
                  controller: !!EvergreenWindow.controllerFor(pw),
                  sidebar: rect("sidebar-container")?.width ?? rect("sidebar-main")?.width ?? 0,
                  readOnly: pw.gURLBar.readOnly,
                  radius: pw.getComputedStyle(d.getElementById("tabbrowser-tabbox")).borderTopLeftRadius,
                  bar: rect("urlbar-container")?.width,
                  centre: [pw.screenX + pw.outerWidth / 2, pw.screenY + pw.outerHeight / 2],
                  parentCentre: [w.screenX + w.outerWidth / 2, w.screenY + w.outerHeight / 2],
                  size: [pw.outerWidth, pw.outerHeight],
                };
                pw.close();
                await sleep(300);
                return res;
                """
            )
            assert r["popupWindow"], f"the test did not open a popup window: {r}"
            assert r["styled"], f"popups should get Evergreen's look: {r}"
            assert not r["spaces"] and not r["controller"], f"popups should have no Spaces UI: {r}"
            assert not r["sidebar"], f"popups should have no sidebar: {r}"
            assert r["readOnly"], r
            assert r["radius"] == "10px", f"the page should sit in a rounded frame: {r}"
            dx = abs(r["centre"][0] - r["parentCentre"][0])
            dy = abs(r["centre"][1] - r["parentCentre"][1])
            assert dx <= 3 and dy <= 3, f"the popup should open centred over the window that opened it: {r}"
            return f"{r['size'][0]}x{r['size'][1]} popup, centred over the window, rounded frame, no sidebar"

        def sidebar_collapse():
            r = self.js(
                """
                let d = w.document, root = d.documentElement;
                let container = c.layout.sidebarContainer;
                let rect = e => e.getBoundingClientRect();
                let tabbox = d.getElementById("tabbrowser-tabbox");
                w.SidebarController.handleToolbarButtonClick();
                await sleep(400);
                let collapsed = { attr: root.hasAttribute("evergreen-sidebar-collapsed"),
                  sidebarRight: rect(container).right, page: rect(tabbox).left, pageWidth: rect(tabbox).width,
                  backAfterDownloads: rect(d.getElementById("back-button")).left - rect(d.getElementById("downloads-button")).right,
                  button: d.getElementById("sidebar-button").checked };
                // The edge reveals it over the page.
                d.getElementById("evergreen-sidebar-edge").dispatchEvent(new w.MouseEvent("mouseenter"));
                await sleep(400);
                let peek = { attr: root.hasAttribute("evergreen-sidebar-peek"), left: rect(container).left,
                  width: rect(container).width, pageWidth: rect(tabbox).width };
                // ...and it slides away when the mouse leaves.
                container.dispatchEvent(new w.MouseEvent("mouseleave"));
                await sleep(100);
                let left = { peek: root.hasAttribute("evergreen-sidebar-peek") };
                await sleep(250);
                left.sidebarRight = rect(container).right;
                w.SidebarController.handleToolbarButtonClick();
                await sleep(400);
                let expanded = { attr: root.hasAttribute("evergreen-sidebar-collapsed"), sidebarLeft: rect(container).left,
                  back: rect(d.getElementById("back-button")).left, page: rect(tabbox).left,
                  button: d.getElementById("sidebar-button").checked,
                  pref: Services.prefs.getBoolPref("evergreen.sidebar.collapsed") };
                return { collapsed, peek, left, expanded };
                """
            )
            col, peek, left, exp = r["collapsed"], r["peek"], r["left"], r["expanded"]
            assert col["attr"] and col["sidebarRight"] <= 0 and col["page"] <= 12, r
            assert col["backAfterDownloads"] < 16 and not col["button"], f"nav should follow Downloads: {r}"
            assert peek["attr"] and abs(peek["left"]) < 1 and peek["width"] > 100, r
            assert peek["pageWidth"] == col["pageWidth"], f"the page must not resize while revealed: {r}"
            assert not left["peek"], f"the sidebar should start hiding within 0.1 s of the mouse leaving: {r}"
            assert left["sidebarRight"] <= 0, f"the sidebar should be gone within 0.35 s: {r}"
            assert not exp["attr"] and exp["sidebarLeft"] == 0 and abs(exp["back"] - exp["page"]) <= 2, r
            assert exp["button"] and exp["pref"] is False, r
            return "collapses fully; the left edge slides it over the page"

        def sidebar_shortcut():
            mods = ["\ue009"] if sys.platform == "darwin" else ["\ue009", "\ue00a"]  # Ctrl(+Alt)+Z
            down = [{"type": "keyDown", "value": m} for m in mods]
            up = [{"type": "keyUp", "value": m} for m in reversed(mods)]
            actions = [{"type": "key", "id": "kbd", "actions": down + [
                {"type": "keyDown", "value": "z"}, {"type": "keyUp", "value": "z"}] + up}]
            has_key = self.js("return !!w.document.getElementById('toggleSidebarKb');")
            if not has_key:
                return "no sidebar shortcut in this Firefox; skipped"
            self.m.command("WebDriver:PerformActions", {"actions": actions})
            self.m.command("WebDriver:ReleaseActions")
            r = self.js("await sleep(300); return c.layout.collapsed;")
            assert r, "the sidebar shortcut should collapse the sidebar"
            self.shot("sidebar-collapsed")
            self.js("c.layout.peek(); await sleep(400);")
            self.shot("sidebar-peek")
            self.js("c.layout.setCollapsed(false); await sleep(300);")
            return "Ctrl+Alt+Z collapses it"

        def rename():
            r = self.js(
                """
                let d = w.document;
                let id = c.activeSpace().id;
                d.getElementById("evergreen-space-name").click();
                let input = d.getElementById("evergreen-space-name-input");
                let focused = d.activeElement == input;
                input.value = "  Home  ";
                input.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
                await sleep(100);
                let saved = { name: SpacesStore.state.spaces.find(s => s.id == id).name,
                  shown: d.getElementById("evergreen-space-name").textContent,
                  inputGone: !d.getElementById("evergreen-space-name-input") };
                d.getElementById("evergreen-space-name").click();
                input = d.getElementById("evergreen-space-name-input");
                input.value = "Nope";
                input.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
                await sleep(100);
                let cancelled = SpacesStore.state.spaces.find(s => s.id == id).name;
                c.editSpace(id, { name: "" });
                return { focused, saved, cancelled };
                """
            )
            assert r["focused"], r
            assert r["saved"] == {"name": "Home", "shown": "Home", "inputGone": True}, r
            assert r["cancelled"] == "Home", r
            return "click, type, Enter; Escape cancels"

        def user_agent():
            ua = self.js(
                """return Cc["@mozilla.org/network/protocol;1?name=http"]
                  .getService(Ci.nsIHttpProtocolHandler).userAgent;"""
            )
            # Exactly Firefox's: sites such as addons.mozilla.org look for the
            # Firefox token, and an extra one would make Evergreen stand out.
            assert re.search(r"Gecko/\d+ Firefox/[\d.]+$", ua), f"not Firefox's User-Agent: {ua}"
            assert "Evergreen" not in ua, f"the User-Agent names Evergreen: {ua}"
            return ua

        def empty_space():
            r = self.js(
                """
                let d = w.document;
                let box = d.getElementById("evergreen-search");
                let input = d.getElementById("evergreen-search-input");
                let spaceId = c.activeSpaceId;
                let inSpace = () => gB.tabs.filter(t => !t.pinned && !t.closing
                  && t.getAttribute("evergreen-space") == spaceId);
                let listed = () => inSpace().filter(t => w.getComputedStyle(t).display != "none");
                let blanks = () => gB.tabs.filter(t => !t.closing && t.hasAttribute("evergreen-blank")).length;
                let state = () => ({ sameSpace: c.activeSpaceId == spaceId, listed: listed().length, blanks: blanks(),
                  selectedBlank: gB.selectedTab.hasAttribute("evergreen-blank"), mode: box.getAttribute("mode"),
                  url: gB.selectedBrowser.currentURI.spec });
                // Close every tab in the Space: the start page shows, with no tab listed.
                for (let t of inSpace()) {
                  gB.removeTab(t, { animate: false });
                }
                await until(() => box.getAttribute("mode") == "start" && !box.hidden);
                await sleep(200);
                let closed = state();
                // Closing the start page (Ctrl+W) leaves it there.
                gB.removeTab(gB.selectedTab, { animate: false });
                await until(() => box.getAttribute("mode") == "start" && blanks() == 1, 3000)
                  .catch(() => { throw new Error("after Ctrl+W: " + JSON.stringify({ ...state(), tabs: tabList(),
                    all: gB.tabs.map(t => [t.label, t.linkedBrowser.currentURI.spec, t.hasAttribute("evergreen-blank"), t.closing, t.selected]) })); });
                await sleep(200);
                let closedAgain = state();
                // Ctrl+T over the start page, an address and Enter: it loads in the
                // start page's own tab, which joins the list (no tab is left behind).
                let startTab = gB.selectedTab, count = gB.tabs.length;
                d.getElementById("cmd_newNavigatorTab").doCommand();
                await sleep(100);
                input.value = "about:robots";
                input.dispatchEvent(new w.Event("input", { bubbles: true }));
                input.dispatchEvent(new w.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
                await until(() => startTab.linkedBrowser.currentURI.spec == "about:robots");
                await sleep(200);
                let opened = { ...state(), sameTab: gB.selectedTab == startTab, added: gB.tabs.length - count,
                  first: listed()[0] == startTab };
                return { closed, closedAgain, opened, windowOpen: !w.closed, tabs: tabList() };
                """
            )
            self.shot("empty-space")
            closed, again, opened = r["closed"], r["closedAgain"], r["opened"]
            assert r["windowOpen"], r
            assert closed["sameSpace"] and closed["mode"] == "start" and closed["selectedBlank"], f"no start page: {r}"
            assert closed["listed"] == 0 and closed["blanks"] == 1, f"the start page should not be a listed tab: {r}"
            assert again["sameSpace"] and again["mode"] == "start" and again["listed"] == 0 and again["blanks"] == 1, r
            assert opened["url"] == "about:robots" and opened["sameTab"] and opened["added"] == 0, r
            assert opened["listed"] == 1 and opened["first"] and opened["blanks"] == 0, r
            return "closing every tab shows the start page; what is opened from it becomes the Space's tab"

        def update_notice():
            r = self.js(
                """
                let [url] = arguments;
                let { EvergreenUpdate } = ChromeUtils.importESModule("@BASE@EvergreenUpdate.sys.mjs");
                let P = Services.prefs;
                P.setStringPref("evergreen.update.url", url);
                let out = {};
                // The dev harness has no version number: nothing to compare, so no request.
                P.setStringPref("evergreen.version", "");
                out.dev = (await EvergreenUpdate.check({ force: true })).state;
                P.setStringPref("evergreen.version", "0.1");
                P.setBoolPref("evergreen.update.check", false);
                out.disabled = (await EvergreenUpdate.check({ force: true })).state;
                P.setBoolPref("evergreen.update.check", true);
                let found = await EvergreenUpdate.check({ force: true });
                out.state = found.state;
                out.error = found.error;
                out.version = found.release?.version;
                // Where did the notice go? Look in every browser window, and say what is there if it is missing.
                let wins = [...Services.wm.getEnumerator("navigator:browser")];
                let find = () => {
                  for (let win of wins) {
                    let found = win.gNotificationBox?.getNotificationWithValue("evergreen-update");
                    if (found) { return found; }
                  }
                  return null;
                };
                let n;
                try { n = await until(find, 8000); } catch {
                  out.windows = wins.map(win => ({
                    popup: win.document.documentElement.hasAttribute("popup-window"),
                    private: win.document.documentElement.hasAttribute("privatebrowsingmode"),
                    notices: [...(win.gNotificationBox?.allNotifications ?? [])].map(x => x.getAttribute("value")),
                  }));
                  out.consoleErrors = evergreenConsoleErrors();
                  return out;
                }
                let text = () => (n.messageText?.textContent || n.shadowRoot?.textContent || "");
                await until(() => text().includes("9.9"));
                out.text = text().trim();
                out.buttons = [...(n.buttonContainer ?? n).querySelectorAll("button")].map(b => b.getAttribute("data-l10n-id"));
                out.platform = ChromeUtils.importESModule("resource://gre/modules/AppConstants.sys.mjs").AppConstants.platform;
                n.close();
                // Same or newer build than the latest release: nothing to announce.
                P.setStringPref("evergreen.version", "9.9");
                out.current = (await EvergreenUpdate.check({ force: true })).state;
                P.clearUserPref("evergreen.version");
                P.clearUserPref("evergreen.update.url");
                return out;
                """,
                f"http://127.0.0.1:{self.server.port}/releases.json",
            )
            assert "windows" not in r, f"the update notice never appeared: {r}"
            assert r["dev"] == "dev" and r["disabled"] == "disabled", r
            assert r["state"] == "available" and r["version"] == "9.9", r
            assert "9.9" in r["text"], r
            # Windows downloads and checks the installer in the browser; anywhere
            # else that is not a managed Linux install, the notice opens the release page.
            first = "evergreen-update-button" if r["platform"] == "win" else "evergreen-update-download"
            assert r["buttons"] == [first, "evergreen-update-later"], r
            assert r["current"] == "current", r
            return f"newer release announced: \"{r['text']}\""

        def windows_installer_download():
            r = self.js(
                """
                let [base] = arguments;
                let { EvergreenUpdate } = ChromeUtils.importESModule("@BASE@EvergreenUpdate.sys.mjs");
                let P = Services.prefs;
                let release = { version: "9.9", tag: "v9.9" };
                let out = {};
                P.setStringPref("evergreen.update.downloadBase", base);
                try {
                  let path = await EvergreenUpdate.downloadInstaller(release);
                  out.ok = await IOUtils.exists(path);
                  out.size = (await IOUtils.stat(path)).size;
                  await IOUtils.remove(path);
                } catch (e) { out.okError = String(e.message); }
                // Not https and not this computer: refused before any request.
                P.setStringPref("evergreen.update.downloadBase", "http://example.com/");
                try { await EvergreenUpdate.downloadInstaller(release); out.insecure = "accepted"; }
                catch (e) { out.insecure = String(e.message); }
                P.clearUserPref("evergreen.update.downloadBase");
                return out;
                """,
                f"http://127.0.0.1:{self.server.port}/",
            )
            assert r.get("ok") and r["size"] == len(b"MZ fake installer " * 1000), r
            assert "trusted" in r["insecure"], r
            PageServer.corrupt = True
            try:
                bad = self.js(
                    """
                    let [base] = arguments;
                    let { EvergreenUpdate } = ChromeUtils.importESModule("@BASE@EvergreenUpdate.sys.mjs");
                    Services.prefs.setStringPref("evergreen.update.downloadBase", base);
                    let out = {};
                    try { out.path = await EvergreenUpdate.downloadInstaller({ version: "9.9", tag: "v9.9" }); }
                    catch (e) { out.error = String(e.message); }
                    Services.prefs.clearUserPref("evergreen.update.downloadBase");
                    out.left = await IOUtils.exists(PathUtils.join(PathUtils.tempDir, "evergreen-update", "Evergreen-9.9-win64-setup.exe"));
                    return out;
                    """,
                    f"http://127.0.0.1:{self.server.port}/",
                )
            finally:
                PageServer.corrupt = False
            assert "checksum" in bad.get("error", "") and not bad["left"] and "path" not in bad, bad
            return "installer downloaded and checked; a bad checksum or an insecure address is refused"

        def profile_adoption():
            r = self.js(
                """
                let { EvergreenProfile } = ChromeUtils.importESModule("@BASE@EvergreenProfile.sys.mjs");
                let { DATA_FILES } = ChromeUtils.importESModule("@BASE@ProfileAdoption.sys.mjs");
                let fake = (name, root) => ({ name, rootDir: { path: root }, QueryInterface() { return this; } });
                let service = list => ({ profiles: list });
                let cur = PathUtils.profileDir;
                let measured = (await EvergreenProfile.describeProfiles(service([fake("current", cur)])))[0].bytes;
                let base = PathUtils.join(PathUtils.tempDir, "evergreen-smoke-profiles");
                await IOUtils.remove(base, { recursive: true, ignoreAbsent: true });
                let make = async (name, bytes, compat) => {
                  let dir = PathUtils.join(base, name);
                  await IOUtils.makeDirectory(dir, { createAncestors: true });
                  await IOUtils.write(PathUtils.join(dir, "places.sqlite"), new Uint8Array(bytes));
                  if (compat) {
                    await IOUtils.writeUTF8(PathUtils.join(dir, "compatibility.ini"), `[Compatibility]\nLastVersion=${compat}_20260101000000/20260101000000\n`);
                  }
                  return dir;
                };
                let old = await make("old.default", measured + 4 * 1024 * 1024, "157.0");
                let newer = await make("newer.default", measured + 8 * 1024 * 1024, "999.0");
                let out = {};
                let pick = async list => (await EvergreenProfile.candidate(service(list)))?.name ?? null;
                // The bigger earlier profile is offered; the one a newer build used is not.
                out.picked = await pick([fake("current", cur), fake("old", old), fake("newer", newer)]);
                out.onlyNewer = await pick([fake("current", cur), fake("newer", newer)]);
                // Nothing bigger than the profile in use: nothing is offered.
                out.none = await pick([fake("current", cur)]);
                await IOUtils.remove(base, { recursive: true, ignoreAbsent: true });
                return out;
                """
            )
            assert r["picked"] == "old", r
            assert r["onlyNewer"] is None and r["none"] is None, r
            return "offers the bigger earlier profile; skips one a newer build used"

        def accelerated_downloads():
            dl_dir = str(egdev.dev_root() / "smoke-downloads")
            shutil.rmtree(dl_dir, ignore_errors=True)
            os.makedirs(dl_dir)
            fetch = """
              let [url, dir, enabled, pauseAt] = arguments;
              let { Downloads } = ChromeUtils.importESModule("resource://gre/modules/Downloads.sys.mjs");
              let { EvergreenDownloads } = ChromeUtils.importESModule("@BASE@EvergreenDownloads.sys.mjs");
              let P = Services.prefs;
              P.setIntPref("browser.download.folderList", 2);
              P.setStringPref("browser.download.dir", dir);
              P.setBoolPref("browser.download.useDownloadDir", true);
              P.setBoolPref("browser.download.always_ask_before_handling_new_types", false);
              P.setBoolPref("evergreen.downloads.enabled", enabled);
              P.setBoolPref("evergreen.downloads.log", true);
              let list = await Downloads.getList(Downloads.ALL);
              for (let d of await list.getAll()) { await list.remove(d); }
              EvergreenDownloads.events.length = 0;
              // saveURL(url, originalUrl, fileName, pickerTitleKey, bypassCache, skipPrompt, referrerInfo,
              //         cookieJarSettings, sourceDocument, isPrivate, principal), as Firefox 160 has it.
              w.saveURL(url, null, null, null, true, true, null, null, null, false,
                        Services.scriptSecurityManager.getSystemPrincipal());
              let dl;
              try {
                dl = await until(async () => (await list.getAll()).find(d => d.source.url == url), 20000);
              } catch {
                let all = await list.getAll();
                throw new Error("no download appeared for " + url + "; list: " +
                  JSON.stringify(all.map(d => [d.source.url, d.error?.message, d.succeeded])) +
                  "; windows: " + [...Services.wm.getEnumerator(null)].map(x => x.location.href).join(" "));
              }
              let out = { paused: false };
              if (pauseAt) {
                await until(() => dl.currentBytes >= pauseAt || dl.succeeded, 30000);
                await dl.cancel();
                out.paused = !dl.succeeded;
                out.bytesAtPause = dl.currentBytes;
                out.partial = dl.hasPartialData;
                await sleep(500);
                dl.start().catch(() => {});
              }
              await until(() => dl.succeeded || dl.error, 90000);
              out.succeeded = dl.succeeded;
              out.error = dl.error ? String(dl.error.message || dl.error) : null;
              out.path = dl.target.path;
              out.partLeft = dl.target.partFilePath ? await IOUtils.exists(dl.target.partFilePath) : false;
              out.sha = dl.succeeded ? await IOUtils.computeHexDigest(dl.target.path, "sha256") : null;
              out.size = dl.succeeded ? (await IOUtils.stat(dl.target.path)).size : null;
              out.events = EvergreenDownloads.events.map(e => e.action + (e.reason ? ":" + e.reason : ""));
              P.clearUserPref("evergreen.downloads.enabled");
              return out;
            """

            def get(path, enabled=True, pause_at=0):
                self.files.reset()
                r = self.js(fetch, self.files.url(path), dl_dir, enabled, pause_at)
                r["server"] = self.files.stats[path]
                return r

            def same_file(path, r):
                assert r["succeeded"], (path, r)
                assert r["sha"] == self.files.sha[path], f"{path}: wrong content, {r}"
                assert r["size"] == self.files.FILES[path]["size"], (path, r)
                assert not r["partLeft"], (path, r)

            out = []
            # 1. A big file is taken over and arrives on several connections.
            r = get("/big.bin")
            same_file("/big.bin", r)
            assert "segmented" in r["events"], r
            assert r["server"]["peak"] >= 3, f"expected several connections at once: {r}"
            out.append(f"big.bin on {r['server']['peak']} connections")

            # 2. Connections cut short are retried from where they stopped.
            r = get("/flaky.bin")
            same_file("/flaky.bin", r)
            assert r["server"]["cut"] >= 1, r
            out.append(f"flaky.bin survived {r['server']['cut']} cut connections")

            # 3. Pause, then resume: the rest is fetched, not everything again.
            r = get("/pause.bin", pause_at=4 * 1024 * 1024)
            same_file("/pause.bin", r)
            assert r["paused"] and r["partial"], r
            assert "resume" in r["events"], r
            out.append("pause and resume")

            # 4. Not splittable: left to Firefox, still arrives intact.
            for path, why in (("/norange.bin", "no byte ranges"), ("/small.bin", "small or unknown size")):
                r = get(path)
                same_file(path, r)
                assert f"left:{why}" in r["events"], (path, r)
                assert not r["server"]["ranged"], (path, r)
            out.append("left alone when it cannot be split")

            # 5. A server that lies about ranges: Firefox downloads it instead.
            r = get("/lie.bin")
            same_file("/lie.bin", r)
            assert any(e.startswith("fallback:") for e in r["events"]), r
            out.append("falls back when the server ignores ranges")

            # 6. Switched off: Firefox's own downloader, no range requests.
            r = get("/big.bin", enabled=False)
            same_file("/big.bin", r)
            assert not r["server"]["ranged"] and "segmented" not in r["events"], r
            out.append("off switch")

            # 7. A limit across all downloads: with 2 connections in total a
            # file that wants 6 still arrives, on at most 2 at once.
            self.js("Services.prefs.setIntPref('evergreen.downloads.maxConnections', 2);")
            try:
                r = get("/big.bin")
            finally:
                self.js("Services.prefs.clearUserPref('evergreen.downloads.maxConnections');")
            same_file("/big.bin", r)
            assert 1 <= r["server"]["peak"] <= 2, f"the global limit of 2 connections was not kept: {r}"
            out.append(f"global limit kept (peak {r['server']['peak']})")

            # 8. A queue: with one place, the second download waits for the first.
            self.files.reset()
            r = self.js(
                """
                let [urlA, urlB, dir] = arguments;
                let { Downloads } = ChromeUtils.importESModule("resource://gre/modules/Downloads.sys.mjs");
                let { EvergreenDownloads } = ChromeUtils.importESModule("@BASE@EvergreenDownloads.sys.mjs");
                let P = Services.prefs;
                P.setIntPref("browser.download.folderList", 2);
                P.setStringPref("browser.download.dir", dir);
                P.setBoolPref("browser.download.useDownloadDir", true);
                P.setBoolPref("evergreen.downloads.enabled", true);
                P.setIntPref("evergreen.downloads.maxActive", 1);
                let list = await Downloads.getList(Downloads.ALL);
                for (let d of await list.getAll()) { await list.remove(d); }
                EvergreenDownloads.events.length = 0;
                let save = url => w.saveURL(url, null, null, null, true, true, null, null, null, false,
                  Services.scriptSecurityManager.getSystemPrincipal());
                let find = url => until(async () => (await list.getAll()).find(d => d.source.url == url), 20000);
                save(urlA);
                let a = await find(urlA);
                await until(() => EvergreenDownloads.events.some(e => e.action == "segmented"), 20000);
                await until(() => a.currentBytes > 1024 * 1024, 30000);
                save(urlB);
                let b = await find(urlB);
                await sleep(3000); // plenty of time to start, if it were allowed to
                let whileFirstRuns = {
                  firstDone: a.succeeded, secondBytes: b.currentBytes, secondDone: b.succeeded,
                  segmented: EvergreenDownloads.events.filter(e => e.action == "segmented").length,
                  waiting: EvergreenDownloads.places.waiting,
                };
                await until(() => (a.succeeded || a.error) && (b.succeeded || b.error), 120000);
                let out = { whileFirstRuns, a: a.succeeded, b: b.succeeded,
                  shaA: a.succeeded ? await IOUtils.computeHexDigest(a.target.path, "sha256") : null,
                  shaB: b.succeeded ? await IOUtils.computeHexDigest(b.target.path, "sha256") : null,
                  segmented: EvergreenDownloads.events.filter(e => e.action == "segmented").length };
                P.clearUserPref("evergreen.downloads.maxActive");
                return out;
                """,
                self.files.url("/pause.bin"), self.files.url("/big.bin"), dl_dir,
            )
            wf = r["whileFirstRuns"]
            assert not wf["firstDone"] and not wf["secondDone"] and wf["segmented"] == 1 and wf["waiting"] == 1, (
                f"the second download should wait for a place: {r}"
            )
            assert r["a"] and r["b"] and r["segmented"] == 2, r
            assert r["shaA"] == self.files.sha["/pause.bin"] and r["shaB"] == self.files.sha["/big.bin"], r
            out.append("a second download waits for a place, then runs")

            # 9. A download made by an extension or a Firefox feature (a plain
            # "copy" download that was never a navigation) is taken over too.
            self.files.reset()
            r = self.js(
                """
                let [url, dir] = arguments;
                let { Downloads } = ChromeUtils.importESModule("resource://gre/modules/Downloads.sys.mjs");
                let { EvergreenDownloads } = ChromeUtils.importESModule("@BASE@EvergreenDownloads.sys.mjs");
                Services.prefs.setBoolPref("evergreen.downloads.enabled", true);
                let list = await Downloads.getList(Downloads.ALL);
                for (let d of await list.getAll()) { await list.remove(d); }
                EvergreenDownloads.events.length = 0;
                let dl = await Downloads.createDownload({
                  source: { url, isPrivate: false },
                  target: { path: PathUtils.join(dir, "from-extension.bin") },
                });
                await list.add(dl);
                dl.start().catch(() => {});
                await until(() => dl.succeeded || dl.error, 90000);
                return { ok: dl.succeeded, error: dl.error?.message ?? null,
                  sha: dl.succeeded ? await IOUtils.computeHexDigest(dl.target.path, "sha256") : null,
                  events: EvergreenDownloads.events.map(e => e.action + (e.reason ? ":" + e.reason : "")) };
                """,
                self.files.url("/big.bin"), dl_dir,
            )
            assert r["ok"] and r["sha"] == self.files.sha["/big.bin"], r
            assert "adopted" in r["events"] and "segmented" in r["events"], r
            assert self.files.stats["/big.bin"]["peak"] >= 3, f"expected several connections: {self.files.stats['/big.bin']}"
            out.append("a download made by an extension is taken over too")
            return "; ".join(out)


        def console_errors():
            r = self.errors_before_restart + self.js("return evergreenConsoleErrors();")
            assert not r, "\n  " + "\n  ".join(r)

        self.check("Evergreen loads into the window", loads)
        self.check("window layout", layout)
        self.check("default prefs from prefs/evergreen.js", prefs)
        self.check("identifies to websites exactly as Firefox", user_agent)
        self.check("first run: ETP Strict and Ecosia", first_run)
        self.check("first run: offer to import from another browser", import_offer)
        self.check("Spaces with separate sign-ins (containers)", spaces_and_containers)
        self.check("new-tab command opens the search box", new_tab_command)
        self.check("command bar: Space switching, moving a tab, open tabs, split view", command_bar)
        self.check("link routing: a site opens in its Space", link_routing)
        self.check("Peek: links from a Favorite open in a mini window", peek_and_mini_window)
        self.check("new tabs open at the top", new_tab_at_top)
        self.check("start page: Evergreen and a search box", start_page)
        self.check("switching Spaces hides/shows tabs", switching)
        self.check("keyboard shortcut switches Space", keyboard_shortcut)
        self.check("moving a tab across sign-in identities", move_tab)
        self.check("auto-archive of idle tabs", archive)
        self.check("archive panel lists and reopens", archive_panel)
        self.check("history clearing wipes the archive", purge)
        self.check("Space editor creates a Space", editor)
        self.check("Spaces and tab assignment survive a restart", persistence)
        self.check("deleting Spaces", delete_space)
        self.check("private windows stay plain", private_window)
        self.check("toolbar: tree menu, sidebar, Downloads, then navigation at the page edge", toolbar)
        self.check("toolbar about 20% thinner, sidebar about two thirds as wide", sizes)
        self.check("address bar: a compact centred pill, empty toolbar to drag", address_bar)
        self.check("Favorites: big tiles, two to a row", favorites)
        self.check("popup windows: slim header, rounded frame, centred over the opener", popup)
        self.check("sidebar collapses and the left edge reveals it", sidebar_collapse)
        self.check("sidebar keyboard shortcut", sidebar_shortcut)
        self.check("rename a Space by clicking its name", rename)
        self.check("an empty Space shows the start page, with no tab", empty_space)
        self.check("newer release: a notice with an update button", update_notice)
        self.check("Windows update: installer downloaded and checked against the checksums", windows_installer_download)
        self.check("new installation: offers the earlier profile with more data", profile_adoption)
        self.check("accelerated downloads: several connections, retries, resume, fallbacks", accelerated_downloads)
        self.check("no Evergreen errors in the console", console_errors)
        self.quit()
        print(f"\n{len(self.failures)} failed" if self.failures else "\nAll checks passed")
        return 1 if self.failures else 0


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--firefox", help="Firefox install directory for the dev harness (default: standard location)")
    p.add_argument("--binary", help="test a built Evergreen instead (path to evergreen.exe / evergreen)")
    p.add_argument("--headless", action="store_true", help="run Firefox with -headless")
    p.add_argument("--screenshots", help="directory to save screenshots in")
    p.add_argument("--log-screenshots", action="store_true",
                   help="also print each screenshot to the log as a small JPEG data URL")
    args = p.parse_args()
    run = Run(args)
    try:
        return run.run()
    finally:
        run.quit()


if __name__ == "__main__":
    sys.exit(main())
