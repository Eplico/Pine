# Download manager (accelerated, resumable downloads)

Status: 0.3 (unreleased); queue, global limit and extension downloads added after the first version. Decided by the user's
brief: *built into Evergreen; faster and steadier downloads by splitting a file
into parts, as download managers do; any engine, as long as it is built in.*

## What it does

Large downloads over HTTP(S) are fetched on several connections at once, each
taking a different byte range of the file, and written into one file. When a
connection finishes early it takes half of the biggest range still left (so a
slow connection never holds the download back). A dropped connection is retried
from where it stopped, with a growing pause; a server that starts refusing
connections (HTTP 429/503) makes the download use fewer. A paused, failed or
interrupted download (including after a restart) continues from the bytes
already on disk, and only if the file on the server is provably the same file.
Anything the manager cannot do safely is left to Firefox's own downloader,
unchanged.

It lives in Firefox's normal Downloads panel: the same entries, the same Pause,
Resume, Cancel, "Show in folder" and "open when finished" behaviour.

## Decision: write the engine, do not bundle one

The brief named aria2-like projects (Motrix, Gopeed, aria2). They were weighed
and rejected for Evergreen:

| | Bundled engine (aria2, Gopeed, ...) | Built-in module (chosen) |
|---|---|---|
| Network stack | Its own: ignores Firefox's proxy, DNS-over-HTTPS, HSTS, certificate rules, tracking protection, containers | Firefox's: the request is a Firefox channel, so every one of those applies |
| Cookies, sign-ins, referrer | Must be copied out of the browser into a second process (a leak of session secrets by design) | Nothing leaves the browser: the channel sends the Space's own cookies |
| Local attack surface | A listening RPC port (even on 127.0.0.1, any local program or web page that can guess it can queue downloads or read their cookies) plus a second process | None: no port, no process |
| Supply chain | A prebuilt binary per platform to build, checksum, update and license (aria2 is GPL-2.0; Motrix MIT but needs aria2 or Electron; Gopeed Go + Flutter) | None. About 700 lines of MPL-2.0 JavaScript in `src/` |
| Build and size | Cross-compile per platform; megabytes | Nothing extra |
| Speed | Highest ceiling | Enough: the limit is the network, not a JavaScript loop (see "Performance") |

Evergreen's rule is a thin layer on Firefox; a second networking stack that
re-implements cookies and TLS decisions would be the opposite, and for a
security-focused browser the missing RPC port and the missing cookie handoff
are the point. If a future need (BitTorrent, metalinks, FTP) outgrows this, the
design can move to a helper; the module boundary below makes that a swap of
`EvergreenDownloads.sys.mjs`, with the same threat-model questions to answer
then (random per-session token, loopback only, no cookie export, pinned
checksums and licence review of the binary).

## How it is built

- `DownloadPlan.sys.mjs`: pure logic with no Firefox dependencies, unit-tested
  in Node (`tests/unit/downloadplan.test.mjs`). The segment plan (split, take
  over half of the largest remaining part, record progress), the choice of how
  many connections, retry pauses, checks of a server's range reply, the rule
  for "is this file the same one", the persisted state, and which downloads to
  take over.
- `EvergreenDownloads.sys.mjs`: the Firefox side. `SegmentedSaver` is a
  `DownloadCopySaver` (Firefox's own saver class) whose `execute()` runs the
  connections; the rest (reputation check, move from the `.part` file to the
  final name, removal of partial data, hash, history) is Firefox's own code,
  inherited. `EvergreenDownloads.init()` watches the Downloads list and swaps
  the saver in.
- No Firefox patch. The hook is the public Downloads list view.

### Taking over a download

Firefox starts a browser download (a link, "Save Link As", a page that sends a
file) with a single connection, then adds it to the list. The manager looks at
each new entry whose saver is Firefox's "legacy" saver (the one wrapping that
single connection) and decides from the response headers the first connection
already received:

