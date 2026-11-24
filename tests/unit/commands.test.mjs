/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  SETTINGS,
  matchCommands,
  validSetting,
  parseQuery,
  score,
  words,
} from "../../src/browser/components/evergreen/Commands.sys.mjs";

function context(extra = {}) {
  return {
    spaces: [
      { id: "a", name: "Personal" },
      { id: "b", name: "Work" },
      { id: "c", name: "Café" },
    ],
    activeSpaceId: "a",
    tab: { pinned: false, isStartPage: false, kept: false, split: false },
    tabs: [
      { id: "0", title: "Inbox", url: "https://mail.example/inbox", selected: false },
      { id: "1", title: "Current page", url: "https://example.org/", selected: true },
    ],
    archive: [{ id: "x1", title: "Old recipe", url: "https://food.example/recipe" }],
    canSplit: true,
    ...extra,
  };
}

test("words folds accents and splits on punctuation", () => {
  assert.deepEqual(words("Café-au_lait.com/x"), ["cafe", "au", "lait", "com", "x"]);
});

test("score needs every typed word to match", () => {
  assert.equal(score("switch work", "switch to Work", "go space Work") > 0, true);
  assert.equal(score("switch nope", "switch to Work", ""), 0);
  assert.ok(score("sw", "switch to Work") > score("itch", "switch to Work"));
});

test("a prefix of the label ranks highest", () => {
  assert.ok(score("switch to w", "switch to Work") > score("work", "switch to Work"));
});

test("parseQuery: > asks for commands only", () => {
  assert.deepEqual(parseQuery("  > split"), { commandsOnly: true, query: "split" });
  assert.deepEqual(parseQuery(">"), { commandsOnly: true, query: "" });
  assert.deepEqual(parseQuery("work"), { commandsOnly: false, query: "work" });
});

test("one character suggests nothing unless commands are asked for", () => {
  assert.deepEqual(matchCommands("w", context()), []);
  assert.ok(matchCommands(">", context()).length > 3);
});

test("switching to another Space", () => {
  let rows = matchCommands("switch work", context());
  assert.equal(rows[0].id, "evergreen-command-switch-space");
  assert.deepEqual(rows[0].args, { name: "Work" });
  assert.deepEqual(rows[0].action, { type: "switch-space", spaceId: "b" });
  // The active Space is never offered.
  assert.equal(matchCommands("switch personal", context()).some(r => r.action.spaceId == "a"), false);
});

test("accents are ignored when matching Space names", () => {
  let rows = matchCommands("move cafe", context());
  assert.deepEqual(rows[0].action, { type: "move-tab", spaceId: "c" });
});

test("moving and keeping are not offered for Favorites or the start page", () => {
  for (let tab of [
    { pinned: true, isStartPage: false },
    { pinned: false, isStartPage: true },
  ]) {
    let rows = matchCommands("> ", context({ tab }));
    assert.equal(rows.some(r => ["move-tab", "toggle-keep"].includes(r.action.type)), false, JSON.stringify(tab));
  }
});

test("keep toggles by state", () => {
  assert.equal(matchCommands("> keep", context())[0].id, "evergreen-command-keep");
  let kept = context({ tab: { pinned: false, isStartPage: false, kept: true, split: false } });
  assert.equal(matchCommands("> keep", kept)[0].id, "evergreen-command-unkeep");
});

test("open tabs are found by title or address, except the current one", () => {
  let rows = matchCommands("inbox", context());
  assert.deepEqual(rows[0].action, { type: "select-tab", tabId: "0" });
  assert.equal(rows[0].detail, "https://mail.example/inbox");
  assert.equal(matchCommands("current page", context()).some(r => r.action.type == "select-tab"), false);
});

test("Archive entries are found by title, address or the word archive", () => {
  assert.deepEqual(matchCommands("recipe", context())[0].action, { type: "reopen", entryId: "x1" });
  assert.deepEqual(matchCommands("archive old", context())[0].action, { type: "reopen", entryId: "x1" });
});

