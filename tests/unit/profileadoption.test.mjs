/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

import assert from "node:assert/strict";
import { test } from "node:test";

import * as P from "../../src/browser/components/evergreen/ProfileAdoption.sys.mjs";

const MB = 1024 * 1024;
const NEW = "/home/u/.mozilla/evergreen/aaaa.default-release";
const prof = (name, bytes, extra = {}) => ({
  name,
  path: `/home/u/.mozilla/evergreen/${name}`,
  bytes,
  lastUsed: 1_800_000_000_000,
  lastVersion: "160.0.1",
  ...extra,
});

test("reads the Firefox version that last used a profile", () => {
  assert.equal(P.lastVersionOf("[Compatibility]\nLastVersion=160.0.1_20261110000000/20261110000000\nLastOSABI=Linux\n"), "160.0.1");
  assert.equal(P.lastVersionOf("LastVersion=157.0_2026/2026"), "157.0");
  assert.equal(P.lastVersionOf(""), null);
  assert.equal(P.lastVersionOf(undefined), null);
  assert.equal(P.lastVersionOf("[Compatibility]\nLastOSABI=Linux"), null);
});

test("versions compare numerically", () => {
  assert.equal(P.isNewerVersion("160.0.1", "160.0"), true);
  assert.equal(P.isNewerVersion("161.0", "160.9.9"), true);
  assert.equal(P.isNewerVersion("100.0", "99.0"), true);
  assert.equal(P.isNewerVersion("160.0", "160.0.0"), false);
  assert.equal(P.isNewerVersion("157.0", "160.0.1"), false);
});

test("the profile in use is never offered", () => {
  let cur = { ...prof("aaaa.default-release", 50 * MB), path: NEW };
  assert.equal(P.chooseProfile([cur], { currentPath: NEW, appVersion: "160.0.1" }), null);
});

test("the profile with the most data wins, not the most recent", () => {
  // An earlier move left an empty profile that was opened last; the real one is bigger.
  let real = prof("bbbb.default-release", 300 * MB, { lastUsed: 1_790_000_000_000 });
  let accident = prof("cccc.default-release", 6 * MB, { lastUsed: 1_799_000_000_000 });
  let picked = P.chooseProfile([accident, real], { currentPath: NEW, appVersion: "160.0.1" });
  assert.equal(picked.name, "bbbb.default-release");
});

test("equal data: the most recently used wins", () => {
  let older = prof("bbbb.default", 10 * MB, { lastUsed: 1_000 });
  let newer = prof("cccc.default", 10 * MB, { lastUsed: 2_000 });
  assert.equal(P.chooseProfile([older, newer], { currentPath: NEW, appVersion: "160.0.1" }).name, "cccc.default");
});

test("a profile last used by a newer build is skipped, because Firefox would refuse it", () => {
  let tooNew = prof("bbbb.default", 300 * MB, { lastVersion: "161.0" });
  let fine = prof("cccc.default", 20 * MB, { lastVersion: "157.0" });
  assert.equal(P.chooseProfile([tooNew, fine], { currentPath: NEW, appVersion: "160.0.1" }).name, "cccc.default");
  assert.equal(P.chooseProfile([tooNew], { currentPath: NEW, appVersion: "160.0.1" }), null);
});

test("a profile with no known last version is still offered", () => {
  let p = prof("bbbb.default", 20 * MB, { lastVersion: null });
  assert.equal(P.chooseProfile([p], { currentPath: NEW, appVersion: "160.0.1" }).name, "bbbb.default");
});

test("empty profiles and profiles without a folder are ignored", () => {
  let empty = prof("bbbb.default", 0);
  let nopath = { name: "x", path: "", bytes: 99 * MB, lastUsed: 1, lastVersion: null };
  assert.equal(P.chooseProfile([empty, nopath], { currentPath: NEW, appVersion: "160.0.1" }), null);
});

test("the offer is made once, on a first run, when enabled", () => {
  assert.equal(P.shouldOffer({ isFirstRun: true, alreadyOffered: false, enabled: true }), true);
  assert.equal(P.shouldOffer({ isFirstRun: false, alreadyOffered: false, enabled: true }), false);
  assert.equal(P.shouldOffer({ isFirstRun: true, alreadyOffered: true, enabled: true }), false);
  assert.equal(P.shouldOffer({ isFirstRun: true, alreadyOffered: false, enabled: false }), false);
});

test("nothing is offered unless it holds more data than the profile in use", () => {
  // Firefox carried the old profile over itself: the profile in use is the big one.
  let other = prof("bbbb.default", 20 * MB);
  assert.equal(P.chooseProfile([other], { currentPath: NEW, appVersion: "160.0.1", currentBytes: 300 * MB }), null);
  assert.equal(P.chooseProfile([other], { currentPath: NEW, appVersion: "160.0.1", currentBytes: 6 * MB }).name, "bbbb.default");
});
