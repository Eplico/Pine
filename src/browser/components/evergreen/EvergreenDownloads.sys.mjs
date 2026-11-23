/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

/**
 * Accelerated downloads: large files are fetched on several connections, each
 * taking a byte range, and written into one file; interrupted downloads
 * resume. Built into Evergreen on Firefox's own network stack (no helper
 * process, no local port, no cookie export). The design, the threat model and
 * the reasons are in docs/download-manager.md; the planning logic, which has
 * no Firefox dependencies, is in DownloadPlan.sys.mjs.
 *
 * How it hooks in, without patching Firefox: a view on the Downloads list sees
 * every new download. A browser download that Firefox started on a single
 * connection ("legacy" saver) and that qualifies is cancelled and restarted in
 * the same Download object with a SegmentedSaver, a DownloadCopySaver whose
 * execute() runs the connections. Everything after the bytes are on disk
 * (reputation check, move to the final name, hash, history) is Firefox's own
 * code, inherited.
 */

import {
  LIMITS,
  MiB,
  SegmentPlan,
  backoffMs,
  judgeRangeReply,
  loadState,
  saveState,
  takeoverVerdict,
  validatorFor,
} from "./DownloadPlan.sys.mjs";

const { Downloads } = ChromeUtils.importESModule("resource://gre/modules/Downloads.sys.mjs");
const { DownloadCopySaver, DownloadLegacySaver, DownloadError } = ChromeUtils.importESModule(
  "resource://gre/modules/DownloadCore.sys.mjs"
);
const { NetUtil } = ChromeUtils.importESModule("resource://gre/modules/NetUtil.sys.mjs");
const { FileUtils } = ChromeUtils.importESModule("resource://gre/modules/FileUtils.sys.mjs");
const { setTimeout, clearTimeout } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");

const PREF = "evergreen.downloads.";
const PART_SUFFIX = ".evergreen-part";
const PROGRESS_INTERVAL_MS = 200;
// How long to wait for the single-connection download's first reply before
// giving up on taking it over.
const REQUEST_WAIT_MS = 15000;
// PR_WRONLY | PR_CREATE_FILE: open for writing without truncating.
const OPEN_FLAGS = 0x02 | 0x08;

const settings = () => {
  let P = Services.prefs;
  return {
    enabled: P.getBoolPref(PREF + "enabled", true),
    connections: P.getIntPref(PREF + "connections", 6),
    minSizeBytes: P.getIntPref(PREF + "minSizeMB", 8) * MiB,
    maxSizeBytes: P.getIntPref(PREF + "maxSizeGB", 64) * 1024 * MiB,
    excludeHosts: P.getStringPref(PREF + "excludeHosts", ""),
  };
};

const log = (...args) => {
  if (Services.prefs.getBoolPref(PREF + "log", false)) {
    console.log("Evergreen downloads:", ...args);
  }
};

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

/** A failure of one request, carrying what the saver needs to decide what next. */
class RangeFailure extends Error {
  constructor(kind, detail = {}) {
    super(`${kind}${detail.status ? " " + detail.status : ""}`);
    this.kind = kind;
    Object.assign(this, detail);
  }
}

function openOutput(path, position) {
  let out = Cc["@mozilla.org/network/file-output-stream;1"].createInstance(Ci.nsIFileOutputStream);
  out.init(new FileUtils.File(path), OPEN_FLAGS, 0o644, 0);
  out.QueryInterface(Ci.nsISeekableStream).seek(Ci.nsISeekableStream.NS_SEEK_SET, position);
  return out;
}

/** Creates the file at its full size, so connections can write anywhere in it. */
function preallocate(path, total) {
  let out = openOutput(path, total);
  try {
    out.QueryInterface(Ci.nsISeekableStream).setEOF();
  } finally {
    out.close();
  }
}

function hexToBinaryString(hex) {
  let s = "";
  for (let i = 0; i + 1 < hex.length; i += 2) {
    s += String.fromCharCode(parseInt(hex.slice(i, i + 2), 16));
  }
  return s;
}

/** Reads and drops `count` bytes (the listener must consume what it is given). */
function drain(stream, count) {
  try {
    NetUtil.readInputStreamToString(stream, count);
  } catch {
    // The request was cancelled; nothing is left to read.
  }
}

function header(http, name) {
  try {
    return http.getResponseHeader(name);
  } catch {
    return null;
  }
}

/**
 * Downloads a file on several connections. State (`info`): the URL, the file's
 * size, the validator for If-Range, and, when resuming, the saved segments.
 */