test("split view offers split or close, only where the window has it", () => {
  assert.deepEqual(matchCommands("split", context())[0].action, { type: "split" });
  let split = context({ tab: { pinned: false, isStartPage: false, kept: false, split: true } });
  assert.deepEqual(matchCommands("split", split)[0].action, { type: "unsplit" });
  assert.equal(matchCommands("split", context({ canSplit: false })).length, 0);
});

test("inline suggestions are capped, commands-only lists more", () => {
  assert.equal(matchCommands("space", context()).length, 3);
  assert.ok(matchCommands("> space", context()).length > 3);
});

test("actions are plain data", () => {
  for (let row of matchCommands("> ", context())) {
    assert.equal(JSON.stringify(row.action), JSON.stringify(structuredClone(row.action)));
  }
});

test("link routing: offered for the current site, with a way to stop", () => {
  let rows = matchCommands("always open", context({ site: "github.com", routedTo: null }));
  assert.deepEqual(rows[0].action, { type: "route-site", host: "github.com", spaceId: "a" });
  let routed = context({ site: "github.com", routedTo: "b" });
  assert.equal(matchCommands("> stop routing", routed)[0].action.type, "unroute-site");
  assert.equal(matchCommands("> always open github.com in work", routed).length, 0);
  assert.equal(matchCommands("> routing", context()).length, 0); // no site: nothing to route
});

test("mini window: not for Favorites or the start page, or where unavailable", () => {
  assert.deepEqual(matchCommands("mini window", context({ canMini: true }))[0].action, { type: "mini-window" });
  assert.equal(matchCommands("mini window", context()).length, 0);
  assert.equal(
    matchCommands("mini window", context({ canMini: true, tab: { pinned: true, isStartPage: false } })).length,
    0
  );
});

const prefs = {
  "evergreen.downloads.enabled": true,
  "evergreen.downloads.connections": 6,
  "evergreen.downloads.maxActive": 3,
  "evergreen.peek.enabled": true,
  "evergreen.externalLinks.miniWindow": false,
};

test("settings: toggles offer the other state, connection counts the other numbers", () => {
  let off = matchCommands("> turn off faster", context({ prefs }));
  assert.deepEqual(off[0].action, { type: "set-pref", name: "evergreen.downloads.enabled", value: false });
  let mini = matchCommands("> other apps", context({ prefs }));
  assert.deepEqual(mini[0].action, { type: "set-pref", name: "evergreen.externalLinks.miniWindow", value: true });
  let eight = matchCommands("> 8 connections", context({ prefs }));
  assert.deepEqual(eight[0].action, { type: "set-pref", name: "evergreen.downloads.connections", value: 8 });
  assert.equal(
    matchCommands("> 6 connections", context({ prefs })).some(r => r.action.value === 6),
    false
  );
  let disabled = matchCommands("> connections", context({ prefs: { ...prefs, "evergreen.downloads.enabled": false } }));
  assert.equal(disabled.some(r => r.action.name == "evergreen.downloads.connections"), false);
});

test("settings: every offered action passes validSetting, nothing else does", () => {
  for (let row of matchCommands("> ", context({ prefs, site: "a.example", canMini: true }))) {
    if (row.action.type == "set-pref") {
      assert.equal(validSetting(row.action), true, JSON.stringify(row.action));
    }
  }
  assert.equal(validSetting({ name: "javascript.enabled", value: false }), false);
  assert.equal(validSetting({ name: "evergreen.peek.enabled", value: "yes" }), false);
  assert.equal(validSetting({ name: "evergreen.downloads.connections", value: 99 }), false);
  assert.equal(validSetting({ name: "evergreen.downloads.connections", value: 8 }), true);
  assert.equal(validSetting(null), false);
  assert.ok(Object.isFrozen(SETTINGS));
});
