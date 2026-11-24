/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

import assert from "node:assert/strict";
import { test } from "node:test";

import * as D from "../../src/browser/components/evergreen/DownloadPlan.sys.mjs";

const { MiB } = D;

test("the connection count follows the wish, the size and the limits", () => {
  assert.equal(D.chooseConnections(100 * MiB, 6), 6);
  assert.equal(D.chooseConnections(100 * MiB, 99), D.LIMITS.maxConnections);
  assert.equal(D.chooseConnections(100 * MiB, 0), 1);
  assert.equal(D.chooseConnections(5 * MiB, 6), 2); // pieces of at least 2 MiB
  assert.equal(D.chooseConnections(1 * MiB, 6), 1);
  assert.equal(D.chooseConnections(-1, 6), 1);
  assert.equal(D.chooseConnections(100 * MiB, "x"), 1);
});

test("retry pauses grow, are capped, and are spread by jitter", () => {
  assert.equal(D.backoffMs(0, 0), 500);
  assert.equal(D.backoffMs(1, 0), 1000);
  assert.equal(D.backoffMs(2, 0), 2000);
  assert.equal(D.backoffMs(20, 0), D.LIMITS.maxDelayMs);
  assert.ok(D.backoffMs(1, 1) > D.backoffMs(1, 0));
  assert.ok(D.backoffMs(1, 1) <= 1250);
});

test("a file is split into contiguous segments that cover it exactly", () => {
  for (let [total, n] of [[100, 3], [101, 4], [8 * MiB, 4], [7, 7], [1, 1]]) {
    let segs = D.splitRange(total, n);
    assert.equal(segs.length, n);
    assert.ok(D.SegmentPlan.coversExactly(segs, total), `${total}/${n}`);
  }
});

test("a plan hands out each free segment once, to different lanes", () => {
  let plan = new D.SegmentPlan(40 * MiB, { connections: 4 });
  let got = [0, 1, 2, 3].map(i => plan.next("lane" + i));
  assert.equal(new Set(got).size, 4);
  assert.equal(plan.active, 4);
  // The cap is reached, nothing more is offered.
  assert.equal(plan.next("lane4"), null);
});

test("a finished lane takes the upper half of the biggest remaining segment", () => {
  let plan = new D.SegmentPlan(40 * MiB, { connections: 2 });
  let a = plan.next("a");
  let b = plan.next("b");
  plan.advance(a, a.end - a.start); // a is done
  plan.release(a);
  assert.equal(plan.complete, false);
  let stolen = plan.next("a");
  assert.ok(stolen !== b);
  // It took the upper half of b's range and b now ends where it starts.
  assert.equal(stolen.end, 40 * MiB);
  assert.equal(b.end, stolen.start);
  assert.ok(D.SegmentPlan.coversExactly(plan.segments, 40 * MiB));
  assert.ok(Math.abs((stolen.end - stolen.start) - (b.end - b.start)) <= 1);
});

test("a segment too small to be worth splitting is not split", () => {
  let plan = new D.SegmentPlan(8 * MiB, { connections: 2 });
  let a = plan.next("a");
  let b = plan.next("b");
  plan.advance(a, a.end - a.start);
  plan.release(a);
  plan.advance(b, b.end - b.start - 3 * MiB); // 3 MiB left, under 2 * 2 MiB
  assert.equal(plan.next("a"), null);
});

test("progress never exceeds a segment and the file is complete only when all are", () => {
  let plan = new D.SegmentPlan(10 * MiB, { connections: 2 });
  let a = plan.next("a");
  let b = plan.next("b");
  assert.equal(plan.advance(a, 100 * MiB), a.end - a.start);
  assert.equal(plan.complete, false);
  assert.equal(plan.advance(b, 100 * MiB), b.end - b.start);
  assert.equal(plan.complete, true);
  assert.equal(plan.doneBytes, 10 * MiB);
  assert.equal(plan.advance(b, 5), 0);
});