- plain HTTP(S) `GET`; status 200; `Accept-Ranges: bytes`; a known
  `Content-Length` of at least `evergreen.downloads.minSizeMB` (default 8);
  no `Content-Encoding` (a compressed body cannot be split by byte range);
- the host is not in `evergreen.downloads.excludeHosts`.

If all hold, it cancels that single connection (the same cancel the user's
Cancel button does), puts a `SegmentedSaver` into the same `Download` object and
starts it. The user sees the same entry keep going; it is never listed twice.
If anything about the new attempt looks wrong before the first byte is written
(a server that answers a range request with the whole file, a different size,
an error status), the saver hands the download to Firefox's own `copy` saver,
which is exactly what "Retry" does on any download.

**Known limit:** the file is requested again after the first connection is
cancelled. A server that hands out single-use URLs (some token links) would
refuse the second request; Firefox's own "Retry" has the same property. The
exclusion pref covers such hosts, and the manager does nothing when the first
reply has no `Accept-Ranges` (which most single-use links omit).

### Writing the file

IOUtils has no write-at-offset. Each connection holds its own
`nsIFileOutputStream` seeked to the start of its range of the same file and
copies the received bytes straight from the network stream
(`writeFrom`, no JavaScript copy). The file is created at its final size first
(sparse on Linux, extended on Windows). Chunks are written on the main thread:
local disks take them from the OS cache in microseconds. A very slow disk (a
network share, a USB stick) would make the browser stutter while a download
runs; if that appears in practice the writes move to a worker. The SHA-256 for
Firefox's reputation check is computed after the file is complete, by
`IOUtils.computeHexDigest` (off the main thread).

### Resuming

The segment state (`total`, the server's validator, and `[start, end, done]` per
segment) is stored inside the download's own record in `downloads.json`, as an
extra property Firefox preserves untouched (`evergreen`). Without the manager
the record is just a "copy" download with no resume information, so Firefox
restarts it from the beginning instead of resuming a file whose layout it does
not understand. With the manager, `onDownloadAdded` finds the stored state and
installs a `SegmentedSaver` that continues. Every request on resume carries
`If-Range` with the stored validator: a server whose file changed answers 200
with the whole new file, which is not written: the download fails, and "Retry"
starts again from byte 0 instead of mixing two versions. A server that gave no
strong `ETag` or `Last-Modified` cannot prove anything, so it is never resumed
across a restart (it starts again).

## Threat model

- **No new listening port, no helper process, no new binary.**
- **Credentials:** requests use the same channel code as Firefox's downloader
  (container id, private-browsing flag, referrer, `Authorization` header of the
  original request). Cookies are chosen by Firefox for that container; they are
  never read or copied by Evergreen code.
- **Private windows:** supported, with the same private flag on every channel
  as Firefox's own downloader, so a private download uses the private cookie
  jar; the file itself lands on disk like any private download in Firefox.
- **A malicious server:** can lie about sizes and ranges. Every reply is checked
  against the plan (`Content-Range` start, end and total must match what was
  asked; `206` required); data beyond the range is never written; the file never
  grows past its announced size. A reply that disagrees before any byte was
  written hands the download to Firefox's own downloader; after that it fails
  with an error (and "Retry" starts again from the beginning).
- **Connection count:** at most `evergreen.downloads.connections` (default 6,
  hard limit 16) per download, `maxConnections` across all downloads (a
  first-come queue, `Limiter` in `DownloadPlan.sys.mjs`), and `maxActive`
  downloads at once. HTTP/2 hosts multiplex the connections on one anyway.
- **Disk:** the target file is created up front at the announced size, so a
  server announcing a huge `Content-Length` makes a sparse file of that size.
  Downloads above `evergreen.downloads.maxSizeGB` (default 64) are not taken
  over, and a failed download removes its file like Firefox does.
