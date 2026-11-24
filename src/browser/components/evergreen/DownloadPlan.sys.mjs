/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

/**
 * The logic of Evergreen's accelerated downloads, with no dependency on
 * Firefox (unit-tested in Node): how a file is split into byte ranges, how a
 * finished connection takes over part of another's range, what a range reply
 * must look like, when to retry, what is saved to resume, and which downloads
 * to take over. See docs/download-manager.md.
 */

export const MiB = 1024 * 1024;

export const LIMITS = {
  maxConnections: 16,
  // A range is never split below this: more connections than this many
  // pieces would only add requests.
  minSegment: 2 * MiB,
  maxAttempts: 6,
  baseDelayMs: 500,
  maxDelayMs: 30000,
};

/**
 * A counting limit with a first-come queue, used for the downloads that run at
 * once and for the connections open across all downloads. A limit of 0 or less
 * means no limit. `acquire()` returns { promise, cancel }: the promise resolves
 * with a release function when a place is free; `cancel()` takes the request
 * out of the queue (the promise then rejects).
 */
export class Limiter {
  constructor(limit = 0) {
    this.limit = limit;
    this.active = 0;
    this.peak = 0; // the most places held at once since the last reset
    this.waiters = [];
  }

  get waiting() {
    return this.waiters.length;
  }

  setLimit(limit) {
    this.limit = Number.isFinite(limit) ? limit : 0;
    this._pump();
  }

  acquire() {
    let waiter = { resolve: null, reject: null, settled: false };
    let promise = new Promise((resolve, reject) => {
      waiter.resolve = resolve;
      waiter.reject = reject;
    });
    this.waiters.push(waiter);
    this._pump();
    return {
      promise,
      cancel: () => {
        if (waiter.settled) {
          return;
        }
        waiter.settled = true;
        this.waiters = this.waiters.filter(w => w !== waiter);
        waiter.reject(new Error("canceled"));
      },
    };
  }

  _pump() {
    while (this.waiters.length && (this.limit <= 0 || this.active < this.limit)) {
      let waiter = this.waiters.shift();
      waiter.settled = true;
      this.active++;
      this.peak = Math.max(this.peak, this.active);
      let released = false;
      waiter.resolve(() => {
        if (!released) {
          released = true;
          this.active--;
          this._pump();
        }
      });
    }
  }
}

/** The number of connections for a file: the wish, clipped to what makes sense. */
export function chooseConnections(total, wanted, minSegment = LIMITS.minSegment) {
  let want = Math.max(1, Math.min(LIMITS.maxConnections, Math.floor(Number(wanted)) || 1));
  if (!(total > 0)) {
    return 1;
  }
  return Math.max(1, Math.min(want, Math.floor(total / minSegment)));
}

/** Milliseconds to wait before attempt number `attempt` (0 is the first retry). */
export function backoffMs(attempt, jitter = Math.random()) {
  let base = Math.min(LIMITS.maxDelayMs, LIMITS.baseDelayMs * 2 ** Math.max(0, attempt));
  // Up to a quarter more, so many connections do not retry in step.
  return Math.round(base * (1 + 0.25 * jitter));
}

/**
 * Splits [0, total) into `count` contiguous ranges. A segment is
 * { start, end, done, owner } where end is exclusive and done counts the bytes
 * already stored from start.
 */
export function splitRange(total, count) {
  let segments = [];
  let size = Math.floor(total / count);
  for (let i = 0; i < count; i++) {
    let start = i * size;
    let end = i == count - 1 ? total : start + size;
    segments.push({ start, end, done: 0, owner: null });
  }
  return segments;
}

/**
 * The work of one download: a set of segments that together cover the file,
 * handed out to connections ("lanes").
 */
