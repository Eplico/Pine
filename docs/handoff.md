# Evergreen: handoff

Written 2026-11-21, updated 2026-11-23. It describes the state of the branch
`claude/determined-cerf-v1dp7l` (Evergreen **0.2**, on Firefox **160.0.1**, not
yet released) and the published release **v0.1.1**. See "Update, 0.2" below
for what changed since the first version of this document. Read this first, then [design.md](design.md) (the why)
and [development.md](development.md) (the how).

## In one paragraph

Evergreen is a security-focused desktop browser built on Firefox Release, with
an Arc-style sidebar: Spaces (optionally each with its own sign-ins), Favorites,
an Archive for idle tabs, a start page and an Arc-like new-tab search box. It
is a **thin layer on unmodified Firefox source**: build settings, default
prefs, branding, Evergreen's own UI modules and five tiny patches (10 changed
upstream lines). There are no Evergreen servers: Windows builds run on
GitHub's hosted runners and are published as GitHub releases. It is a
personal, non-commercial project. The repository is called **Pine**; the
product is **Evergreen**.

## Where things are

| What | Where |
|---|---|
| Code | GitHub `Eplico/Pine`, branch `claude/determined-cerf-v1dp7l` (all work so far; no pull request has been opened) |
| Releases | <https://github.com/Eplico/Pine/releases>: **v0.1.1** (latest, 2026-10-05), v0.1, and two older `v157.0-N` previews |
| CI | `.github/workflows/ci.yml` on every push: lint, unit and tooling tests, patch and pref checks against the pinned Firefox, smoke test in Firefox on Linux (the pinned 160.0.1) and Windows (the runner's Firefox) |
| Windows build | `.github/workflows/windows-build.yml`, started by hand (Actions › Windows build › Run workflow) or by pushing a `v<VERSION>` tag. About 90 minutes |
| Design | `docs/design.md`: goals, threat model, architecture, features, decisions (Q1–Q11), milestones |
| Developer guide | `docs/development.md`: dev harness, tests, building on Windows, patches, releasing |

## Update, 0.2 (unreleased)

Done in a later session, all on this branch:

- **Firefox 157.0 → 160.0.1** (`upstream.json`). Only patch 0003 needed
  rebasing: upstream moved the installer stub path into `browser/moz.configure`
  (`NSIS_SFX_MODULE`), so 0003 now overrides `SFX_MODULE` in
  `browser/installer/windows/Makefile.in` for non-ARM builds. The sidebar width
  calculation uses the renamed `--tab-container-margin-inline-pinned-expanded`.
  `check-patches` and `check-prefs` pass against 160.0.1. **The Windows build
  has not been run on 160.0.1 yet.**
- **Address bar** is a compact pill (`--evergreen-urlbar-width`) centred between
  the navigation buttons and the extensions, as in Arc; the empty toolbar on
  both sides drags the window. It opens to `--evergreen-urlbar-width-open` while
  focused. CSS only (`evergreen.css`, "The address bar").
- **Favorites** (pinned tabs) are big tiles, two to a row (`evergreen.css`,
  "Favorites"). Pure CSS on Firefox's pinned-tab grid; it leans on
  `#pinned-tabs-container::part(items-wrapper)` and `--icon-size`, so check it
  on every Firefox bump.
- **Popup windows** (`window.open` with a size; Firefox sets `popup-window` on
  the root): `EvergreenWindow.startPopup` gives them `evergreen-popup`, the
  toolbar and rounded frame, no Spaces and no sidebar, and centres them over
  the opener (`centerPopup` in `EvergreenLayout.sys.mjs`, tested by
  `tests/unit/layout.test.mjs`). The "mini window" and Peek (M2) are separate and
  still not done; Firefox 160 now has its own `mini-window` mode to build on.
- **Linux build**: `.github/workflows/linux-build.yml` (Actions › Linux build ›
  Run workflow). It builds on `ubuntu-24.04`, packages
  `Evergreen-<VERSION>-linux-x86_64-portable.tar.*`, smoke-tests the packaged
  browser headless, and publishes (tag `v<VERSION>-linux`, not "latest") only
  with *Publish* ticked. See development.md, "Building for Linux".
- **Linux build verified**: [run 44856584591](https://github.com/Eplico/Pine/actions/runs/44856584591)
  (commit 477209f) compiled, packaged the tarball (artifact `evergreen-linux`,
  about 92 MB) and passed the smoke test on the packaged binary. **Nothing from
  0.2 has been published**; a release needs the user's go-ahead (run the Linux
  workflow with *Publish* ticked, or `from_run`). The Windows build is still
  unrun on 160.0.1.
- Firefox 160 gotchas found on the way (all handled): Marionette's chrome
  context needs `MOZ_REMOTE_ALLOW_SYSTEM_ACCESS=1`; many browser modules moved
  from `resource:///modules/` to `moz-src:///` (use `importBrowserModule` in
  `FirefoxModules.sys.mjs`); `-moz-window-dragging` can't be read through
  `getComputedStyle`, so the smoke test checks that the bare toolbar gaps hit
  `#nav-bar` instead.
- Not done or not verified: popups were checked by assertions only, never by
  eye; "window popups" was read as Arc-style popup windows, not Peek (confirm
  with the user); no local Firefox could be run in the cloud session (download
  denied), so all UI verification was through CI.
- Smoke checks for the pill, the Favorites tiles and popups; the Linux CI smoke
  job prints screenshots into its log as `EVERGREEN-SHOT <name> data:image/jpeg;base64,...`
  lines (decode them to see the UI when artifacts can't be downloaded).

## Update, 0.2 (installing and updating; Linux launcher)

A later session, on branch `claude/relaxed-bardeen-ojrswr` (same history as
`claude/determined-cerf-v1dp7l`). Triggered by two reports from daily use on
Linux: moving to a new build lost the data, and the executable had no icon and
a pinned copy turned into a blank page that could not be reopened.

**Causes found (in Firefox 160.0.1's source):**

- *Data seemed lost.* `nsToolkitProfileService` ("dedicated profiles",
  `MOZ_DEDICATED_PROFILES`, implied by `browser/moz.configure`) keys each
  install's profile on a hash of the program's folder (`GetInstallHash`). A tarball
  unpacked somewhere else is a "first run", Firefox creates a new empty profile and
  the old one stays claimed by the old folder. Nothing was deleted.
- *No icon, dead pin.* The tarball is only a folder: no `evergreen.desktop`, no
  icons in the icon theme. GNOME/KDE match a window to an app through the
  desktop entry (`StartupWMClass` / Wayland app id = Firefox's remoting name,
  `evergreen`), so a pinned window had nothing to match or launch.

**What was added:**

- `packaging/linux/evergreen-install` (Python 3, stdlib): `install`, `update`,
  `check`, `stage`, `apply`, `integrate`, `uninstall`. Fixed install path
  `~/.local/share/evergreen/app`, so updates keep the profile; verifies
  downloads against `SHA256SUMS.txt`; writes the launcher script, the app-menu
  entry (absolute `Icon=` path) and icons. `eg.py collect` ships it as a release
  asset. 23 tests in `tests/python/test_linux_installer.py` (fake GitHub).
- `EvergreenUpdate` + `UpdateCheck` (unit-tested): daily check of GitHub's
  release list (`evergreen.update.*` prefs; `evergreen.version` is written
  into the branding prefs by `eg.py prepare`), a notification bar, and on a
  managed Linux install *Update* then *Restart* (the launcher script switches
  folders while Evergreen is closed). Windows and hand-unpacked installs get a
  *Download* button that opens the release page.
- `EvergreenProfile` + `ProfileAdoption` (unit-tested): on the first run of a new
  folder, offers the earlier profile with the most data (skipping any that a newer
  Firefox last used), switching the way `about:profiles` does. Nothing copied or deleted.
- Smoke checks: update notice (against a fake release list) and profile choice.
  CI step `tests/linux/check_desktop_integration.py` (Linux build workflow,
  `continue-on-error` until it has passed once).

**Not verified (no Firefox could be run in the session; CI is the check):**

- The two new smoke checks and the notification bars have never run; the
  `l10n-id` notification labels, `Subprocess` use and the `about:profiles`-style
  switch (`defaultProfile` then `asyncFlush`, then a restart) follow Firefox
  160's own code but were not exercised.
- Whether a *Wayland* app id equals `evergreen` (X11 class does, by reading the
  code): `check_desktop_integration.py` checks X11 only.
- The Windows build is still unrun on 160.0.1 and Windows has no in-app update.
- The updater trusts GitHub + TLS only (the checksums are in the same release).
  Signed MAR updates (M3) are still the real fix; see design.md §9.4.

## Update, 0.3 (download manager; CI findings)

Same branch (`claude/relaxed-bardeen-ojrswr`). The user's ongoing permission:
**publishing releases is allowed** once the build and its smoke test pass (do not
publish a failing build).

**CI results that the previous session never saw** (its last push, `ee8b325`,
had failed CI): the installer tests failed on the runner because the fake
"Evergreen" was a copy of `sleep` and the runner's coreutils is a multi-call
binary that refuses to run under another name (now a tiny compiled fake); and
the update notice never showed up in the smoke test (now shown in
`BrowserWindowTracker.getTopWindow({allowPopups: false})`; the smoke check
reports windows and notices when it is missing). Read CI results with
`curl https://api.github.com/repos/Eplico/Pine/actions/runs/<id>/jobs` (the
repository is public, no token needed) and job logs with the GitHub MCP tool
(save to a file: they are 400+ KB on one line; grep for `PASS|FAIL`).

**Download manager**, built in, no helper process: see
[download-manager.md](download-manager.md) (the why, the threat model, the
prefs, the limits).

- `DownloadPlan.sys.mjs`: segments, work taking, range-reply checks, retry
  pauses, resume state, take-over rules. 15 Node unit tests.
- `EvergreenDownloads.sys.mjs`: `SegmentedSaver` (a `DownloadCopySaver` subclass:
  several ranged channels, one file via `nsIFileOutputStream` per connection,
  Firefox's own reputation check and move) and the Downloads-list view that
  swaps it into qualifying downloads (`DownloadLegacySaver`: cancel the single
  connection, give the same `Download` our saver and our own `.evergreen-part`
  file, start). Resume across restarts through an `evergreen` property in the
  saver's record. Started from `EvergreenStartup`; paused at quit.
- Prefs `evergreen.downloads.*` (enabled, connections, minSizeMB, maxSizeGB,
  excludeHosts, log).
- Smoke check "accelerated downloads": a local range server with throttling
  (`DownloadServer` in `smoke_test.py`): several connections at once, cut
  connections retried, pause and resume, files left alone, a server that lies
  about ranges, and the off switch.
- **Not verified until CI says so** (see the next section's results): the whole
  Firefox side. Things most likely to need a fix: taking over a legacy download
  (`download.cancel()` then restart), `nsIFileOutputStream` seek/`writeFrom`,
  the resume path at startup, and the `_checkReputationAndMove` inheritance.

## Current state (v0.1.1, as released)

**Released, v0.1.1** (built on Firefox 157.0, run 37249999440):

- Spaces in the vertical-tab sidebar, optionally with separate sign-ins
  (Firefox containers); Favorites (pinned tabs, shared by all Spaces); "Keep in
  Space"; an Archive for tabs idle 12 hours; click a Space's name to rename it.
- Toolbar, left to right: the app menu drawn as an evergreen tree, the sidebar
  button, Downloads, then navigation lined up with the page's left edge;
  extensions at the far right. Toolbar about 20% thinner than Firefox's,
  sidebar two thirds as wide.
- Hide the sidebar (button or `Ctrl+Alt+Z`); the left window edge slides it
  back in over the page, and it slides away 40 ms after the mouse leaves.
- **Start page**: a solid page in the Space's colour with "Evergreen" above a
  search box. It is the *empty state*: shown when a Space has no tabs (fresh
  start, or the last tab closed; the window never closes with its last tab).
  It never appears as a tab in the list.
- **New tab** (`Ctrl+T`, the + button): a search box floats over the page, as
  in Arc. Enter opens a new tab at the top of the Space's tabs; Escape cancels.
  Both boxes follow the default search engine (Firefox's own address parsing)
  and suggest pages from history and bookmarks.
- New tabs open at the top of the list.
- First run: ETP Strict, Ecosia as the default engine (kept unless the user
  picks another), and an offer to import from another browser.
- Hardened prefs (65 in `prefs/evergreen.js`): HTTPS-Only, encrypted DNS
  (Quad9), no telemetry or studies, no sponsored content, AI features off.
  uBlock Origin is bundled.
- The User-Agent is exactly Firefox's (patch 0005), so addons.mozilla.org
  installs add-ons and Evergreen doesn't stand out.
- Installer: Evergreen artwork, icon, version info and titles. The release
  workflow opens the installer on a real Windows runner and checks it at 100%
  and 125% display scaling (`tests/installer/check_installer.ps1`).

**Not done**: automatic (silent) updates, code signing, macOS packages, the
command bar, split view, Peek, the mini window, link routing (see
"Next steps"). Update notices and Linux install/update came in 0.2 (above).

## Urgent: Firefox security updates

Evergreen is pinned to **Firefox 157.0** (`upstream.json`). As of 2026-11-21
Mozilla's GitHub mirror has release tags up to **`FIREFOX_160_0_1_RELEASE`**
(157.0.1, 158.0, 158.0.1, 159.0, 159.0.1, 160.0 and 160.0.1 have all shipped since).
Each of those carries security fixes that Evergreen users don't have, and
Evergreen has no updater yet. Moving to the latest release is the most
important next task:

1. Set `firefox.version` in `upstream.json` to the new version and
   `sha512` to `null`.
2. `python eg.py check-patches` and `python eg.py check-prefs` show within
   seconds whether the five patches still apply and every pref still exists
   (they fetch single files from the GitHub mirror).
3. Fix whatever broke. UI selectors and Firefox internals Evergreen relies on
   are listed under "Fragile points" below; the smoke test in CI (Linux runs
   the pinned version) catches most breakage.
4. Bump `VERSION` (e.g. 0.1.2 or 0.2), push, wait for CI, run the Windows
   build. Its `eg.py fetch` checks the source tarball's GPG signature against
   Mozilla's release key pinned in `upstream.json` (the signing subkey was
   rotated on 2026-08-10; a future rotation needs a new key entry there).
   `eg.py fetch --pin`, on a machine that can reach archive.mozilla.org, also
   pins the tarball's hash.
5. Publish (see "Releasing").

## How to work on it

Commands (`python eg.py <command>`; Python 3.11+):

| Command | Does |
|---|---|
| `status` | Evergreen version, Firefox pin, work directory, patch budget |
| `dev` | Runs Evergreen's UI inside a copy of an installed Firefox (fast loop; no build) |
| `lint` | Patch headers and series, prefs file syntax |
| `check-patches`, `check-prefs` | Against the pinned Firefox, via the GitHub mirror |
| `fetch`, `prepare`, `build`, `package`, `collect` | The real build (Windows; see development.md) |

Tests:

- `npm run lint` (ESLint with `no-unsanitized`) and `npm test` (36 Node unit
  tests: Spaces model, archive policy, search defaults, start page/search box
  helpers).
- `python -m unittest discover -s tests/python` (35 tooling tests, including
  the full prepare pipeline on a fake Firefox tarball and installer stub
  branding).
- `python tests/smoke/smoke_test.py --firefox <Firefox dir> --headless`: 27
  end-to-end checks in a real Firefox over Marionette (dev harness), or
  `--binary <evergreen.exe>` for a built Evergreen (the release workflow does
  this). Add `--screenshots <dir>` to save screenshots.
- `tests/installer/check_installer.ps1 -Setup <setup.exe> [-Scale 150]`
  (Windows only): runs the installer, checks its windows and images, closes it
  without installing.

**Releasing** (Windows):

1. Set `VERSION`. A version is published once; the tag is `v<VERSION>`, the
   files are `Evergreen-<VERSION>-win64-setup.exe` and `-portable.zip`. Keep
   the version to five characters or fewer (`0.1.2`, `0.10`): the installer
   stub's version block has no spare room, and a longer one leaves the
   product version blank in the file's Properties.
2. Push and wait for CI to pass.
3. Run the **Windows build** workflow. With *Publish* ticked it publishes the
   release (as the repository's Latest) only if the smoke test and installer
   check pass. To publish a build that was run with *Publish* off, run the
   workflow again with `from_run` set to that run's ID (it re-tests and
   publishes without recompiling).
4. Release notes come from `.github/release-notes.md` (update the feature list
   when features change).

## Code map

UI layer, `src/browser/components/evergreen/` (privileged JS modules loaded
into every browser window; registered by patch 0001; see design.md §6):

| File | Responsibility |
|---|---|
| `EvergreenStartup.sys.mjs` | Startup and first-run steps (ETP, search default, import offer), purge hooks |
| `EvergreenWindow.sys.mjs` | Per-window controller: sidebar UI, Spaces (switching, containers, tab bookkeeping), Archive panel, editor, keys, start-page tabs (`evergreen-blank`), last-tab handling, new tabs at the top |
| `EvergreenLayout.sys.mjs` | Toolbar order and alignment, sidebar collapse and edge reveal |
| `EvergreenSearch.sys.mjs` | Start page and `Ctrl+T` search box (overlay over the page area), history suggestions |
| `Spaces.sys.mjs`, `SpacesStore.sys.mjs` | Spaces model and its JSON store in the profile |
| `Archive.sys.mjs`, `ArchiveStore.sys.mjs` | Archive policy and store |
| `DownloadPlan.sys.mjs`, `EvergreenDownloads.sys.mjs` | Accelerated downloads (logic; Firefox side). See download-manager.md |
| `SearchDefaults.sys.mjs` | Ecosia as default, kept unless the user changes it; `getSearchService` (156+ moved the service to a module) |
| `evergreen.css` | All window styling |

Other places:

- `prefs/evergreen.js`: default prefs (appended to the branding prefs at build).
- `branding/evergreen/`: icons, installer bitmaps (`source/make_installer_images.py`
  draws them), brand strings, `configure.sh`.
- `patches/` (series file lists them): 0001 registers the component; 0002
  packages bundled extensions; 0003 builds the installer from the branded
  stub; 0004 titles the self-extractor "Evergreen"; 0005 makes the
  User-Agent Firefox's. Patch rules and budget: design.md §5.3.
- `tools/eg/`: the build CLI. `sfxstub.py` rebrands the installer's 7-Zip stub
  (icons and version strings, in place: it is UPX-packed with no free room).
- `mozconfigs/`: build settings (`--with-app-basename=Evergreen`, app name
  `evergreen`, release config, updater and crash reporter off).
- `dev/`: the dev harness (autoconfig that loads the modules into a stock
  Firefox).

## How the trickier features work

- **Spaces** hide other Spaces' tabs with `gBrowser.hideTab`; each tab's Space
  is a SessionStore tab value. Pinned tabs are Favorites and are never hidden.
- **Start page as the empty state.** Firefox can't show a window with no tabs,
  so a tab showing `about:home`/`about:newtab` is marked `evergreen-blank`,
  hidden by CSS, covered by the start page overlay, and closed as soon as
  another tab is selected. When the selected tab closes and it was the
  Space's last one, Firefox picks the next tab *before* `TabClose`;
  `onTabSelect` notices the closing previous tab and holds the Space switch,
  and `onTabClose` opens a new start-page tab in the same Space instead.
  `browser.tabs.closeWindowWithLastTab` is off. Whatever is opened from the
  start page loads in its tab, which then moves to the top of the list.
- **New tab** replaces `BrowserCommands.openTab` when it's called without an
  address (Ctrl+T, the + button and the tab strip all go through it).
- **New tabs at the top / in the Space's container**: a wrapper around the
  window's `openTrustedLinkIn`. Links opened from a page are untouched.
- **Search box** resolves text with `Services.uriFixup` (keyword lookup uses
  the default engine, or the private one in private windows); a search is
  detected by `keywordAsSent` (157 reports the engine as `keywordProviderId`,
  older versions as `keywordProviderName`). History suggestions query
  `moz_places` directly; Firefox's SQLite wrapper rejects SQL in which `LIKE`
  isn't followed by a bound parameter, and its check is case-insensitive, so
  don't name a parameter `:like`.

## Fragile points (check these on every Firefox bump)

- Monkeypatches that a real build should eventually turn into small hook
  patches: `window.openTrustedLinkIn`, `BrowserCommands.openTab`,
  `SidebarController.handleToolbarButtonClick`/`updateToolbarButton`.
- DOM ids and attributes used by the layout and tests: `#sidebar-main` /
  `#sidebar-container`, `#tabbrowser-tabbox`, `#vertical-tabs-newtab-button` /
  `#tabs-newtab-button`, `cmd_newNavigatorTab`, toolbar widget ids.
- Tabbrowser behaviour relied on: `TabSelect`'s `detail.previousTab`, next-tab
  selection happening before `TabClose`, `removeTab(..., { skipSessionStore })`,
  `moveTabToStart`.
- `MOZ_APP_UA_NAME` and `MOZ_APP_VENDOR` can only be set by patching
  `browser/moz.configure` (157+).

## Known issues and open questions

- **Installer image report.** The user once saw the installer without the
  Evergreen image while the uninstaller had it. It was never reproduced: on
  GitHub's Windows runner the image and header icon draw correctly at 100%
  and 125% scaling for v157.0-3, v0.1 and v0.1.1 (the installer check now
  verifies this on every build). If it comes back, ask for a screenshot and
  the display scaling.
- **Unsigned**: SmartScreen warns on every download until there's a
  code-signing certificate (design.md §12, still open).
- **No updater**: users must install new releases by hand; with Firefox
  security releases every few weeks this matters (see "Urgent").
- **The browser's About window shows Firefox's version** (157.0), not 0.1.1:
  Firefox's own version is left alone because sites and extensions rely on it.
  `distribution.id` was considered for showing the Evergreen version but
  rejected: Firefox's search code treats any distribution id as a partner
  build.
- Prefs audit and network egress test (M0) are still pending.
- "Evergreen" is a common software name (Microsoft's WebView2 runtime); check
  for conflicts before any public launch (design.md Q10).

## Next steps, roughly in order

1. Run the **Linux build** (it changed: the UI code is compiled in, so run
   44856584591 is out of date), read the smoke test and the desktop check in its
   log, then ask the user whether to publish (*Publish* ticked, or `from_run`).
   Publishing 0.2 also needs a Windows build, first run on 160.0.1. Check for
   newer Firefox security releases first (see "Urgent"; 160.0.1 was newest on
   2026-11-23).
2. Signed updates: Windows in-app update (download, verify, run the installer);
   later signed MAR updates (M3, design.md §9.4).
3. Turn the monkeypatches listed above into small hook patches.
4. M2 features: command bar (grow the `Ctrl+T` box: switch Space, move tab,
   search the Archive), split view on Firefox's native one, Peek, mini window,
   link routing (design.md §7).
5. Prefs audit and egress test (M0), then code signing (M3).
6. Download manager, next: queue and global limits, a settings page, writes
   on a worker for slow disks, taking over downloads made by extensions and
   Firefox features (copy-saver downloads). See download-manager.md, "Not done yet".

## Conventions and preferences

- Work on the branch the session names (so far `claude/determined-cerf-v1dp7l`
  and `claude/relaxed-bardeen-ojrswr`); don't open pull requests unless asked.
- Commit messages explain what and why. Commits made by Claude end with:
  `Co-Authored-By: <the session's model> <noreply@anthropic.com>` and
  `Claude-Session: <session URL>`.
- **Publishing a release is public: get the user's explicit go-ahead first.**
  Building with *Publish* off and asking is the normal flow; publish from that
  run with `from_run`.
- The user cares about the Arc feel: thin chrome, the sidebar out of the way,
  no visible empty "New Tab" tabs, Ecosia by default. Decisions Q1–Q11 are
  in design.md §12 (Windows first, no servers, non-commercial).
- Keep patches tiny and documented (headers required; budget ≤ 30 patches and
  ≤ 2,000 lines at v1.0).

## Notes for cloud (Claude Code on the web) sessions

- Only GitHub is reachable: github.com, the API, raw.githubusercontent.com
  and release downloads. Mozilla's hosts are blocked, so read Firefox source
  file by file from the mirror:
  `https://raw.githubusercontent.com/mozilla-firefox/firefox/FIREFOX_<ver>_RELEASE/<path>`
  (e.g. `FIREFOX_157_0_RELEASE`). Release-tag existence there is also the
  quickest way to see which Firefox versions have shipped.
- Workflow artifacts can't be downloaded (their storage host is blocked).
  Read results from job logs instead: the smoke test prints every check, and
  the installer check prints the colours it sampled.
- To run the smoke test locally you need a Linux Firefox to point
  `--firefox` at. An earlier session used Ubuntu 20.04's `firefox` package
  (136) from Ubuntu's package archive, which is reachable, unpacked with
  `dpkg -x`; the dev harness works with Firefox 136 and later. CI covers the
  pinned version.
- PowerShell for syntax-checking `check_installer.ps1`: the Linux build from
  the PowerShell/PowerShell GitHub releases works (`Add-Type` compiles its
  C#; the Windows calls only run on Windows).
- Wine can run the installer, but draws its welcome image black whatever the
  bitmap; use the Windows workflow for installer visuals.
- PowerShell variable names are case-insensitive: `$setup` and a parameter
  `$Setup` are the same variable (this bit the installer check twice).
