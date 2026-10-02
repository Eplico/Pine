/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

import assert from "node:assert/strict";
import { test } from "node:test";

import * as S from "../../src/browser/components/evergreen/Spaces.sys.mjs";

const NOW = 1_800_000_000_000;

function ids(...list) {
  let i = 0;
  return () => list[i++];
}

test("default state has one unnamed Space on the default identity", () => {
  let state = S.defaultState(NOW);
  assert.equal(state.spaces.length, 1);
  assert.deepEqual(state.spaces[0], {
    id: "personal",
    name: "",
    color: "green",
    userContextId: 0,
    createdAt: NOW,
  });
});

test("normalizeState repairs whatever is on disk", () => {
  let state = S.normalizeState(
    {
      spaces: [
        { id: "a", name: "  Work\u0007  ", color: "nope", userContextId: 3 },
        { id: "a", name: "duplicate" },
        { id: "", name: "no id" },
        null,
        "junk",
        { id: "b", name: "x".repeat(100), userContextId: -4, color: "blue" },
      ],
    },
    NOW
  );
  assert.deepEqual(
    state.spaces.map(s => [s.id, s.name, s.color, s.userContextId]),
    [
      ["a", "Work", "green", 3],
      ["b", "x".repeat(S.MAX_NAME_LENGTH), "blue", 0],
    ]
  );
  assert.deepEqual(S.normalizeState(null, NOW), S.defaultState(NOW));
  assert.deepEqual(S.normalizeState({ spaces: [] }, NOW), S.defaultState(NOW));
});

test("normalizeState caps the number of Spaces", () => {
  let spaces = Array.from({ length: S.MAX_SPACES + 5 }, (_, i) => ({ id: `s${i}` }));
  assert.equal(S.normalizeState({ spaces }, NOW).spaces.length, S.MAX_SPACES);
});

test("addSpace appends with a fresh id, retrying collisions", () => {
  let state = S.defaultState(NOW);
  let { state: next, space } = S.addSpace(
    state,
    { name: "Work", color: "blue", userContextId: 7 },
    { idFactory: ids("personal", "s-work"), now: NOW }
  );
  assert.equal(space.id, "s-work");
  assert.equal(next.spaces.length, 2);
  assert.equal(state.spaces.length, 1, "input state is not mutated");
  assert.equal(S.getSpace(next, "s-work").userContextId, 7);
});

test("updateSpace changes name and color but never the identity", () => {
  let { state } = S.addSpace(S.defaultState(NOW), { name: "Work", color: "blue", userContextId: 7 }, {
    idFactory: ids("w"),
  });
  let next = S.updateSpace(state, "w", { name: "Job", color: "red", userContextId: 0 });
  assert.deepEqual(
    [S.getSpace(next, "w").name, S.getSpace(next, "w").color, S.getSpace(next, "w").userContextId],
    ["Job", "red", 7]
  );
  assert.equal(S.getSpace(S.updateSpace(state, "w", { color: "bogus" }), "w").color, "blue");
  assert.throws(() => S.updateSpace(state, "missing", { name: "x" }));
});

test("removeSpace refuses to delete the last Space", () => {
  let state = S.defaultState(NOW);
  assert.throws(() => S.removeSpace(state, "personal"), /last Space/);
  let { state: two } = S.addSpace(state, { name: "W" }, { idFactory: ids("w") });
  assert.deepEqual(S.removeSpace(two, "personal").spaces.map(s => s.id), ["w"]);
  assert.throws(() => S.removeSpace(two, "missing"));
});

test("moveSpace clamps the target index", () => {
  let state = { spaces: ["a", "b", "c"].map(id => ({ id })) };
  assert.deepEqual(S.moveSpace(state, "a", 99).spaces.map(s => s.id), ["b", "c", "a"]);
  assert.deepEqual(S.moveSpace(state, "c", -3).spaces.map(s => s.id), ["c", "a", "b"]);
});

test("neighbor and fallback Spaces", () => {
  let state = { spaces: ["a", "b", "c"].map(id => ({ id })) };
  assert.equal(S.neighborSpaceId(state, "c", 1), "a");
  assert.equal(S.neighborSpaceId(state, "a", -1), "c");
  assert.equal(S.neighborSpaceId(state, "b", 4), "c");
  assert.equal(S.fallbackSpaceId(state, "b"), "a");
  assert.equal(S.fallbackSpaceId(state, "a"), "b");
  assert.equal(S.fallbackSpaceId({ spaces: [{ id: "a" }] }, "a"), null);
});

test("identityInUse looks at other Spaces only", () => {
  let state = {
    spaces: [
      { id: "a", userContextId: 0 },
      { id: "b", userContextId: 5 },
    ],
  };
  assert.equal(S.identityInUse(state, 5), true);
  assert.equal(S.identityInUse(state, 5, "b"), false);
});

test("cleanName strips control characters and trims", () => {
  assert.equal(S.cleanName("\u0000 Side\nproject \u001f"), "Sideproject");
  assert.equal(S.cleanName(undefined), "");
});

test("spaceColor falls back to green", () => {
  assert.equal(S.spaceColor({ color: "blue" }), S.SPACE_COLORS.blue);
  assert.equal(S.spaceColor({ color: "nope" }), S.SPACE_COLORS.green);
  assert.equal(S.spaceColor(null), S.SPACE_COLORS.green);
});
