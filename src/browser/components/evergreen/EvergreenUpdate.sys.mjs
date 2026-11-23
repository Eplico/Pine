/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

/**
 * Tells you when a newer Evergreen is out, and on Linux installs made by
 * evergreen-install, downloads it and restarts into it.
 *
 * - Once a day (and a little after start-up) it asks GitHub's public release
 *   list which Evergreen is newest for this platform. No account, cookies or
 *   identifiers are sent. Turn it off with evergreen.update.check = false.
 * - A newer release shows a bar in the window. On a Linux install managed by
 *   evergreen-install, "Update" downloads the release (checked against its
 *   SHA256SUMS.txt), then "Restart" switches over; the profile is untouched.
 *   Elsewhere, the bar opens the release page.
 *
 * Everything that decides *whether* there is an update lives in
 * UpdateCheck.sys.mjs (unit-tested); this file is the fetching and the UI.
 */

import { importBrowserModule } from "./FirefoxModules.sys.mjs";
import { isUpdate, newestRelease } from "./UpdateCheck.sys.mjs";

const PREF_ENABLED = "evergreen.update.check";
const PREF_URL = "evergreen.update.url";
const PREF_VERSION = "evergreen.version";
const PREF_LAST = "evergreen.update.lastCheck";

const FIRST_CHECK_MS = 30 * 1000;
const CHECK_EVERY_MS = 12 * 60 * 60 * 1000;
const MIN_GAP_S = 6 * 60 * 60;
const FETCH_TIMEOUT_MS = 20 * 1000;
const NOTIFICATION = "evergreen-update";

const lazy = {};
ChromeUtils.defineESModuleGetters(lazy, {
  AppConstants: "resource://gre/modules/AppConstants.sys.mjs",
  Subprocess: "resource://gre/modules/Subprocess.sys.mjs",
});

async function readAll(pipe) {
  let text = "";
  for (;;) {
    let chunk = await pipe.readString();
    if (!chunk) {
      return text;
    }
    text += chunk;
  }
}