test("a released segment is picked up again where it stopped", () => {
  let plan = new D.SegmentPlan(10 * MiB, { connections: 2 });
  let a = plan.next("a");
  plan.advance(a, 1000);
  plan.release(a);
  let again = plan.next("c");
  assert.equal(again, a);
  assert.equal(plan.position(a), a.start + 1000);
  assert.equal(plan.rangeHeader(a), `bytes=${a.start + 1000}-${a.end - 1}`);
});

test("a server that pushes back lowers the cap, never below one", () => {
  let plan = new D.SegmentPlan(100 * MiB, { connections: 4 });
  for (let i = 0; i < 4; i++) {
    plan.next("l" + i);
  }
  plan.throttle();
  assert.equal(plan.cap, 3);
  plan.throttle();
  assert.equal(plan.cap, 2);
  for (let i = 0; i < 10; i++) {
    plan.throttle();
  }
  assert.equal(plan.cap, 1);
});

test("saved state round-trips and is refused when it does not fit", () => {
  let plan = new D.SegmentPlan(20 * MiB, { connections: 3 });
  let a = plan.next("a");
  plan.advance(a, 12345);
  let saved = D.saveState({ url: "https://x/f", total: 20 * MiB, validator: '"abc"', plan });
  saved = JSON.parse(JSON.stringify(saved));
  assert.ok(D.loadState(saved, "https://x/f"));
  let restored = new D.SegmentPlan(20 * MiB, { connections: 3, segments: saved.segments });
  assert.equal(restored.doneBytes, 12345);
  assert.equal(restored.active, 0);

  assert.equal(D.loadState(saved, "https://x/other"), null);
  assert.equal(D.loadState(null, "https://x/f"), null);
  assert.equal(D.loadState({ ...saved, v: 2 }, "https://x/f"), null);
  assert.equal(D.loadState({ ...saved, total: 21 * MiB }, "https://x/f"), null);
  assert.equal(D.loadState({ ...saved, segments: [[0, 5, 9]] }, "https://x/f"), null);
  assert.equal(D.loadState({ ...saved, segments: "x" }, "https://x/f"), null);
  assert.throws(() => new D.SegmentPlan(20 * MiB, { segments: [[0, 5, 0]] }), RangeError);
});

test("Content-Range is parsed strictly", () => {
  assert.deepEqual(D.parseContentRange("bytes 10-19/100"), { start: 10, end: 19, total: 100 });
  assert.deepEqual(D.parseContentRange("bytes 0-0/*"), { start: 0, end: 0, total: null });
  for (let bad of ["", null, "bytes */100", "bytes 20-10/100", "items 1-2/3", "bytes 1-2", "bytes a-b/3"]) {
    assert.equal(D.parseContentRange(bad), null, String(bad));
  }
});

test("range replies are judged against what was asked for", () => {
  let ask = { total: 100, position: 10, last: 19, hadValidator: true };
  let judge = o => D.judgeRangeReply({ ...ask, ...o });
  assert.equal(judge({ status: 206, contentRange: "bytes 10-19/100" }), "ok");
  assert.equal(judge({ status: 206, contentRange: "bytes 10-19/*" }), "ok");
  assert.equal(judge({ status: 206, contentRange: "bytes 11-19/100" }), "bad");
  assert.equal(judge({ status: 206, contentRange: "bytes 10-20/100" }), "bad");
  assert.equal(judge({ status: 206, contentRange: "bytes 10-19/101" }), "changed");
  assert.equal(judge({ status: 206, contentRange: null }), "bad");
  assert.equal(judge({ status: 200 }), "changed"); // If-Range failed: new file
  assert.equal(judge({ status: 200, hadValidator: false }), "no-ranges");
  assert.equal(judge({ status: 503 }), "refused");
  assert.equal(judge({ status: 429 }), "refused");
  assert.equal(judge({ status: 403 }), "bad");
  assert.equal(judge({ status: 416 }), "bad");
});

