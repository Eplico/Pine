/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

/**
 * Loads and saves the Spaces list (profile/evergreen/spaces.json) and tells
 * every window when it changes. Which Space each tab belongs to is not kept
 * here: it is stored on the tab through SessionStore, so it survives
 * restarts and crash recovery together with the tabs themselves.
 */

import { normalizeState } from "./Spaces.sys.mjs";

function storePath() {
  return PathUtils.join(PathUtils.profileDir, "evergreen", "spaces.json");
}

class Store {
  #state = null;
  #ready = null;
  #listeners = new Set();
  #writes = Promise.resolve();

  ready() {
    this.#ready ??= this.#load();
    return this.#ready;
  }

  async #load() {
    let raw = null;
    try {
      raw = await IOUtils.readJSON(storePath());
    } catch (e) {
      if (!DOMException.isInstance(e) || e.name != "NotFoundError") {
        console.error("Evergreen: could not read spaces.json, starting fresh", e);
      }
    }
    this.#state = normalizeState(raw);
  }

  get state() {
    if (!this.#state) {
      throw new Error("SpacesStore used before ready()");
    }
    return this.#state;
  }

  /** Apply `fn(state) -> state`, persist, and notify listeners. */
  update(fn) {
    this.#state = normalizeState(fn(this.state));
    this.#save();
    for (let listener of this.#listeners) {
      try {
        listener(this.#state);
      } catch (e) {
        console.error(e);
      }
    }
    return this.#state;
  }

  addListener(fn) {
    this.#listeners.add(fn);
  }

  removeListener(fn) {
    this.#listeners.delete(fn);
  }

  #save() {
    let snapshot = this.#state;
    let path = storePath();
    this.#writes = this.#writes
      .then(async () => {
        await IOUtils.makeDirectory(PathUtils.parent(path));
        await IOUtils.writeJSON(path, snapshot, { tmpPath: path + ".tmp" });
      })
      .catch(e => console.error("Evergreen: could not save spaces.json", e));
  }

  flush() {
    return this.#writes;
  }
}

export const SpacesStore = new Store();
