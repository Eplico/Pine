/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

/**
 * The command bar's commands: pure functions over plain objects (no Firefox
 * dependencies), so the matching and ranking can be unit-tested in Node.
 *
 * The search box (EvergreenSearch) asks for commands that match the typed
 * text; each result is a row { kind: "command", id, args, label?, detail?,
 * action } where `id` is a Fluent message id (evergreen.ftl) and `action` is
 * a small description of what to do, which EvergreenWindow carries out. The
 * actions are data, never code, so nothing typed or stored can run anything.
 *
 *   { type: "switch-space", spaceId }
 *   { type: "move-tab", spaceId }
 *   { type: "select-tab", tabId }
 *   { type: "reopen", entryId }
 *   { type: "split" }, { type: "unsplit" }
 *   { type: "new-space" }
 *   { type: "toggle-keep" }
 *
 * Text starting with ">" asks for commands only ("> " alone lists them all);
 * otherwise commands are suggested only when two or more characters match.
 */

export const COMMAND_PREFIX = ">";
export const MAX_COMMAND_RESULTS = 3;
export const MAX_COMMAND_RESULTS_ONLY = 12;
const MIN_QUERY = 2;

/** Lowercase words of `text`, accents folded away. */
export function words(text) {
  return String(text ?? "")
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .split(/[\s/._:-]+/)
    .filter(Boolean);
}

/**
 * How well `query` matches a candidate (a haystack of text): 0 for no match,
 * otherwise higher is better. Every typed word has to start a word of the
 * haystack (3), or appear inside one (1); whole-label prefixes rank highest.
 */
export function score(query, label, keywords = "") {
  let q = words(query);
  if (!q.length) {
    return 1;
  }
  let hay = words(`${label} ${keywords}`);
  let total = 0;
  for (let word of q) {
    if (hay.some(h => h.startsWith(word))) {
      total += 3;
    } else if (hay.some(h => h.includes(word))) {
      total += 1;
    } else {
      return 0;
    }
  }
  if (words(label).join(" ").startsWith(q.join(" "))) {
    total += 2;
  }
  return total;
}

/** `{ commandsOnly, query }` for the typed text. */
export function parseQuery(text) {
  let t = String(text ?? "").trimStart();
  if (t.startsWith(COMMAND_PREFIX)) {
    return { commandsOnly: true, query: t.slice(COMMAND_PREFIX.length).trim() };
  }
  return { commandsOnly: false, query: t.trim() };
}

/**
 * Everything the typed text could run, best first.
 *
 * `context`:
 *   spaces: [{ id, name, color }]  (names already localized)
 *   activeSpaceId
 *   tab: { pinned, isStartPage, kept, split } for the selected tab
 *   tabs: [{ id, title, url, spaceId, selected }]   (open tabs, not hidden by a Space)
 *   archive: [{ id, title, url }]
 *   canSplit: boolean   (the window offers split view)
 */
export function matchCommands(text, context) {
  let { commandsOnly, query } = parseQuery(text);
  if (!commandsOnly && Array.from(query).length < MIN_QUERY) {
    return [];
  }
  let rows = [];
  let add = (id, args, haystack, action, extra = {}) => {
    let s = score(query, haystack.label, haystack.keywords);
    if (s > 0) {
      rows.push({ kind: "command", id, args, action, score: s, ...extra });
    }
  };

  let spaces = context.spaces ?? [];
  for (let space of spaces) {
    if (space.id != context.activeSpaceId) {
      add(
        "evergreen-command-switch-space",
        { name: space.name },
        { label: `switch to ${space.name}`, keywords: `go space ${space.name}` },
        { type: "switch-space", spaceId: space.id }
      );
    }
  }
  let tab = context.tab ?? {};
  if (!tab.pinned && !tab.isStartPage) {
    for (let space of spaces) {
      if (space.id != context.activeSpaceId) {
        add(
          "evergreen-command-move-tab",
          { name: space.name },
          { label: `move tab to ${space.name}`, keywords: `send space ${space.name}` },
          { type: "move-tab", spaceId: space.id }
        );
      }
    }
    add(
      tab.kept ? "evergreen-command-unkeep" : "evergreen-command-keep",
      {},
      { label: tab.kept ? "stop keeping tab" : "keep tab in space", keywords: "pin archive" },
      { type: "toggle-keep" }
    );
  }
  add("evergreen-command-new-space", {}, { label: "new space", keywords: "create add" }, { type: "new-space" });
  if (context.canSplit && !tab.isStartPage) {
    if (tab.split) {
      add("evergreen-command-unsplit", {}, { label: "close split view", keywords: "unsplit separate" }, { type: "unsplit" });
    } else {
      add("evergreen-command-split", {}, { label: "split view", keywords: "side by side two panes" }, { type: "split" });
    }
  }

  // Open tabs ("switch to tab") and the Archive (reopen) match by what they
  // are, so they need a typed word; with ">" alone only commands are listed.
  if (query) {
    for (let t of context.tabs ?? []) {
      if (t.selected) {
        continue;
      }
      add(
        "evergreen-command-select-tab",
        { title: t.title || t.url },
        { label: t.title || t.url, keywords: t.url },
        { type: "select-tab", tabId: t.id },
        { detail: t.url }
      );
    }
    for (let entry of context.archive ?? []) {
      add(
        "evergreen-command-reopen",
        { title: entry.title || entry.url },
        { label: entry.title || entry.url, keywords: `${entry.url} archive` },
        { type: "reopen", entryId: entry.id },
        { detail: entry.url }
      );
    }
  }

  // Stable ordering: score, then the order built above.
  rows = rows.map((r, i) => ({ r, i })).sort((a, b) => b.r.score - a.r.score || a.i - b.i).map(x => x.r);
  return rows.slice(0, commandsOnly ? MAX_COMMAND_RESULTS_ONLY : MAX_COMMAND_RESULTS);
}
