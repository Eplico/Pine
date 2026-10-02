/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

/**
 * App-wide Evergreen startup: first-run defaults, the archive timer, and
 * wiring the archive to Firefox's history-clearing paths.
 *
 * Registered in Evergreen.manifest (browser-first-window-ready and
 * browser-quit-application-granted). The dev harness calls the same methods.
 */

import { ArchiveStore } from "./ArchiveStore.sys.mjs";
import { applyDefaultSearch } from "./SearchDefaults.sys.mjs";
import { SpacesStore } from "./SpacesStore.sys.mjs";
import { EvergreenWindow } from "./EvergreenWindow.sys.mjs";

// Each first-run step records that it ran, so a step that fails (for example
// search initialisation) is retried next start, and a choice the user makes
// afterwards is never overridden.
const STEP_PREFS = {
  etp: "evergreen.defaults.strictTrackingProtectionApplied",
  search: "evergreen.defaults.searchEngineApplied",
};

const ARCHIVE_CHECK_INTERVAL_MS = 10 * 60 * 1000;

/**
 * The search service and its "unknown" change reason. Recent Firefox
 * releases (156 and 157 at least) replaced the XPCOM service
 * (Services.search) with an ES module; support both so the dev harness also
 * runs on older installed Firefox versions.
 */
export function getSearchService() {
  if (Services.search) {
    return {
      service: Services.search,
      changeReason: Ci.nsISearchService.CHANGE_REASON_UNKNOWN,
    };
  }
  let { SearchService } = ChromeUtils.importESModule(
    "moz-src:///toolkit/components/search/SearchService.sys.mjs"
  );
  return { service: SearchService, changeReason: SearchService.CHANGE_REASON.UNKNOWN };
}

const PURGE_ALL = "browser:purge-session-history";
const PURGE_DOMAIN = "browser:purge-session-history-for-domain";

export const EvergreenStartup = {
  _initialized: false,
  _timer: null,

  firstWindowReady() {
    if (this._initialized) {
      return;
    }
    this._initialized = true;
    Services.obs.addObserver(this, PURGE_ALL);
    Services.obs.addObserver(this, PURGE_DOMAIN);
    try {
      IOUtils.profileBeforeChange.addBlocker("Evergreen: saving Spaces and Archive", () =>
        Promise.all([SpacesStore.flush(), ArchiveStore.flush()])
      );
    } catch (e) {
      console.error(e);
    }
    this._applyFirstRunDefaults().catch(e => console.error("Evergreen first run", e));
    ArchiveStore.ready().then(() => this._startArchiveTimer());
  },

  quit() {
    this._timer?.cancel();
    this._timer = null;
  },

  async _applyFirstRunDefaults() {
    if (!Services.prefs.getBoolPref(STEP_PREFS.etp, false)) {
      // Firefox applies a tracking-protection category only when it is a user
      // value, so it cannot be a default pref. Firefox's ContentBlockingPrefs
      // also starts with the first window and re-derives the category from the
      // individual prefs ("standard" on a new profile), so wait until startup
      // work is done, then switch. "custom" means the user (or an imported
      // profile) chose settings by hand: leave those alone.
      await new Promise(resolve => ChromeUtils.idleDispatch(resolve));
      const CATEGORY = "browser.contentblocking.category";
      if (Services.prefs.getStringPref(CATEGORY, "standard") == "standard") {
        Services.prefs.setStringPref(CATEGORY, "strict");
      }
      Services.prefs.setBoolPref(STEP_PREFS.etp, true);
    }
    if (!Services.prefs.getBoolPref(STEP_PREFS.search, false)) {
      let { service, changeReason } = getSearchService();
      await service.init();
      await applyDefaultSearch(service, changeReason);
      Services.prefs.setBoolPref(STEP_PREFS.search, true);
    }
  },

  _startArchiveTimer() {
    // The first check runs one interval after startup, so session restore has
    // finished and the user has seen their tabs before any are archived.
    this._timer = Cc["@mozilla.org/timer;1"].createInstance(Ci.nsITimer);
    this._timer.initWithCallback(
      () => EvergreenWindow.archiveIdleTabsEverywhere(),
      ARCHIVE_CHECK_INTERVAL_MS,
      Ci.nsITimer.TYPE_REPEATING_SLACK
    );
  },

  observe(subject, topic, data) {
    if (topic == PURGE_ALL) {
      ArchiveStore.ready().then(() => ArchiveStore.clear());
    } else if (topic == PURGE_DOMAIN && data) {
      ArchiveStore.ready().then(() => ArchiveStore.removeForDomain(data));
    }
  },

  QueryInterface: ChromeUtils.generateQI(["nsIObserver"]),
};
