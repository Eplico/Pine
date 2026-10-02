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
import http.server
import os
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
  return [...scriptErrors, ...apiErrors].filter(s => /evergreen/i.test(s));
};
"""


class PageServer:
    """Serves /a.html, /b.html, ... with predictable titles on 127.0.0.1."""

    def __init__(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
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


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Run:
    def __init__(self, args):
        self.args = args
        self.failures: list[str] = []
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
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        self.server = PageServer()
        self.proc = None
        self.m = None

    # --- process control ---------------------------------------------------------

    def launch(self):
        cmd = [str(self.binary), "-profile", str(self.profile), "-no-remote",
               "--marionette", "-remote-allow-system-access"]
        env = dict(os.environ, MOZ_CRASHREPORTER_DISABLE="1")
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
        self.js(
            """
            await until(() => w && w.document.documentElement.hasAttribute("evergreen"), 30000);
            await until(() => Services.wm.getMostRecentWindow("navigator:browser").gBrowserInit?.delayedStartupFinished, 30000);
            """
        )

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

        def prefs():
            expected = {p.name: p.value for p in load_prefs(PREFS_FILE)}
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
                await until(() => Services.prefs.getBoolPref("evergreen.defaults.searchEngineApplied", false), 20000)
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
                  google: !!search.getEngineByName("Google"),
                  ddg: !!search.getEngineByName("DuckDuckGo"),
                };
                """
            )
            assert r["etp"] == "strict" and r["trackingProtection"], r
            assert r["engine"] == "Ecosia", r
            assert r["google"] and r["ddg"], f"Google and DuckDuckGo should stay available: {r}"
            return r

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
                let opened = nextTabOpen();
                w.BrowserCommands.openTab();
                let t = await opened;
                let ctx = t.userContextId, sp = t.getAttribute("evergreen-space");
                gB.removeTab(t);
                return { ctx, sp, workCtx: space("Work").userContextId, work: space("Work").id };
                """
            )
            if r.get("skipped"):
                return "BrowserCommands.openTab not present; skipped"
            assert r["ctx"] == r["workCtx"] and r["sp"] == r["work"], r
            return "Ctrl+T opens in the Space's container"

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
            self.quit()
            self.launch()
            after = self.js(
                """
                await until(() => tabByTitle("Page A") && tabByTitle("Page B"), 20000);
                return { spaces: SpacesStore.state.spaces.map(s => s.name || c.defaultName),
                  active: c.activeSpaceId,
                  tabs: gB.tabs.filter(t => !t.pinned).map(t => [t.label, t.getAttribute("evergreen-space")]).sort(),
                  workHidden: gB.tabs.filter(t => t.getAttribute("evergreen-space") == space("Work").id).every(t => t.hidden),
                  kept: tabByTitle("Page A").hasAttribute("evergreen-keep") };
                """
            )
            assert after["spaces"] == before["spaces"], (before, after)
            assert after["active"] == before["active"], (before, after)
            # Restored tabs keep their Space (blank new-tab pages may differ in label).
            want = [t for t in before["tabs"] if t[0].startswith("Page")]
            got = [t for t in after["tabs"] if t[0].startswith("Page")]
            assert got == want, (want, got)
            assert after["workHidden"] and after["kept"], after
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

        def console_errors():
            r = self.js("return evergreenConsoleErrors();")
            assert not r, "\n  " + "\n  ".join(r)

        self.check("Evergreen loads into the window", loads)
        self.check("default prefs from prefs/evergreen.js", prefs)
        self.check("first run: ETP Strict and Ecosia", first_run)
        self.check("Spaces with separate sign-ins (containers)", spaces_and_containers)
        self.check("new-tab command uses the Space's container", new_tab_command)
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
    args = p.parse_args()
    run = Run(args)
    try:
        return run.run()
    finally:
        run.quit()


if __name__ == "__main__":
    sys.exit(main())