export const EvergreenUpdate = {
  _first: null,
  _timer: null,
  /** The release a notice was last shown for, so a window opened later still gets it. */
  _shownFor: null,

  /** Called when the first window is ready. */
  init() {
    if (!this.currentVersion()) {
      return; // dev harness: no version number, so nothing to compare
    }
    let run = () => this.check().catch(e => console.error("Evergreen update check", e));
    this._first = Cc["@mozilla.org/timer;1"].createInstance(Ci.nsITimer);
    this._first.initWithCallback({ notify: run }, FIRST_CHECK_MS, Ci.nsITimer.TYPE_ONE_SHOT);
    this._timer = Cc["@mozilla.org/timer;1"].createInstance(Ci.nsITimer);
    this._timer.initWithCallback({ notify: run }, CHECK_EVERY_MS, Ci.nsITimer.TYPE_REPEATING_SLACK);
  },

  quit() {
    this._first?.cancel();
    this._timer?.cancel();
    this._first = this._timer = null;
  },

  /** This build's Evergreen version (set at build time), or "" in the dev harness. */
  currentVersion() {
    let v = Services.prefs.getStringPref(PREF_VERSION, "");
    return /^[0-9]+(\.[0-9]+)*$/.test(v) ? v : "";
  },

  /**
   * Look for a newer release and, if there is one, show the notice.
   *
   * @param {object} [options]
   * @param {boolean} [options.force]  Ignore the minimum gap since the last check.
   * @returns {Promise<{state: string, release?: object}>}
   *   state: "disabled" | "dev" | "recent" | "current" | "available" | "error"
   */
  async check({ force = false } = {}) {
    if (!Services.prefs.getBoolPref(PREF_ENABLED, true)) {
      return { state: "disabled" };
    }
    let current = this.currentVersion();
    if (!current) {
      return { state: "dev" };
    }
    let now = Math.floor(Date.now() / 1000);
    if (!force && now - Services.prefs.getIntPref(PREF_LAST, 0) < MIN_GAP_S) {
      return this._shownFor ? { state: "available", release: this._shownFor } : { state: "recent" };
    }
    let release;
    try {
      release = await this._latest();
    } catch (e) {
      console.warn("Evergreen: could not check for updates:", e.message);
      return { state: "error" };
    }
    Services.prefs.setIntPref(PREF_LAST, now);
    if (!isUpdate(current, release)) {
      this._shownFor = null;
      return { state: "current" };
    }
    this._shownFor = release;
    await this.showNotice(release);
    return { state: "available", release };
  },

  async _latest() {
    let response = await fetch(Services.prefs.getStringPref(PREF_URL), {
      credentials: "omit",
      cache: "no-store",
      headers: { Accept: "application/vnd.github+json" },
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    return newestRelease(await response.json(), lazy.AppConstants.platform);
  },

  // --- the notice ----------------------------------------------------------

  /** The window to show the notice in: the top normal (not popup, not private) browser window. */
  _window() {
    try {
      let { BrowserWindowTracker } = importBrowserModule("browser/modules", "BrowserWindowTracker");
      let top = BrowserWindowTracker.getTopWindow({ allowPopups: false });
      if (top && !top.closed) {
        return top;
      }
    } catch (e) {
      console.error("Evergreen update: no window tracker", e);
    }
    for (let win of Services.wm.getEnumerator("navigator:browser")) {
      let root = win.document.documentElement;
      if (!win.closed && !root.hasAttribute("privatebrowsingmode") && !root.hasAttribute("popup-window")) {
        return win;
      }
    }
    return null;
  },

  _box(win) {
    return win.gNotificationBox ?? win.gBrowser.getNotificationBox();
  },

  async showNotice(release) {
    let win = this._window();
    if (!win) {
      return;
    }
    let box = this._box(win);
    box.getNotificationWithValue(NOTIFICATION)?.close();
    let managed = await this.managedInstaller();
    let version = release.version;
    if (managed && (await this._stagedVersion(managed)) == version) {
      return this._showReady(win, release, managed);
    }
    let buttons = [
      managed
        ? {
            "l10n-id": "evergreen-update-button",
            callback: () => {
              this._download(win, release, managed);
              return true;
            },
          }
        : {
            "l10n-id": "evergreen-update-download",
            callback: () => {
              this._openPage(win, release);
              return false;
            },
          },
      { "l10n-id": "evergreen-update-later", callback: () => false },
    ];
    await box.appendNotification(
      NOTIFICATION,
      {
        label: { "l10n-id": "evergreen-update-available", "l10n-args": { version } },
        priority: box.PRIORITY_INFO_MEDIUM,
      },
      buttons
    );
  },

  async _showReady(win, release, installer) {
    let box = this._box(win);
    box.getNotificationWithValue(NOTIFICATION)?.close();
    await box.appendNotification(
      NOTIFICATION,
      {
        label: { "l10n-id": "evergreen-update-ready", "l10n-args": { version: release.version } },
        priority: box.PRIORITY_INFO_HIGH,
      },
      [
        {
          "l10n-id": "evergreen-update-restart",
          callback: () => {
            this._restartIntoUpdate(win, release, installer);
            return true;
          },
        },
        { "l10n-id": "evergreen-update-later", callback: () => false },
      ]
    );
  },

  async _showFailure(win, release, error) {
    let box = this._box(win);
    box.getNotificationWithValue(NOTIFICATION)?.close();
    await box.appendNotification(
      NOTIFICATION,
      {
        label: { "l10n-id": "evergreen-update-failed", "l10n-args": { error: String(error).slice(0, 200) } },
        priority: box.PRIORITY_WARNING_MEDIUM,
      },
      [
        {
          "l10n-id": "evergreen-update-download",
          callback: () => {
            this._openPage(win, release);
            return false;
          },
        },
      ]
    );
  },

  _openPage(win, release) {
    if (release.pageUrl) {
      win.openTrustedLinkIn(release.pageUrl, "tab");
    }
  },

  // --- Linux: evergreen-install --------------------------------------------

  /**
   * The path of evergreen-install if this Evergreen is the one it installed
   * (<root>/app/ next to <root>/evergreen-install), else null.
   */
  async managedInstaller() {
    if (lazy.AppConstants.platform != "linux") {
      return null;
    }
    try {
      let appDir = Services.dirsvc.get("GreD", Ci.nsIFile).path;
      if (PathUtils.filename(appDir) != "app") {
        return null;
      }
      let tool = PathUtils.join(PathUtils.parent(appDir), "evergreen-install");
      return (await IOUtils.exists(tool)) ? tool : null;
    } catch {
      return null;
    }
  },

  async _stagedVersion(installer) {
    try {
      return (await IOUtils.readUTF8(PathUtils.join(PathUtils.parent(installer), "staged-version"))).trim();
    } catch {
      return null;
    }
  },

  /** Run evergreen-install; resolves to its parsed --json output. */
  async runInstaller(installer, args) {
    let proc = await lazy.Subprocess.call({
      command: installer,
      arguments: [...args, "--json", "--quiet"],
      stdout: "pipe",
      stderr: "pipe",
      environmentAppend: true,
    });
    let [out, err, { exitCode }] = await Promise.all([readAll(proc.stdout), readAll(proc.stderr), proc.wait()]);
    if (exitCode != 0 && exitCode != 10) {
      throw new Error((err || out || `exit code ${exitCode}`).trim().split("\n").pop());
    }
    try {
      return JSON.parse(out);
    } catch {
      return {};
    }
  },

  async _download(win, release, installer) {
    let box = this._box(win);
    box.getNotificationWithValue(NOTIFICATION)?.close();
    await box.appendNotification(
      NOTIFICATION,
      {
        label: { "l10n-id": "evergreen-update-working", "l10n-args": { version: release.version } },
        priority: box.PRIORITY_INFO_MEDIUM,
      },
      []
    );
    try {
      await this.runInstaller(installer, ["stage", "--version", release.version]);
      await this._showReady(win, release, installer);
    } catch (e) {
      console.error("Evergreen update", e);
      await this._showFailure(win, release, e.message);
    }
  },

  /**
   * Quit, and have the launcher start Evergreen again once this process has
   * gone. The launcher switches to the downloaded release while nothing is
   * running (Firefox's own restart would start the old program again, and
   * swapping folders under a running browser is what to avoid). The tabs
   * come back through session restore.
   */
  async _restartIntoUpdate(win, release, installer) {
    let launcher = PathUtils.join(PathUtils.parent(installer), "evergreen");
    let cancel = Cc["@mozilla.org/supports-PRBool;1"].createInstance(Ci.nsISupportsPRBool);
    Services.obs.notifyObservers(cancel, "quit-application-requested", "restart");
    if (cancel.data) {
      return; // the user cancelled the quit (for example at "close all tabs?")
    }
    try {
      // $1: this process; $2: the launcher. Gives up after about two minutes.
      await lazy.Subprocess.call({
        command: "/bin/sh",
        arguments: [
          "-c",
          'n=0; ( while kill -0 "$1" 2>/dev/null && [ $n -lt 400 ]; do sleep 0.3; n=$((n+1)); done;' +
            ' [ $n -lt 400 ] && exec "$2" ) >/dev/null 2>&1 &',
          "sh",
          String(Services.appinfo.processID),
          launcher,
        ],
        environmentAppend: true,
        environment: { EVERGREEN_UPDATE_WAIT: "1" },
      });
    } catch (e) {
      console.error("Evergreen update", e);
      await this._showFailure(win, release, e.message);
      return;
    }
    Services.startup.quit(Ci.nsIAppStartup.eAttemptQuit);
  },
};
