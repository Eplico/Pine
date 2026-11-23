/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

/**
 * Which GitHub release (if any) is a newer Evergreen than this one. Pure
 * functions over the GitHub "list releases" JSON, so they can be unit-tested;
 * EvergreenUpdate does the fetching and the UI.
 *
 * Releases are named by platform (design doc §9): Windows publishes v<N>, Linux
 * publishes v<N>-linux. A release counts only if its files for this platform
 * and SHA256SUMS.txt are attached.
 */

const VERSION_RE = /^[0-9]+(\.[0-9]+)*$/;

/** "0.10" -> [0, 10]; null for anything that is not dotted numbers (such as "dev"). */
export function parseVersion(text) {
  if (typeof text != "string" || !VERSION_RE.test(text)) {
    return null;
  }
  return text.split(".").map(Number);
}

/** -1, 0 or 1; null if either is not a version. Missing parts count as 0 ("0.2" == "0.2.0"). */
export function compareVersions(a, b) {
  let x = parseVersion(a);
  let y = parseVersion(b);
  if (!x || !y) {
    return null;
  }
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    let d = (x[i] ?? 0) - (y[i] ?? 0);
    if (d) {
      return d < 0 ? -1 : 1;
    }
  }
  return 0;
}

const PLATFORMS = {
  // AppConstants.platform -> how this platform's releases are named.
  linux: {
    tag: /^v([0-9][0-9.]*)-linux$/,
    asset: version => new RegExp(`^Evergreen-${version.replaceAll(".", "\\.")}-linux-x86_64-portable\\.tar\\.(xz|bz2|gz)$`),
  },
  win: {
    tag: /^v([0-9][0-9.]*)$/,
    asset: version => new RegExp(`^Evergreen-${version.replaceAll(".", "\\.")}-win64-setup\\.exe$`),
  },
};

/**
 * The newest published release for `platform`.
 *
 * @param {object[]} releases  GitHub's releases JSON.
 * @param {string} platform    AppConstants.platform ("linux", "win", "macosx").
 * @returns {{version: string, tag: string, pageUrl: string} | null}
 */
export function newestRelease(releases, platform) {
  let rules = PLATFORMS[platform];
  if (!rules || !Array.isArray(releases)) {
    return null;
  }
  let best = null;
  for (let r of releases) {
    let m = rules.tag.exec(r?.tag_name ?? "");
    if (!m || r.draft || r.prerelease || !parseVersion(m[1])) {
      continue;
    }
    let version = m[1];
    let names = new Set((r.assets ?? []).map(a => a?.name));
    let hasPackage = [...names].some(n => rules.asset(version).test(n ?? ""));
    if (!hasPackage || !names.has("SHA256SUMS.txt")) {
      continue;
    }
    if (!best || compareVersions(version, best.version) > 0) {
      best = { version, tag: r.tag_name, pageUrl: safePageUrl(r.html_url) };
    }
  }
  return best;
}

/** The release page, only if it is a plain https link. */
export function safePageUrl(url) {
  try {
    let u = new URL(url);
    return u.protocol == "https:" ? u.href : "";
  } catch {
    return "";
  }
}

/**
 * Is `release` newer than the running `current` version? A build with no
 * version number (the dev harness reports "dev") never claims an update.
 */
export function isUpdate(current, release) {
  return !!release && compareVersions(release.version, current) === 1;
}