export class SegmentPlan {
  /**
   * @param {number} total       File size in bytes.
   * @param {object} options
   * @param {number} options.connections  Connections wanted.
   * @param {number} [options.minSegment]
   * @param {Array}  [options.segments]   Saved [start, end, done] triples to resume from.
   */
  constructor(total, { connections = 4, minSegment = LIMITS.minSegment, segments = null } = {}) {
    this.total = total;
    this.minSegment = minSegment;
    this.connections = chooseConnections(total, connections, minSegment);
    // The most connections that may run at once; a server that pushes back
    // lowers it (see throttle()).
    this.cap = this.connections;
    if (segments) {
      this.segments = segments.map(([start, end, done]) => ({ start, end, done, owner: null }));
      if (!SegmentPlan.coversExactly(this.segments, total)) {
        throw new RangeError("saved segments do not cover the file");
      }
    } else {
      this.segments = splitRange(total, this.connections);
    }
  }

  /** True when the segments are contiguous, in order, and span [0, total). */
  static coversExactly(segments, total) {
    let at = 0;
    for (let s of segments) {
      if (!(s.start == at && s.end > s.start && s.done >= 0 && s.done <= s.end - s.start)) {
        return false;
      }
      at = s.end;
    }
    return at == total;
  }

  get doneBytes() {
    return this.segments.reduce((sum, s) => sum + s.done, 0);
  }

  get complete() {
    return this.segments.every(s => s.done >= s.end - s.start);
  }

  /** Connections currently working on a segment. */
  get active() {
    return this.segments.filter(s => s.owner !== null).length;
  }

  remaining(s) {
    return s.end - s.start - s.done;
  }

  /**
   * The next piece of work for a lane that has none: a segment nobody works on
   * that is not finished, else half of the segment with the most left (if
   * that is worth a request). Returns null when there is nothing to do, or the
   * lane should stay idle because the cap is reached.
   */
  next(owner) {
    if (this.active >= this.cap) {
      return null;
    }
    let free = this.segments.find(s => s.owner === null && this.remaining(s) > 0);
    if (free) {
      free.owner = owner;
      return free;
    }
    let victim = null;
    for (let s of this.segments) {
      if (s.owner !== null && (!victim || this.remaining(s) > this.remaining(victim))) {
        victim = s;
      }
    }
    if (!victim || this.remaining(victim) < 2 * this.minSegment) {
      return null;
    }
    // The lower half stays with its connection; the upper half is new. The
    // old connection stops at its new end (see `end`), at most a few chunks
    // that are already on the wire are fetched twice.
    let from = victim.start + victim.done;
    let mid = from + Math.floor((victim.end - from) / 2);
    let created = { start: mid, end: victim.end, done: 0, owner };
    victim.end = mid;
    this.segments.splice(this.segments.indexOf(victim) + 1, 0, created);
    return created;
  }

  /** Stop handing out more than `active - 1` connections after a refusal. */
  throttle() {
    this.cap = Math.max(1, Math.min(this.cap, this.active) - 1);
  }

  /** Record `n` bytes stored for the segment. Returns how many it could take (it never grows past its end). */
  advance(segment, n) {
    let room = this.remaining(segment);
    let took = Math.max(0, Math.min(n, room));
    segment.done += took;
    return took;
  }

  /** A lane gives a segment back (failed or stopped); it can be taken again. */
  release(segment) {
    segment.owner = null;
  }

  /** The first byte this segment still needs. */
  position(segment) {
    return segment.start + segment.done;
  }

  /** The Range header for the rest of a segment. */
  rangeHeader(segment) {
    return `bytes=${this.position(segment)}-${segment.end - 1}`;
  }

  /** Merges nothing and invents nothing: the saved form, for resuming. */
  toJSON() {
    return this.segments.map(s => [s.start, s.end, s.done]);
  }
}

/** Parses "bytes 10-19/100" (or "bytes 10-19/*"). Returns null if malformed. */
export function parseContentRange(value) {
  let m = /^\s*bytes\s+(\d+)-(\d+)\/(\d+|\*)\s*$/i.exec(value ?? "");
  if (!m) {
    return null;
  }
  let start = Number(m[1]);
  let end = Number(m[2]);
  if (end < start) {
    return null;
  }
  return { start, end, total: m[3] == "*" ? null : Number(m[3]) };
}