test("If-Range uses a strong ETag, else the date, else nothing", () => {
  assert.equal(D.validatorFor({ etag: '"v1"', lastModified: "Tue, 1 Jan 2030 00:00:00 GMT" }), '"v1"');
  assert.equal(D.validatorFor({ etag: 'W/"v1"', lastModified: "Tue, 1 Jan 2030 00:00:00 GMT" }), "Tue, 1 Jan 2030 00:00:00 GMT");
  assert.equal(D.validatorFor({ etag: 'W/"v1"' }), null);
  assert.equal(D.validatorFor({}), null);
  assert.equal(D.validatorFor(), null);
});

test("excluded hosts match exactly or as a subdomain, not as a suffix of text", () => {
  assert.equal(D.hostExcluded("cdn.example.com", "example.com"), true);
  assert.equal(D.hostExcluded("example.com", " Example.com , other.org"), true);
  assert.equal(D.hostExcluded("*.example.com".slice(2), "*.example.com"), true);
  assert.equal(D.hostExcluded("badexample.com", "example.com"), false);
  assert.equal(D.hostExcluded("example.com", ""), false);
  assert.equal(D.hostExcluded("", "example.com"), false);
});

test("only large, splittable, plain HTTP downloads are taken over", () => {
  let good = {
    scheme: "https", method: "GET", status: 200, acceptRanges: "bytes",
    contentLength: 100 * MiB, contentEncoding: "", host: "files.example.org",
  };
  assert.equal(D.takeoverVerdict(good, {}), null);
  let reason = (change, settings = {}) => D.takeoverVerdict({ ...good, ...change }, settings);
  assert.equal(reason({}, { enabled: false }), "off");
  assert.equal(reason({ scheme: "ftp" }), "not http");
  assert.equal(reason({ method: "POST" }), "not GET");
  assert.equal(reason({ status: 206 }), "status 206");
  assert.equal(reason({ acceptRanges: "none" }), "no byte ranges");
  assert.equal(reason({ acceptRanges: undefined }), "no byte ranges");
  assert.equal(reason({ contentEncoding: "gzip" }), "content-encoding");
  assert.equal(reason({ contentEncoding: "identity" }), null);
  assert.equal(reason({ contentLength: 1 * MiB }), "small or unknown size");
  assert.equal(reason({ contentLength: -1 }), "small or unknown size");
  assert.equal(reason({ contentLength: 100 * MiB }, { maxSizeBytes: 50 * MiB }), "too large");
  assert.equal(reason({}, { excludeHosts: "example.org" }), "host excluded");
  assert.equal(reason({ contentLength: 4 * MiB }, { minSizeBytes: 2 * MiB }), null);
});

test("Limiter: places are handed out in order and released", async () => {
  let limiter = new D.Limiter(2);
  let a = limiter.acquire();
  let b = limiter.acquire();
  let c = limiter.acquire();
  let releaseA = await a.promise;
  await b.promise;
  assert.equal(limiter.active, 2);
  assert.equal(limiter.waiting, 1);
  let done = false;
  c.promise.then(() => (done = true));
  await Promise.resolve();
  assert.equal(done, false);
  releaseA();
  releaseA(); // releasing twice frees one place only
  await c.promise;
  assert.equal(limiter.active, 2);
});

test("Limiter: a canceled request leaves the queue", async () => {
  let limiter = new D.Limiter(1);
  let first = limiter.acquire();
  let release = await first.promise;
  let second = limiter.acquire();
  let third = limiter.acquire();
  second.cancel();
  await assert.rejects(second.promise, /canceled/);
  assert.equal(limiter.waiting, 1);
  release();
  await third.promise;
  assert.equal(limiter.active, 1);
});

test("Limiter: 0 is unlimited, and raising the limit wakes the queue", async () => {
  let unlimited = new D.Limiter(0);
  await Promise.all(Array.from({ length: 50 }, () => unlimited.acquire().promise));
  assert.equal(unlimited.active, 50);
  let limiter = new D.Limiter(1);
  await limiter.acquire().promise;
  let queued = limiter.acquire();
  limiter.setLimit(2);
  await queued.promise;
  assert.equal(limiter.active, 2);
});