export class SegmentedSaver extends DownloadCopySaver {
  constructor(info) {
    super();
    this._info = info; // { url, total, validator, segments|null }
    this._plan = null;
    this._lanes = new Set();
    this._canceled = false;
  }

  async execute(aSetProgressBytesFn, aSetPropertiesFn) {
    this._canceled = false;
    let download = this.download;
    let targetPath = download.target.path;
    let filePath = download.target.partFilePath || targetPath;

    if (!this.alreadyAddedToHistory) {
      this.addToHistory();
      this.alreadyAddedToHistory = true;
    }
    try {
      // Reserve the final name early, as Firefox's own saver does.
      await IOUtils.writeUTF8(targetPath, "", { mode: "appendOrCreate" });
    } catch (ex) {
      if (!DOMException.isInstance(ex)) {
        throw ex;
      }
      let error = new DownloadError({ message: ex.message });
      error.becauseTargetFailed = true;
      throw error;
    }
    if (this._canceled) {
      throw new DownloadError({ message: "Saver canceled." });
    }

    let { total, validator } = this._info;
    let want = settings().connections;
    let plan = null;
    // Resume only if the file on disk is the one the saved state describes and
    // the server can prove (If-Range) it still has the same file.
    let saved = this._plan ? this._plan.toJSON() : this._info.segments;
    if (saved && validator) {
      try {
        let stat = await IOUtils.stat(filePath);
        if (stat.size == total) {
          plan = new SegmentPlan(total, { connections: want, segments: saved });
        }
      } catch {
        // No usable file: start over below.
      }
    }
    let resumed = !!plan;
    if (!plan) {
      plan = new SegmentPlan(total, { connections: want });
      await IOUtils.remove(filePath, { ignoreAbsent: true });
      try {
        preallocate(filePath, total);
      } catch (ex) {
        let error = new DownloadError({ message: String(ex.message ?? ex) });
        error.becauseTargetFailed = true;
        throw error;
      }
    }
    this._plan = plan;
    this._filePath = filePath;
    this._setProgress = aSetProgressBytesFn;
    this._lastProgress = 0;
    this._failure = null;
    log("start", download.source.url, `${plan.connections} connections`, resumed ? `resuming at ${plan.doneBytes}` : "");
    EvergreenDownloads.note({ url: download.source.url, action: resumed ? "resume" : "segmented", connections: plan.connections });
    aSetProgressBytesFn(plan.doneBytes, total, plan.doneBytes > 0);

    await Promise.all(Array.from({ length: plan.connections }, (_, i) => this._lane(i)));

    if (this._canceled) {
      throw new DownloadError({ message: "Saver canceled." });
    }
    if (this._failure && !plan.complete) {
      let failure = this._failure;
      if (failure.kind == "target") {
        let error = new DownloadError({ message: failure.message });
        error.becauseTargetFailed = true;
        throw error;
      }
      if (["changed", "no-ranges", "bad"].includes(failure.kind) && plan.doneBytes == 0) {
        // The server does not behave like the first reply promised. Let Firefox
        // download it the ordinary way, as a "Retry" would.
        return this._fallback(failure.kind, aSetProgressBytesFn, aSetPropertiesFn);
      }
      throw new DownloadError(
        failure.result !== undefined
          ? { result: failure.result, inferCause: true }
          : { message: failure.message ?? failure.kind, becauseSourceFailed: true }
      );
    }

    aSetProgressBytesFn(total, total, true);
    // The finished file: size, hash (for Firefox's reputation check), then the
    // inherited reputation check and move to the final name.
    let stat = await IOUtils.stat(filePath);
    if (stat.size != total) {
      throw new DownloadError({ message: `File is ${stat.size} bytes, expected ${total}` });
    }
    this._sha256Hash = hexToBinaryString(await IOUtils.computeHexDigest(filePath, "sha256"));
    this._signatureInfo = [];
    this._finished = true;
    if (typeof super._checkReputationAndMove == "function") {
      await super._checkReputationAndMove(aSetPropertiesFn);
    } else if (download.target.partFilePath) {
      await IOUtils.move(download.target.partFilePath, targetPath);
    }
  }

  /** Hand the download to Firefox's own single-connection saver. */
  async _fallback(reason, aSetProgressBytesFn, aSetPropertiesFn) {
    log("falling back to the ordinary downloader:", reason);
    EvergreenDownloads.note({ url: this.download.source.url, action: "fallback", reason });
    await IOUtils.remove(this._filePath, { ignoreAbsent: true });
    let copy = new DownloadCopySaver();
    copy.download = this.download;
    copy.alreadyAddedToHistory = true;
    this.download.saver = copy;
    this._plan = null;
    await copy.execute(aSetProgressBytesFn, aSetPropertiesFn);
  }

