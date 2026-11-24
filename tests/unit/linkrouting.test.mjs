/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  MAX_RULES,
  addRule,
  parseRules,
  pruneRules,
  removeRule,
  routeFor,
  ruleFor,
  serializeRules,
  siteOf,
} from "../../src/browser/components/evergreen/LinkRouting.sys.mjs";

test("siteOf: http(s) hosts only, without www", () => {
  assert.equal(siteOf("https://www.Example.com/a?b"), "example.com");
  assert.equal(siteOf("http://mail.example.com:8080/"), "mail.example.com");
  assert.equal(siteOf("about:robots"), null);
  assert.equal(siteOf("file:///etc/passwd"), null);
  assert.equal(siteOf("javascript:alert(1)"), null);
  assert.equal(siteOf("https://localhost/"), null); // not a site that can be routed
  assert.equal(siteOf("nonsense"), null);
});

test("parseRules drops invalid and duplicate rules", () => {
  let rules = parseRules(
    JSON.stringify([
      { host: "github.com", spaceId: "w" },
      { host: "GitHub.com", spaceId: "x" },
      { host: "www.example.org", spaceId: "p" },
      { host: "bad host", spaceId: "p" },
      { host: "single", spaceId: "p" },
      { host: "ok.example", spaceId: "" },
      null,
      "text",
    ])
  );
  assert.deepEqual(rules, [
    { host: "github.com", spaceId: "w" },
    { host: "example.org", spaceId: "p" },
  ]);
  assert.deepEqual(parseRules("not json"), []);
  assert.deepEqual(parseRules('{"a":1}'), []);
  assert.deepEqual(parseRules(""), []);
});

test("rules are capped", () => {
  let many = Array.from({ length: MAX_RULES + 20 }, (_, i) => ({ host: `site${i}.example`, spaceId: "w" }));
  assert.equal(parseRules(JSON.stringify(many)).length, MAX_RULES);
});

test("a rule matches its host and subdomains, the longest wins", () => {
  let rules = [
    { host: "example.com", spaceId: "a" },
    { host: "docs.example.com", spaceId: "b" },
  ];
  assert.equal(ruleFor("https://example.com/", rules).spaceId, "a");
  assert.equal(ruleFor("https://www.example.com/", rules).spaceId, "a");
  assert.equal(ruleFor("https://mail.example.com/", rules).spaceId, "a");
  assert.equal(ruleFor("https://docs.example.com/x", rules).spaceId, "b");
  assert.equal(ruleFor("https://notexample.com/", rules), null);
  assert.equal(ruleFor("https://example.com.evil.net/", rules), null);
  assert.equal(ruleFor("about:blank", rules), null);
});

test("routeFor ignores a rule whose Space is gone", () => {
  let rules = [{ host: "example.com", spaceId: "gone" }];
  assert.equal(routeFor("https://example.com/", rules, ["a", "b"]), null);
  assert.equal(routeFor("https://example.com/", rules, ["gone"]), "gone");
});

test("addRule replaces, removeRule removes, pruneRules drops lost Spaces", () => {
  let rules = addRule([], "www.Example.com", "a");
  rules = addRule(rules, "example.com", "b");
  assert.deepEqual(rules, [{ host: "example.com", spaceId: "b" }]);
  assert.throws(() => addRule(rules, "localhost", "a"));
  assert.throws(() => addRule(rules, "example.org", ""));
  rules = addRule(rules, "other.net", "c");
  assert.deepEqual(removeRule(rules, "example.com"), [{ host: "other.net", spaceId: "c" }]);
  assert.deepEqual(pruneRules(rules, ["c"]), [{ host: "other.net", spaceId: "c" }]);
});

test("rules survive a round trip through the pref's text", () => {
  let rules = [{ host: "example.com", spaceId: "a" }];
  assert.deepEqual(parseRules(serializeRules(rules)), rules);
});
