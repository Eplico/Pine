/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

/**
 * Archived tabs (profile/evergreen/archive.jsonlz4, compressed like
 * Firefox's own session files). The archive is browsing history, so
 * EvergreenStartup wires it to Firefox's history-clearing notifications.
 */

import {
  DEFAULT_SETTINGS,
  addEntries,
  pruneEntries,
  removeEntriesForDomain,
  removeEntry,
} from "./Archive.sys.mjs";

function storePath() {
  return PathUtils.join(PathUtils.profileDir, "evergreen", "archive.jsonlz4");
}

export function archiveSettings() {
  let p = (name, fallback) => Services.prefs.getIntPref(name, fallback);
  return {
    afterHours: p("evergreen.archive.afterHours", DEFAULT_SETTINGS.afterHours),
    maxEntries: p("evergreen.archive.maxEntries", DEFAULT_SETTINGS.maxEntries),
    maxAgeDays: p("evergreen.archive.maxAgeDays", DEFAULT_SETTINGS.maxAgeDays),
  };
}

class Store {
  #entries = [];
  #ready = null;
  #listeners = new Set();
  #writes = Promise.resolve();

  ready() {
    this.#ready ??= this.#load();
    return this.#ready;
  }

  async #load() {
    try {
      let data = await IOUtils.readJSON(storePath(), { decompress: true });
      this.#entries = pruneEntries(
        Array.isArray(data?.entries) ? data.entries : [],
        Date.now(),
        archiveSettings()
      );
    } catch (e) {
      if (!DOMException.isInstance(e) || e.name != "NotFoundError") {
        console.error("Evergreen: could not read the archive, starting empty", e);
      }
      this.#entries = [];
    }
  }

  get entries() {
    return this.#entries;
  }

  add(newEntries) {
    if (newEntries.length) {
      this.#set(addEntries(this.#entries, newEntries, Date.now(), archiveSettings()));
    }
  }

  remove(id) {
    this.#set(removeEntry(this.#entries, id));
  }

  removeForDomain(domain) {
    this.#set(removeEntriesForDomain(this.#entries, domain));
  }

  /** Forget everything, on disk too. */
  clear() {
    this.#entries = [];
    this.#notify();
    let path = storePath();
    this.#writes = this.#writes
      .then(() => IOUtils.remove(path, { ignoreAbsent: true }))
      .catch(e => console.error("Evergreen: could not delete the archive", e));
    return this.#writes;
  }

  addListener(fn) {
    this.#listeners.add(fn);
  }

  removeListener(fn) {
    this.#listeners.delete(fn);
  }

  flush() {
    return this.#writes;
  }

  #set(entries) {
    this.#entries = entries;
    this.#notify();
    let snapshot = { version: 1, entries };
    let path = storePath();
    this.#writes = this.#writes
      .then(async () => {
        await IOUtils.makeDirectory(PathUtils.parent(path));
        await IOUtils.writeJSON(path, snapshot, { compress: true, tmpPath: path + ".tmp" });
      })
      .catch(e => console.error("Evergreen: could not save the archive", e));
  }

  #notify() {
    for (let listener of this.#listeners) {
      try {
        listener(this.#entries);
      } catch (e) {
        console.error(e);
      }
    }
  }
}

export const ArchiveStore = new Store();
