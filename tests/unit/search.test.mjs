/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ECOSIA,
  applyDefaultSearch,
  ensureEngine,
} from "../../src/browser/components/evergreen/SearchDefaults.sys.mjs";

/** A fake of the bits of Services.search that SearchDefaults uses. */
function fakeSearch({ engines = [], legacy = false } = {}) {
  let calls = [];
  let list = engines.map(name => ({ name }));
  let svc = {
    calls,
    getEngineByName: name => list.find(e => e.name == name) ?? null,
    async setDefault(engine, reason) {
      calls.push(["setDefault", engine.name, reason]);
    },
  };
  svc.addUserEngine = legacy
    ? async function (name, url, alias) {
        calls.push(["addUserEngine", { name, url, alias }]);
        list.push({ name });
      }
    : async function (formInfo) {
        calls.push(["addUserEngine", formInfo]);
        list.push({ name: formInfo.name });
      };
  return svc;
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