/**
 * Judges a reply to a request for bytes [position, last].
 *
 * @returns {"ok"|"changed"|"no-ranges"|"refused"|"bad"}
 *   ok: exactly the requested bytes of the same file.
 *   changed: the server sent the whole (new) file: the validator no longer matches.
 *   no-ranges: a 200 where a 206 was expected without a validator in play.
 *   refused: the server pushes back (429/503): use fewer connections, retry later.
 *   bad: anything else (errors, wrong range, wrong total).
 */
export function judgeRangeReply({ status, contentRange, total, position, last, hadValidator }) {
  if (status == 429 || status == 503) {
    return "refused";
  }
  if (status == 200) {
    return hadValidator ? "changed" : "no-ranges";
  }
  if (status != 206) {
    return "bad";
  }
  let r = parseContentRange(contentRange);
  if (!r || r.start != position || r.end != last) {
    return "bad";
  }
  if (r.total !== null && r.total != total) {
    return "changed";
  }
  return "ok";
}

/**
 * The value for an If-Range header: a strong ETag if there is one, else the
 * Last-Modified date. Null when the server gave neither (then a resume cannot
 * prove it is the same file, and does not try).
 */
export function validatorFor({ etag, lastModified } = {}) {
  if (etag && !/^\s*W\//i.test(etag)) {
    return etag.trim();
  }
  if (lastModified) {
    return lastModified.trim();
  }
  return null;
}

/** True if `host` is in the comma-separated list, or is a subdomain of an entry. */
export function hostExcluded(host, list) {
  host = String(host ?? "").toLowerCase();
  if (!host) {
    return false;
  }
  return String(list ?? "")
    .split(",")
    .map(s => s.trim().toLowerCase().replace(/^\*?\./, ""))
    .filter(Boolean)
    .some(entry => host == entry || host.endsWith("." + entry));
}

/**
 * Should the download be taken over? Takes what the first (single-connection)
 * reply showed. Returns null to take it over, or a short reason it is left
 * alone (shown in the console when `evergreen.downloads.log` is on).
 */
export function takeoverVerdict(info, settings) {
  let { enabled = true, minSizeBytes = 8 * MiB, maxSizeBytes = 64 * 1024 * MiB, excludeHosts = "" } = settings;
  if (!enabled) {
    return "off";
  }
  if (!/^https?$/.test(info.scheme ?? "")) {
    return "not http";
  }
  if (info.method && info.method != "GET") {
    return "not GET";
  }
  if (info.status != 200) {
    return "status " + info.status;
  }
  if (!/^bytes$/i.test((info.acceptRanges ?? "").trim())) {
    return "no byte ranges";
  }
  if (info.contentEncoding && !/^identity$/i.test(info.contentEncoding.trim())) {
    return "content-encoding";
  }
  if (!(info.contentLength >= minSizeBytes)) {
    return "small or unknown size";
  }
  if (info.contentLength > maxSizeBytes) {
    return "too large";
  }
  if (hostExcluded(info.host, excludeHosts)) {
    return "host excluded";
  }
  return null;
}

/** The form stored in the download's record (inside `downloads.json`). */
export function saveState({ url, total, validator, plan }) {
  return { v: 1, url, total, validator: validator ?? null, segments: plan.toJSON() };
}

/**
 * Reads a stored state. Returns null unless it is well formed and describes
 * the same URL (a download whose source was changed must not reuse it).
 */
export function loadState(state, url) {
  if (!state || state.v !== 1 || state.url !== url || !(state.total > 0) || !Array.isArray(state.segments)) {
    return null;
  }
  let ok = state.segments.every(s => Array.isArray(s) && s.length == 3 && s.every(Number.isSafeInteger));
  if (!ok) {
    return null;
  }
  let segments = state.segments.map(([start, end, done]) => ({ start, end, done }));
  return SegmentPlan.coversExactly(segments, state.total) ? state : null;
}