  /** One connection's loop: take a segment, fetch it, take the next. */
  async _lane(index) {
    let plan = this._plan;
    let name = "lane" + index;
    while (!this._canceled && !this._failure) {
      let segment = plan.next(name);
      if (!segment) {
        return;
      }
      try {
        await this._fetchSegment(segment);
      } catch (e) {
        if (!this._canceled && !this._failure) {
          this._failure = e instanceof RangeFailure ? e : new RangeFailure("bad", { message: String(e?.message ?? e) });
          // One connection failing for good ends the download: stop the others.
          this._abortConnections();
        }
      } finally {
        plan.release(segment);
      }
    }
  }

  /** Fetches the rest of a segment, retrying a broken connection from where it stopped. */
  async _fetchSegment(segment) {
    let plan = this._plan;
    let attempt = 0;
    while (plan.remaining(segment) > 0) {
      if (this._canceled || this._failure) {
        return;
      }
      let before = plan.doneBytes;
      try {
        await this._request(segment);
        return;
      } catch (e) {
        if (this._canceled || this._failure) {
          return;
        }
        if (!(e instanceof RangeFailure) || !["network", "refused", "short"].includes(e.kind)) {
          throw e;
        }
        if (e.kind == "refused") {
          // The server is pushing back: use fewer connections from now on.
          plan.throttle();
        }
        attempt = plan.doneBytes > before ? 0 : attempt + 1;
        if (attempt >= LIMITS.maxAttempts) {
          throw e;
        }
        log("retry", e.message, `attempt ${attempt}`);
        await sleep(backoffMs(attempt));
      }
    }
  }

  _newChannel() {
    let download = this.download;
    let channel;
    if (download.source.loadingPrincipal) {
      channel = NetUtil.newChannel({
        uri: download.source.url,
        contentPolicyType: Ci.nsIContentPolicy.TYPE_SAVEAS_DOWNLOAD,
        loadingPrincipal: download.source.loadingPrincipal,
        // The system principal as the trigger keeps the request from being
        // mistaken for a third-party one (as in Firefox's own saver).
        triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal(),
        securityFlags: Ci.nsILoadInfo.SEC_ALLOW_CROSS_ORIGIN_SEC_CONTEXT_IS_NULL,
      });
    } else {
      channel = NetUtil.newChannel({
        uri: download.source.url,
        contentPolicyType: Ci.nsIContentPolicy.TYPE_SAVEAS_DOWNLOAD,
        loadUsingSystemPrincipal: true,
      });
    }
    if (channel instanceof Ci.nsIPrivateBrowsingChannel) {
      channel.setPrivate(download.source.isPrivate);
    }
    if (channel instanceof Ci.nsIHttpChannel) {
      if (download.source.referrerInfo) {
        channel.referrerInfo = download.source.referrerInfo;
      }
      if (download.source.cookieJarSettings) {
        channel.loadInfo.cookieJarSettings = download.source.cookieJarSettings;
      }
      if (download.source.authHeader) {
        try {
          channel.setRequestHeader("Authorization", download.source.authHeader, true);
        } catch {
          // Not a header Firefox lets us set; the request then goes without it.
        }
      }
    }
    if (download.source.userContextId) {
      // Origin attributes can only be replaced as a whole.
      channel.loadInfo.originAttributes = {
        ...channel.loadInfo.originAttributes,
        userContextId: download.source.userContextId,
      };
    }
    if (channel instanceof Ci.nsIHttpChannelInternal) {
      // Throttled like page loads and not cached.
      channel.channelIsForDownload = true;
      // Cookies are sent even when third-party cookies are blocked.
      channel.forceAllowThirdPartyCookie = true;
    }
    return channel;
  }

