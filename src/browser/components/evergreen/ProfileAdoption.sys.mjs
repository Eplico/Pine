/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

/**
 * Which earlier profile should a new Evergreen installation offer to take
 * over? Pure functions over plain descriptions of profiles, so they can be
 * unit-tested; EvergreenProfile gathers the descriptions and shows the offer.
 *
 * Why this exists: Firefox ties a profile to the folder the program runs
 * from. Unpack or install Evergreen somewhere new and it starts with an empty
 * profile, leaving tabs, Spaces, logins and settings behind in the old one.
 */

/** Files whose sizes say how much a profile has been used (relative to the profile folder). */
export const DATA_FILES = [
  "places.sqlite",
  "places.sqlite-wal",
  "favicons.sqlite",
  "cookies.sqlite",
  "formhistory.sqlite",
  "logins.json",
  "key4.db",
  "permissions.sqlite",
  "evergreen/spaces.json",
  "evergreen/archive.jsonlz4",
  "sessionstore-backups/recovery.jsonlz4",
];

/**
 * The version in compatibility.ini's "LastVersion=160.0.1_20261110000000/..."
 * line (the build that last used the profile), or null.
 */
export function lastVersionOf(compatibilityIni) {
  let m = /^LastVersion=([0-9][0-9.]*)/m.exec(compatibilityIni ?? "");
  return m ? m[1] : null;
}

function parts(version) {
  return String(version)
    .split(".")
    .map(n => parseInt(n, 10) || 0);
}

/** Is `a` a newer Firefox version than `b`? ("160.0.1" > "160.0") */
export function isNewerVersion(a, b) {
  let x = parts(a);
  let y = parts(b);
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    let d = (x[i] ?? 0) - (y[i] ?? 0);
    if (d) {
      return d > 0;
    }
  }
  return false;
}

/**
 * Pick the profile to offer, or null.
 *
 * @param {object[]} profiles  Each: { name, path, bytes, lastUsed, lastVersion }
 *   (bytes: size of the profile's data files; lastUsed: ms since the epoch,
 *   0 if unknown; lastVersion: Firefox version that last used it, or null).
 * @param {object} options
 * @param {string} options.currentPath  The profile in use now (never offered).
 * @param {string} options.appVersion   This build's Firefox version. A profile
 *   last used by a newer build is skipped: Firefox refuses to start those.
 * @param {number} [options.currentBytes] Data in the profile in use now. Only a
 *   profile with more than this is offered: when Firefox already carried the
 *   old profile over, the one in use is the biggest and nothing is offered.
 */
export function chooseProfile(profiles, { currentPath, appVersion, currentBytes = 0 }) {
  let usable = profiles.filter(
    p =>
      p.path &&
      p.path != currentPath &&
      p.bytes > currentBytes &&
      !(p.lastVersion && isNewerVersion(p.lastVersion, appVersion))
  );
  if (!usable.length) {
    return null;
  }
  // The profile with the most data is the one that was really used; a profile
  // an earlier move created and never filled would otherwise win on recency.
  usable.sort((a, b) => b.bytes - a.bytes || b.lastUsed - a.lastUsed);
  return usable[0];
}

/**
 * A fresh install sees more than "no profile": Firefox only claims an old
 * profile for the new install when the first run asks it to. Offer only on
 * a first run, and only once.
 */
export function shouldOffer({ isFirstRun, alreadyOffered, enabled }) {
  return !!enabled && !!isFirstRun && !alreadyOffered;
}
