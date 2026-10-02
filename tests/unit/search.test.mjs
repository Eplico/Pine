/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ECOSIA,
  applyDefaultSearch,
  ensureEngine,
  reconcileDefaultSearch,
} from "../../src/browser/components/evergreen/SearchDefaults.sys.mjs";

/** A fake of the bits of Services.search that SearchDefaults uses. */
function fakeSearch({ engines = [], legacy = false, defaultName = null } = {}) {
  let calls = [];
  let nextId = 1;
  let engine = name => ({ name, id: `${name.toLowerCase()}-${nextId++}`, hidden: false });
  let list = engines.map(engine);
  let svc = {
    calls,
    list,
    defaultEngine: list.find(e => e.name == defaultName) ?? null,
    getEngineByName: name => list.find(e => e.name == name) ?? null,
    getEngineById: id => list.find(e => e.id == id) ?? null,
    async setDefault(e, reason) {
      calls.push(["setDefault", e.name, reason]);
      svc.defaultEngine = e;
    },
    /** What Firefox does when its own copy of an engine replaces a user's. */
    replaceWithAppEngine(name, fallbackName) {
      let i = list.findIndex(e => e.name == name);
      list.splice(i, 1, engine(name));
      svc.defaultEngine = list.find(e => e.name == fallbackName);
    },
  };
  svc.addUserEngine = legacy
    ? async function (name, url, alias) {
        calls.push(["addUserEngine", { name, url, alias }]);
        list.push(engine(name));
      }
    : async function (formInfo) {
        calls.push(["addUserEngine", formInfo]);
        list.push(engine(formInfo.name));
      };
  return svc;
}

/** The pref that remembers which engine Evergreen made the default. */
function fakeManaged(value = "") {
  return { value, get: () => value, set: v => (value = v), clear: () => (value = "") };
}

test("uses Firefox's own Ecosia engine when it exists", async () => {
  let svc = fakeSearch({ engines: ["Google", "Ecosia"] });
  let result = await applyDefaultSearch(svc, 7);
  assert.equal(result.added, false);
  assert.deepEqual(svc.calls, [["setDefault", "Ecosia", 7]]);
});

test("adds Ecosia with the current form-info API", async () => {
  let svc = fakeSearch({ engines: ["Google", "DuckDuckGo"] });
  let result = await applyDefaultSearch(svc, 0);
  assert.equal(result.added, true);
  assert.deepEqual(svc.calls[0], [
    "addUserEngine",
    { name: "Ecosia", url: ECOSIA.url, suggestUrl: ECOSIA.suggestUrl, alias: "@ecosia" },
  ]);
  assert.deepEqual(svc.calls[1], ["setDefault", "Ecosia", 0]);
});

test("adds Ecosia with the older (name, url, alias) API", async () => {
  let svc = fakeSearch({ legacy: true });
  await ensureEngine(svc, ECOSIA);
  assert.deepEqual(svc.calls[0], ["addUserEngine", { name: "Ecosia", url: ECOSIA.url, alias: "@ecosia" }]);
});

test("fails loudly if the engine cannot be added", async () => {
  let svc = fakeSearch();
  svc.addUserEngine = async () => {};
  await assert.rejects(ensureEngine(svc, ECOSIA), /Could not add/);
});

test("Evergreen's Ecosia entry carries no partner code", () => {
  for (let url of [ECOSIA.url, ECOSIA.suggestUrl]) {
    let u = new URL(url.replace("{searchTerms}", "x"));
    assert.equal(u.protocol, "https:");
    assert.equal(u.searchParams.has("tt"), false);
    assert.equal(u.searchParams.get("q"), "x");
  }
});

test("remembers the engine it made the default", async () => {
  let svc = fakeSearch({ engines: ["Google"], defaultName: "Google" });
  let managed = fakeManaged();
  let { engine } = await applyDefaultSearch(svc, 0, managed);
  assert.equal(managed.get(), engine.id);
  assert.equal(svc.defaultEngine.name, "Ecosia");
});

test("keeps Ecosia the default when Firefox swaps in its own Ecosia", async () => {
  let svc = fakeSearch({ engines: ["Google"], defaultName: "Google" });
  let managed = fakeManaged();
  await applyDefaultSearch(svc, 0, managed);
  // Firefox learns the region, adds its own Ecosia, drops Evergreen's
  // duplicate and falls back to Google.
  svc.replaceWithAppEngine("Ecosia", "Google");
  assert.equal(await reconcileDefaultSearch(svc, 0, managed), true);
  assert.equal(svc.defaultEngine.name, "Ecosia");
  assert.equal(managed.get(), svc.getEngineByName("Ecosia").id);
  assert.equal(svc.calls.filter(c => c[0] == "addUserEngine").length, 1, "no second copy added");
});

test("stops managing the default once the user picks another engine", async () => {
  let svc = fakeSearch({ engines: ["Google", "DuckDuckGo"], defaultName: "Google" });
  let managed = fakeManaged();
  await applyDefaultSearch(svc, 0, managed);
  await svc.setDefault(svc.getEngineByName("DuckDuckGo"), 1);
  assert.equal(await reconcileDefaultSearch(svc, 0, managed), false);
  assert.equal(managed.get(), "");
  // Later engine changes leave the user's choice alone.
  svc.replaceWithAppEngine("Ecosia", "DuckDuckGo");
  assert.equal(await reconcileDefaultSearch(svc, 0, managed), false);
  assert.equal(svc.defaultEngine.name, "DuckDuckGo");
});

test("does nothing while its engine is still the default", async () => {
  let svc = fakeSearch({ engines: ["Google"], defaultName: "Google" });
  let managed = fakeManaged();
  await applyDefaultSearch(svc, 0, managed);
  let before = svc.calls.length;
  assert.equal(await reconcileDefaultSearch(svc, 0, managed), false);
  assert.equal(svc.calls.length, before);
});

test("leaves the default alone when the user removed Ecosia", async () => {
  let svc = fakeSearch({ engines: ["Google"], defaultName: "Google" });
  let managed = fakeManaged();
  await applyDefaultSearch(svc, 0, managed);
  svc.list.splice(svc.list.findIndex(e => e.name == "Ecosia"), 1);
  svc.defaultEngine = svc.getEngineByName("Google");
  assert.equal(await reconcileDefaultSearch(svc, 0, managed), false);
  assert.equal(svc.defaultEngine.name, "Google");
  assert.equal(managed.get(), "");
});
