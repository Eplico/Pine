/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

import assert from "node:assert/strict";
import { test } from "node:test";

import * as A from "../../src/browser/components/evergreen/Archive.sys.mjs";

const HOUR = 3600 * 1000;
const NOW = 1_800_000_000_000;

function idle(overrides = {}) {
  return { lastAccessed: NOW - 13 * HOUR, ...overrides };
}

test("idle unpinned tabs are archivable after the threshold", () => {
  assert.equal(A.isArchivable(idle(), NOW), true);
  assert.equal(A.isArchivable(idle({ lastAccessed: NOW - 12 * HOUR }), NOW), true);
  assert.equal(A.isArchivable(idle({ lastAccessed: NOW - 11 * HOUR }), NOW), false);
});

test("tabs the user cares about are never archived", () => {
  for (let flag of ["pinned", "kept", "selected", "soundPlaying", "sharing", "privateWindow"]) {
    assert.equal(A.isArchivable(idle({ [flag]: true }), NOW), false, flag);
  }
});

test("archiving can be turned off, and bad timestamps are ignored", () => {
  assert.equal(A.isArchivable(idle(), NOW, { ...A.DEFAULT_SETTINGS, afterHours: 0 }), false);
  assert.equal(A.isArchivable(idle({ lastAccessed: 0 }), NOW), false);
  assert.equal(A.isArchivable(idle({ lastAccessed: NaN }), NOW), false);
  assert.equal(A.isArchivable(idle({ lastAccessed: undefined }), NOW), false);
});

test("only http(s) pages get archive entries", () => {
  assert.equal(A.isRecordable("https://example.org/"), true);
  assert.equal(A.isRecordable("HTTP://example.org/"), true);
  for (let url of ["about:newtab", "javascript:alert(1)", "data:text/html,x", "file:///etc/passwd", "", null]) {
    assert.equal(A.isRecordable(url), false, String(url));
  }
});

test("makeEntry keeps what is needed to reopen the tab", () => {
  let e = A.makeEntry(
    { url: "https://example.org/", title: "t".repeat(400), spaceId: "s", userContextId: 3 },
    NOW,
    () => 0.5
  );
  assert.equal(e.title.length, 300);
  assert.deepEqual([e.url, e.spaceId, e.userContextId, e.archivedAt], ["https://example.org/", "s", 3, NOW]);
  assert.match(e.id, /^a-/);
});

test("pruneEntries sorts, bounds and drops tampered entries", () => {
  let entries = [
    { id: "old", url: "https://a.example/", archivedAt: NOW - 40 * 24 * HOUR },
    { id: "js", url: "javascript:alert(1)", archivedAt: NOW },
    { id: "future", url: "https://b.example/", archivedAt: NOW + 5 * 24 * HOUR },
    { id: "1", url: "https://c.example/", archivedAt: NOW - 2 * HOUR },
    { id: "2", url: "https://d.example/", archivedAt: NOW - HOUR },
    null,
  ];
  assert.deepEqual(A.pruneEntries(entries, NOW).map(e => e.id), ["2", "1"]);
  let settings = { ...A.DEFAULT_SETTINGS, maxEntries: 1 };
  assert.deepEqual(A.pruneEntries(entries, NOW, settings).map(e => e.id), ["2"]);
});

test("addEntries puts new entries first", () => {
  let old = [{ id: "o", url: "https://o.example/", archivedAt: NOW - HOUR }];
  let fresh = [{ id: "n", url: "https://n.example/", archivedAt: NOW }];
  assert.deepEqual(A.addEntries(old, fresh, NOW).map(e => e.id), ["n", "o"]);
});

test("removeEntriesForDomain removes the domain and its subdomains only", () => {
  let entries = ["https://example.org/", "https://www.example.org/x", "https://notexample.org/", "https://example.com/"].map(
    (url, i) => ({ id: String(i), url, archivedAt: NOW })
  );
  assert.deepEqual(A.removeEntriesForDomain(entries, "example.org").map(e => e.url), [
    "https://notexample.org/",
    "https://example.com/",
  ]);
  assert.equal(A.removeEntriesForDomain(entries, "").length, 4);
  assert.deepEqual(A.removeEntry(entries, "0").map(e => e.id), ["1", "2", "3"]);
});