  /** One ranged request for the rest of `segment`. Resolves when the segment is complete. */
  _request(segment) {
    let plan = this._plan;
    let { total, validator } = this._info;
    let position = plan.position(segment);
    let last = segment.end - 1;
    let channel = this._newChannel();
    if (!(channel instanceof Ci.nsIHttpChannel)) {
      return Promise.reject(new RangeFailure("bad", { message: "not an HTTP request" }));
    }
    // Ranges are byte positions in the stored form: ask for the plain bytes.
    channel.setRequestHeader("Accept-Encoding", "identity", false);
    channel.setRequestHeader("Range", `bytes=${position}-${last}`, false);
    if (validator) {
      channel.setRequestHeader("If-Range", validator, false);
    }

    let out;
    try {
      out = openOutput(this._filePath, position);
    } catch (ex) {
      return Promise.reject(new RangeFailure("target", { message: String(ex.message ?? ex) }));
    }
    let input = Cc["@mozilla.org/binaryinputstream;1"].createInstance(Ci.nsIBinaryInputStream);
    let lane = { channel, request: null };
    this._lanes.add(lane);

    return new Promise((resolve, reject) => {
      let failure = null;
      let segmentDone = false;
      const cancel = req => req.cancel(Cr.NS_BINDING_ABORTED);

      let listener = {
        QueryInterface: ChromeUtils.generateQI(["nsIStreamListener", "nsIRequestObserver"]),

        onStartRequest: req => {
          lane.request = req;
          try {
            let http = req.QueryInterface(Ci.nsIHttpChannel);
            if (!Components.isSuccessCode(req.status)) {
              return; // onStopRequest reports it
            }
            let verdict = judgeRangeReply({
              status: http.responseStatus,
              contentRange: header(http, "Content-Range"),
              total,
              position,
              last,
              hadValidator: !!validator,
            });
            let encoding = header(http, "Content-Encoding");
            if (verdict == "ok" && encoding && !/^identity$/i.test(encoding)) {
              verdict = "bad";
            }
            if (verdict != "ok") {
              failure = new RangeFailure(verdict, { status: http.responseStatus });
              cancel(req);
            }
          } catch (e) {
            failure = new RangeFailure("bad", { message: String(e?.message ?? e) });
            cancel(req);
          }
        },

        onDataAvailable: (req, stream, offset, count) => {
          if (this._canceled || failure || segmentDone) {
            drain(stream, count);
            cancel(req);
            return;
          }
          // Never write past the segment's current end (it may have been
          // shortened when another connection took over its upper half).
          let room = segment.end - plan.position(segment);
          let take = Math.max(0, Math.min(count, room));
          try {
            if (take > 0) {
              // (nsIFileOutputStream has no writeFrom: copy through a byte string.)
              input.setInputStream(stream);
              let written = out.write(input.readBytes(take), take);
              if (written != take) {
                throw new Error(`short write: ${written} of ${take}`);
              }
              plan.advance(segment, take);
            }
          } catch (e) {
            failure = new RangeFailure("target", { message: String(e?.message ?? e) });
            cancel(req);
            return;
          }
          if (take < count) {
            drain(stream, count - take);
          }
          this._reportProgress();
          if (plan.position(segment) >= segment.end) {
            segmentDone = true;
            cancel(req); // the rest of the body, if any, is not needed
          }
        },

        onStopRequest: (req, status) => {
          this._lanes.delete(lane);
          try {
            out.close();
          } catch {
            // Closing a stream that failed to write may throw; the failure is reported below.
          }
          this._reportProgress(true);
          if (failure) {
            reject(failure);
          } else if (segmentDone || plan.remaining(segment) <= 0) {
            resolve();
          } else if (this._canceled) {
            reject(new RangeFailure("network", { message: "canceled" }));
          } else if (!Components.isSuccessCode(status)) {
            reject(new RangeFailure("network", { result: status, message: "network error " + status.toString(16) }));
          } else {
            reject(new RangeFailure("short", { message: "connection ended early" }));
          }
        },
      };

      try {
        channel.asyncOpen(listener);
      } catch (e) {
        this._lanes.delete(lane);
        try {
          out.close();
        } catch {
          // Nothing was written.
        }
        reject(new RangeFailure("network", { message: String(e?.message ?? e) }));
      }
    });
  }

  _reportProgress(force = false) {
    let now = Date.now();
    if (!force && now - this._lastProgress < PROGRESS_INTERVAL_MS) {
      return;
    }
    this._lastProgress = now;
    if (this._plan) {
      this._setProgress?.(this._plan.doneBytes, this._info.total, true);
    }
  }

  cancel() {
    this._canceled = true;
    this._abortConnections();
  }

  _abortConnections() {
    for (let lane of this._lanes) {
      try {
        (lane.request ?? lane.channel).cancel(Cr.NS_BINDING_ABORTED);
      } catch {
        // Already finished.
      }
    }
  }

  async removeData(canRemoveFinalTarget = false) {
    this._info.segments = null;
    this._plan = null;
    return super.removeData(canRemoveFinalTarget);
  }

