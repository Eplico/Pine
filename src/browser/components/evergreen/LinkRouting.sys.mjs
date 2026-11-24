/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

/**
 * Link routing (design doc §7.8): rules that send a site to a Space. Pure
 * functions over plain objects, unit-tested in Node.
 *
 * A rule is { host, spaceId }. It matches the host and its subdomains
 * ("github.com" also matches "gist.github.com"); the longest matching host
 * wins. Rules apply only to loads that start a NEW tab from the browser's
 * own UI (the search box, bookmarks, links opened from other apps) and never
 * to navigation inside a tab or to links clicked on a page, so a site can't
 * move itself between Spaces and sign-in flows (OAuth, SSO) keep working.
 *
 * Stored as JSON in the pref evergreen.routing.rules.
 */

export const MAX_RULES = 100;
const HOST_RE = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/;

/** The host of an http(s) address, lowercased, without a leading "www."; else null. */
export function siteOf(url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  if (parsed.protocol != "https:" && parsed.protocol != "http:") {
    return null;
  }
  let host = parsed.hostname.toLowerCase().replace(/^www\./, "");
  return HOST_RE.test(host) ? host : null;
}

/** Rules read from the pref's text; anything invalid is dropped. */
export function parseRules(json) {
  let raw;
  try {
    raw = JSON.parse(json || "[]");
  } catch {
    return [];
  }
  let rules = [];
  let seen = new Set();
  for (let r of Array.isArray(raw) ? raw : []) {
    let host = typeof r?.host == "string" ? r.host.toLowerCase().replace(/^www\./, "") : "";
    if (!HOST_RE.test(host) || typeof r.spaceId != "string" || !r.spaceId || seen.has(host)) {
      continue;
    }
    seen.add(host);
    rules.push({ host, spaceId: r.spaceId.slice(0, 64) });
    if (rules.length >= MAX_RULES) {
      break;
    }
  }
  return rules;
}

export function serializeRules(rules) {
  return JSON.stringify(rules.map(({ host, spaceId }) => ({ host, spaceId })));
}

/** Rules with `host` sent to `spaceId` (replacing an earlier rule for it). */
export function addRule(rules, host, spaceId) {
  let site = siteOf(`https://${host}/`);
  if (!site || !spaceId) {
    throw new Error("Not a site that can be routed");
  }
  let others = rules.filter(r => r.host != site);
  if (others.length >= MAX_RULES) {
    throw new Error("Too many routing rules");
  }
  return [...others, { host: site, spaceId }];
}

export function removeRule(rules, host) {
  return rules.filter(r => r.host != host);
}

/** Drop the rules of Spaces that no longer exist. */
export function pruneRules(rules, spaceIds) {
  let ids = new Set(spaceIds);
  return rules.filter(r => ids.has(r.spaceId));
}

/** The rule that applies to an address, or null. */
export function ruleFor(url, rules) {
  let site = siteOf(url);
  if (!site) {
    return null;
  }
  let best = null;
  for (let rule of rules) {
    if (site == rule.host || site.endsWith("." + rule.host)) {
      if (!best || rule.host.length > best.host.length) {
        best = rule;
      }
    }
  }
  return best;
}

/** The Space an address is routed to, if that Space exists; else null. */
export function routeFor(url, rules, spaceIds) {
  let rule = ruleFor(url, rules);
  return rule && spaceIds.includes(rule.spaceId) ? rule.spaceId : null;
}
