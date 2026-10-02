/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

/**
 * Archive policy: which tabs auto-archive, and how the archive list is kept
 * bounded. Pure functions over plain objects so they can be unit-tested.
 *
 * The archive is browsing history. ArchiveStore clears it with every
 * Firefox history-clearing path, and nothing from private windows is
 * ever archived.
 */

export const DEFAULT_SETTINGS = Object.freeze({
  afterHours: 12,
  maxEntries: 500,
  maxAgeDays: 30,
});

const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;

/**
 * Should this tab be archived now?
 *
 * @param {object} tab  A plain description of the tab:
 *   { pinned, kept, selected, soundPlaying, sharing, privateWindow, lastAccessed }
 * @param {number} now
 * @param {object} settings
 */
export function isArchivable(tab, now, settings = DEFAULT_SETTINGS) {
  if (!(settings.afterHours > 0)) {
    return false;
  }
  if (
    tab.pinned ||
    tab.kept ||
    tab.selected ||
    tab.soundPlaying ||
    tab.sharing ||
    tab.privateWindow
  ) {
    return false;
  }
  if (!Number.isFinite(tab.lastAccessed) || tab.lastAccessed <= 0) {
    return false;
  }
  return now - tab.lastAccessed >= settings.afterHours * HOUR;
}

/** Only real web pages are worth an archive entry; blank tabs just close. */
export function isRecordable(url) {
  return /^https?:\/\//i.test(url ?? "");
}

export function makeEntry({ url, title, spaceId, userContextId = 0 }, now, random = Math.random) {
  return {
    id: "a-" + now.toString(36) + "-" + Math.floor(random() * 36 ** 6).toString(36),
    url: String(url),
    title: String(title ?? "").slice(0, 300),
    spaceId: spaceId ?? null,
    userContextId: Number.isInteger(userContextId) ? userContextId : 0,
    archivedAt: now,
  };
}

/** Drop entries older than maxAgeDays and keep at most maxEntries, newest first. */
export function pruneEntries(entries, now, settings = DEFAULT_SETTINGS) {
  let cutoff = now - settings.maxAgeDays * DAY;
  return entries
    .filter(e => e && isRecordable(e.url) && e.archivedAt >= cutoff && e.archivedAt <= now + DAY)
    .sort((a, b) => b.archivedAt - a.archivedAt)
    .slice(0, settings.maxEntries);
}

export function addEntries(entries, newEntries, now, settings = DEFAULT_SETTINGS) {
  return pruneEntries([...newEntries, ...entries], now, settings);
}

export function removeEntry(entries, id) {
  return entries.filter(e => e.id != id);
}

function hostOf(url) {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch {
    return "";
  }
}

/** "Forget About This Site": drop entries for `domain` and its subdomains. */
export function removeEntriesForDomain(entries, domain) {
  let d = String(domain).toLowerCase().replace(/^\.+/, "");
  if (!d) {
    return entries;
  }
  return entries.filter(e => {
    let host = hostOf(e.url);
    return host != d && !host.endsWith("." + d);
  });
}
