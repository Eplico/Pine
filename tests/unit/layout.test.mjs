/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

import assert from "node:assert/strict";
import { test } from "node:test";

const { centeredPosition } = await import(
  "../../src/browser/components/evergreen/EvergreenLayout.sys.mjs"
);

const screen = { x: 0, y: 0, width: 1920, height: 1080 };

test("a popup is centred over the window that opened it", () => {
  let parent = { x: 100, y: 50, width: 1000, height: 800 };
  let spot = centeredPosition(parent, { width: 400, height: 600 }, screen);
  assert.deepEqual(spot, { x: 400, y: 150 });
});

test("a popup stays on the screen when its opener is at the edge", () => {
  let parent = { x: 1700, y: 900, width: 400, height: 400 };
  let spot = centeredPosition(parent, { width: 600, height: 500 }, screen);
  assert.deepEqual(spot, { x: 1320, y: 580 });
  parent = { x: -300, y: -200, width: 400, height: 400 };
  spot = centeredPosition(parent, { width: 600, height: 500 }, screen);
  assert.deepEqual(spot, { x: 0, y: 0 });
});

test("a popup bigger than the screen goes to its top left", () => {
  let parent = { x: 0, y: 0, width: 800, height: 600 };
  let spot = centeredPosition(parent, { width: 3000, height: 2000 }, screen);
  assert.deepEqual(spot, { x: 0, y: 0 });
});

test("the screen's usable area may start away from the origin", () => {
  let area = { x: 1920, y: 40, width: 1920, height: 1040 };
  let parent = { x: 1900, y: 0, width: 200, height: 200 };
  let spot = centeredPosition(parent, { width: 400, height: 300 }, area);
  assert.deepEqual(spot, { x: 1920, y: 40 });
});

test("positions are whole pixels", () => {
  let parent = { x: 0, y: 0, width: 1001, height: 801 };
  let spot = centeredPosition(parent, { width: 400, height: 300 }, screen);
  assert.ok(Number.isInteger(spot.x) && Number.isInteger(spot.y), JSON.stringify(spot));
});