- **Integrity:** the plan checks byte counts, not file contents. As with any
  download, a checksum published by the site should be checked by the user;
  nothing here weakens Firefox's own checks (safe-browsing reputation, content
  analysis) because they run unchanged on the finished file.

## Preferences

| Pref | Default | Meaning |
|---|---|---|
| `evergreen.downloads.enabled` | `true` | Master switch. Off: Firefox's downloader, exactly as before. Takes effect for new downloads at once |
| `evergreen.downloads.connections` | `6` | Connections per download (1 to 16) |
| `evergreen.downloads.minSizeMB` | `8` | Smaller files are left to Firefox |
| `evergreen.downloads.maxSizeGB` | `64` | Larger files are left to Firefox |
| `evergreen.downloads.excludeHosts` | `""` | Comma-separated hosts (and their subdomains) never accelerated |
| `evergreen.downloads.maxActive` | `3` | Accelerated downloads running at once; further ones wait for a place (`0` = no limit) |
| `evergreen.downloads.maxConnections` | `32` | Connections open across all downloads together (`0` = no limit) |

There is no settings page. The common settings (on or off, connections per
download) are commands in the command bar (`Ctrl+T`, then `> download`); every
pref is also reachable from `about:config`. Why no page: it would need its own
`about:` module and a patch, for two switches.

## Performance

Parts help when a server or a path limits each connection (per-connection
rate limits, long-distance latency, flaky links), not when the user's own line
is already full. A plain single stream remains the right tool below the size
threshold. The engine's work per chunk is one `writeFrom` call and a counter
update; the per-download overhead is at most `connections` timers. Progress
is reported to the panel about five times a second.

## Known limits

- A crash (not a normal quit) leaves a record that Firefox restarts at the next
  start with its own downloader, from the beginning. A normal quit pauses the
  manager's downloads, and they continue by themselves at the next start.
- A download that was already being resumed by Firefox itself when the browser
  starts is stopped and continued by the manager (a short race, see
  `EvergreenDownloads._resume`).

## Queue and limits

`SegmentedSaver.execute()` first takes a *place* from `EvergreenDownloads.places`
(`maxActive`); a download waiting for one is shown as started with no progress
yet, and Cancel works while it waits. Each connection (a request for a segment)
takes a ticket from `EvergreenDownloads.connections` (`maxConnections`) and gives
it back when the request ends, so downloads share the budget in the order they
ask. A canceled download leaves both queues at once. There is no *speed* limit.

## Downloads made by extensions and Firefox features

A fresh plain `copy` download (the `downloads` WebExtension API, "Save As"
from a page, and the like; one younger than 15 seconds and not restored from
disk) cannot show the first connection's headers, so `probe()` asks the server
with one request for `bytes=0-0`: a `206` with a `Content-Range` that starts at 0
proves ranges and gives the size. The rest is the same verdict as for
navigated downloads. Left alone: downloads whose request an extension shaped
(`adjustChannel`, a method other than GET). The single connection that was
already running is canceled, as for navigated downloads. The probe is a second
request, so a single-use URL is lost like it is for navigated downloads.

## Not done yet

- A speed limit; a per-download connection count.
- Writes on a worker for very slow disks. Blocked by Firefox: `IOUtils` has no
  write at an offset, and a worker cannot use `nsIFileOutputStream`, so the
  only ways are one file per segment joined at the end (a second copy of the
  whole file) or a native helper (rejected above). Main-thread writes stay.
- Signature information for Windows reputation checks is not computed.

## Verification

`tests/unit/downloadplan.test.mjs` covers the planner. The smoke test
(`accelerated downloads` in `tests/smoke/smoke_test.py`) runs a real Firefox
against a local HTTP server that supports ranges and checks that a navigated
download is taken over, arrives on several concurrent range requests, matches
the SHA-256 of the served file, survives a server that drops connections
mid-way, falls back when the server ignores ranges, and is left alone when the
pref is off.
