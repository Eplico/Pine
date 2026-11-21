/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

/**
 * Firefox's own JS modules. Recent releases (160 at least) build them into
 * moz-src:/// URLs and no longer serve them from resource:///modules/; older
 * Firefox versions (the dev harness runs on whichever is installed) only have
 * the resource:// ones. Try the new location, then the old.
 *
 * @param {string} dir  Directory in Firefox's source, e.g. "browser/modules".
 * @param {string} name File name without ".sys.mjs", e.g. "BrowserWindowTracker".
 */
export function importBrowserModule(dir, name) {
  try {
    return ChromeUtils.importESModule(`moz-src:///${dir}/${name}.sys.mjs`);
  } catch {
    return ChromeUtils.importESModule(`resource:///modules/${name}.sys.mjs`);
  }
}
