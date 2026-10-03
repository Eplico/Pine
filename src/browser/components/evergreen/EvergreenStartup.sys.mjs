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
import { applyDefaultSearch, getSearchService, reconcileDefaultSearch } from "./SearchDefaults.sys.mjs";
import { SpacesStore } from "./SpacesStore.sys.mjs";
import { EvergreenWindow } from "./EvergreenWindow.sys.mjs";

export { getSearchService };

// Each first-run step records that it ran, so a step that fails (for example
// search initialisation) is retried next start, and a choice the user makes
// afterwards is never overridden.
const STEP_PREFS = {
  etp: "evergreen.defaults.strictTrackingProtectionApplied",
  // "2": profiles from the first preview could lose the Ecosia default when
  // Firefox learned the region (see reconcileDefaultSearch); apply it again.
  search: "evergreen.defaults.searchEngineApplied2",
  importOffer: "evergreen.defaults.importOffered",
};
// Off in automated tests, where an import window would get in the way.
const IMPORT_PROMPT_PREF = "evergreen.firstrun.importPrompt";

// The id of the engine Evergreen made the default, while it manages it.
const MANAGED_SEARCH_PREF = "evergreen.search.managedDefaultEngineId";
const managedSearch = {
  get: () => Services.prefs.getStringPref(MANAGED_SEARCH_PREF, ""),
  set: id => Services.prefs.setStringPref(MANAGED_SEARCH_PREF, id),
  clear: () => Services.prefs.clearUserPref(MANAGED_SEARCH_PREF),
};
const SEARCH_TOPIC = "browser-search-engine-modified";
const SEARCH_SETTLE_MS = 1000;

const ARCHIVE_CHECK_INTERVAL_MS = 10 * 60 * 1000;


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
    if (this._searchObserver) {
      Services.obs.removeObserver(this._searchObserver, SEARCH_TOPIC);
      this._searchObserver = null;
    }
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
    let { service, changeReason } = getSearchService();
    await service.init();
    if (!Services.prefs.getBoolPref(STEP_PREFS.search, false)) {
      await applyDefaultSearch(service, changeReason, managedSearch);
      Services.prefs.setBoolPref(STEP_PREFS.search, true);
    } else {
      await reconcileDefaultSearch(service, changeReason, managedSearch);
    }
    this._watchSearchEngines(service, changeReason);

    if (
      !Services.prefs.getBoolPref(STEP_PREFS.importOffer, false) &&
      Services.prefs.getBoolPref(IMPORT_PROMPT_PREF, true)
    ) {
      Services.prefs.setBoolPref(STEP_PREFS.importOffer, true);
      await this.offerImport();
    }
  },

  /**
   * Offer to bring bookmarks, passwords and history over from another
   * browser: Firefox's import window, shown once, and only when another
   * browser's data is found on this computer. Everything is read locally.
   *
   * @returns {Promise<"opened" | "nothing-found">}
   */
  async offerImport() {
    let { MigrationUtils } = ChromeUtils.importESModule(
      "resource:///modules/MigrationUtils.sys.mjs"
    );
    let found = false;
    for (let key of MigrationUtils.availableMigratorKeys) {
      if (await MigrationUtils.getMigrator(key)) {
        found = true;
        break;
      }
    }
    if (!found) {
      return "nothing-found";
    }
    // Without an opener this is a small standalone window, rather than a
    // Settings tab.
    MigrationUtils.showMigrationWizard(null, {
      entrypoint: MigrationUtils.MIGRATION_ENTRYPOINTS.FIRSTRUN,
    });
    return "opened";
  },

  /** Re-check the default whenever Firefox changes its engines (see reconcileDefaultSearch). */
  _watchSearchEngines(service, changeReason) {
    let timer = null;
    this._searchObserver = () => {
      timer?.cancel();
      timer = Cc["@mozilla.org/timer;1"].createInstance(Ci.nsITimer);
      timer.initWithCallback(
        () =>
          reconcileDefaultSearch(service, changeReason, managedSearch).catch(e =>
            console.error("Evergreen search default", e)
          ),
        SEARCH_SETTLE_MS,
        Ci.nsITimer.TYPE_ONE_SHOT
      );
    };
    Services.obs.addObserver(this._searchObserver, SEARCH_TOPIC);
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
