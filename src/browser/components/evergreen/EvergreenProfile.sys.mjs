/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

/**
 * Carry your data over to a new Evergreen installation.
 *
 * Firefox ties a profile to the folder the program runs from, so Evergreen
 * unpacked or installed in a new place starts with an empty profile and leaves
 * tabs, Spaces, logins and settings behind in the old one. On such a first run
 * this offers to switch to the profile with the most data, using the same
 * mechanism as about:profiles (set it as this installation's default, then
 * restart). Nothing is copied, moved or deleted: both profiles stay as they were.
 *
 * Linux installs made by evergreen-install never change folder, so updates do
 * not need this; it is for moving from an older or hand-unpacked copy.
 */

import { DATA_FILES, chooseProfile, lastVersionOf, shouldOffer } from "./ProfileAdoption.sys.mjs";

const PREF_ENABLED = "evergreen.firstrun.profilePrompt";
const PREF_OFFERED = "evergreen.profile.adoptOffered";
const NOTIFICATION = "evergreen-adopt-profile";

const toolkitProfiles = () =>
  Cc["@mozilla.org/toolkit/profile-service;1"].getService(Ci.nsIToolkitProfileService);

async function fileInfo(path) {
  try {
    let info = await IOUtils.stat(path);
    return { size: info.size ?? 0, modified: info.lastModified ?? 0 };
  } catch {
    return null;
  }
}

/** How much data a profile folder holds, when it was last used, and by which Firefox. */
async function describeFolder(root) {
  let bytes = 0;
  let lastUsed = 0;
  for (let rel of [...DATA_FILES, "prefs.js"]) {
    let info = await fileInfo(PathUtils.join(root, ...rel.split("/")));
    if (info) {
      if (rel != "prefs.js") {
        bytes += info.size;
      }
      lastUsed = Math.max(lastUsed, info.modified);
    }
  }
  let lastVersion = null;
  try {
    lastVersion = lastVersionOf(await IOUtils.readUTF8(PathUtils.join(root, "compatibility.ini")));
  } catch {
    // No compatibility.ini: the profile never finished a start-up.
  }
  return { bytes, lastUsed, lastVersion };
}

export const EvergreenProfile = {
  /**
   * Every profile Evergreen knows, as plain data (plus the profile object).
   * `service` is injectable for tests.
   */
  async describeProfiles(service = toolkitProfiles()) {
    let out = [];
    for (let p of service.profiles) {
      p = p.QueryInterface(Ci.nsIToolkitProfile);
      let path = p.rootDir.path;
      out.push({ name: p.name, path, profile: p, ...(await describeFolder(path)) });
    }
    return out;
  },

  /**
   * The earlier profile worth offering, or null.
   */
  async candidate(service = toolkitProfiles()) {
    let currentPath = PathUtils.profileDir;
    let profiles = await this.describeProfiles(service);
    let current = await describeFolder(currentPath);
    return chooseProfile(profiles, {
      currentPath,
      appVersion: Services.appinfo.version,
      currentBytes: current.bytes,
    });
  },

  /**
   * On the first run of an installation, show the offer in `win` if an
   * earlier profile has more data than this one.
   *
   * @returns {Promise<"offered" | "skipped" | "none">}
   */
  async offerIfNeeded(win) {
    let service = toolkitProfiles();
    if (
      !shouldOffer({
        isFirstRun: service.isFirstRun,
        alreadyOffered: Services.prefs.getBoolPref(PREF_OFFERED, false),
        enabled: Services.prefs.getBoolPref(PREF_ENABLED, true),
      })
    ) {
      return "skipped";
    }
    let found = await this.candidate(service);
    if (!found) {
      return "none";
    }
    await this._showOffer(win, service, found);
    return "offered";
  },

  async _showOffer(win, service, found) {
    let box = win.gNotificationBox ?? win.gBrowser.getNotificationBox();
    if (box.getNotificationWithValue(NOTIFICATION)) {
      return;
    }
    let date = found.lastUsed
      ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(found.lastUsed))
      : "";
    let megabytes = Math.max(1, Math.round(found.bytes / (1024 * 1024)));
    let answered = false;
    let answer = () => {
      answered = true;
      Services.prefs.setBoolPref(PREF_OFFERED, true);
    };
    await box.appendNotification(
      NOTIFICATION,
      {
        label: {
          "l10n-id": date ? "evergreen-profile-offer" : "evergreen-profile-offer-undated",
          "l10n-args": { date, megabytes },
        },
        priority: box.PRIORITY_INFO_HIGH,
        // Closing the bar without answering counts as "start fresh", and it
        // is not asked again: this runs on first runs only.
        eventCallback: event => {
          if (event == "dismissed" && !answered) {
            answer();
          }
        },
      },
      [
        {
          "l10n-id": "evergreen-profile-use",
          callback: () => {
            answer();
            this.adopt(found.profile, service).catch(e => console.error("Evergreen profile switch", e));
            return true; // keep the bar until the restart
          },
        },
        {
          "l10n-id": "evergreen-profile-keep",
          callback: () => {
            answer();
            return false;
          },
        },
      ]
    );
  },

  /** Make `profile` this installation's default, then restart into it (as about:profiles does). */
  async adopt(profile, service = toolkitProfiles()) {
    service.defaultProfile = profile;
    await service.asyncFlush();
    let cancel = Cc["@mozilla.org/supports-PRBool;1"].createInstance(Ci.nsISupportsPRBool);
    Services.obs.notifyObservers(cancel, "quit-application-requested", "restart");
    if (!cancel.data) {
      Services.startup.quit(Ci.nsIAppStartup.eAttemptQuit | Ci.nsIAppStartup.eRestart);
    }
  },
};