  toSerializable() {
    if (this._finished || !this.download || this.download.succeeded) {
      return "copy";
    }
    let segments = this._plan ? this._plan.toJSON() : this._info.segments;
    let serializable = { type: "copy", ...this._unknownProperties };
    if (segments) {
      serializable.evergreen = saveState({
        url: this._info.url,
        total: this._info.total,
        validator: this._info.validator,
        plan: { toJSON: () => segments },
      });
    }
    return serializable;
  }
}

// --- Taking over downloads -------------------------------------------------------

/** Waits for the single-connection download's first reply (its request). */
function waitForRequest(saver) {
  if (saver.request) {
    return Promise.resolve(saver.request);
  }
  return new Promise(resolve => {
    let original = Object.getPrototypeOf(saver).onTransferStarted;
    let timer = setTimeout(() => {
      delete saver.onTransferStarted;
      resolve(null);
    }, REQUEST_WAIT_MS);
    saver.onTransferStarted = function (request) {
      delete saver.onTransferStarted;
      clearTimeout(timer);
      original.call(this, request);
      resolve(request);
    };
  });
}

function describeRequest(request) {
  if (!(request instanceof Ci.nsIHttpChannel)) {
    return null;
  }
  try {
    return {
      scheme: request.URI.scheme,
      method: request.requestMethod,
      status: request.responseStatus,
      acceptRanges: header(request, "Accept-Ranges") ?? "",
      contentEncoding: header(request, "Content-Encoding") ?? "",
      contentLength: request instanceof Ci.nsIChannel ? request.contentLength : -1,
      host: request.URI.asciiHost,
      validator: validatorFor({ etag: header(request, "ETag"), lastModified: header(request, "Last-Modified") }),
    };
  } catch {
    return null;
  }
}

export const EvergreenDownloads = {
  _view: null,
  _list: null,
  // The most recent decisions, for tests and for `evergreen.downloads.log`.
  events: [],

  note(event) {
    this.events.push(event);
    if (this.events.length > 50) {
      this.events.shift();
    }
  },

  async init() {
    if (this._view) {
      return;
    }
    this._view = {
      onDownloadAdded: download => this._consider(download).catch(e => console.error("Evergreen downloads", e)),
    };
    this._list = await Downloads.getList(Downloads.ALL);
    await this._list.addView(this._view);
  },

  /** At quit, pause our downloads so they are saved as resumable, not as in progress. */
  quit() {
    if (!this._list) {
      return;
    }
    this._list.getAll().then(all => {
      for (let download of all) {
        if (download.saver instanceof SegmentedSaver && !download.stopped) {
          download.cancel().catch(() => {});
        }
      }
    }, () => {});
  },

  async _consider(download) {
    if (!settings().enabled || download.succeeded) {
      return;
    }
    let saver = download.saver;
    if (saver instanceof SegmentedSaver) {
      return;
    }
    if (saver instanceof DownloadLegacySaver) {
      await this._takeOver(download, saver);
    } else if (saver instanceof DownloadCopySaver && saver._unknownProperties?.evergreen) {
      await this._resume(download, saver);
    }
  },

  async _takeOver(download, saver) {
    let request = await waitForRequest(saver);
    if (!request || download.stopped || download.saver !== saver) {
      return;
    }
    let info = describeRequest(request);
    let reason = info ? takeoverVerdict(info, settings()) : "not http";
    if (reason) {
      log("left to Firefox:", reason, download.source.url);
      this.note({ url: download.source.url, action: "left", reason });
      return;
    }
    let state = { url: download.source.url, total: info.contentLength, validator: info.validator, segments: null };
    // Our own part file: the single-connection download's helper may still be
    // deleting its temporary file while we start.
    let targetPath = download.target.path;
    await download.cancel();
    if (download.succeeded) {
      return;
    }
    let ours = new SegmentedSaver(state);
    ours.download = download;
    ours.alreadyAddedToHistory = true;
    download.saver = ours;
    download.target.partFilePath = targetPath + PART_SUFFIX;
    download.hasPartialData = false;
    download.start().catch(() => {});
  },

  async _resume(download, saver) {
    let state = loadState(saver._unknownProperties.evergreen, download.source.url);
    if (!state) {
      return;
    }
    // Firefox may already have restarted it as a plain download at startup:
    // stop that before it can overwrite the partial file.
    if (!download.stopped) {
      await download.cancel();
    }
    let ours = new SegmentedSaver({
      url: state.url,
      total: state.total,
      validator: state.validator,
      segments: state.segments,
    });
    ours.download = download;
    ours.alreadyAddedToHistory = true;
    download.saver = ours;
    if (!download.error && !download.succeeded) {
      download.start().catch(() => {});
    }
  },
};
