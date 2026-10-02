# Evergreen — Design Document

| | |
|---|---|
| **Status** | Prototype running; design under review |
| **Last updated** | 2026-10-02 |
| **Base** | Thin patch layer on upstream Firefox, **Release** channel, pinned to 157.0 |
| **Platforms** | **Windows first**; Linux and macOS kept buildable |
| **Repository** | `Eplico/Pine` (the repository keeps its old name; the product is Evergreen) |

Evergreen is a security-focused desktop browser built on Firefox (Gecko) with
the sidebar and multitasking workflow popularised by Arc: vertical tabs in a
sidebar, Spaces, Favorites, auto-archiving tabs, split view, a command bar,
Peek and a mini window for external links.

This document covers what we are building, what we are deliberately not
building, how Evergreen relates to Firefox, the threat model, and how
features and hardening are implemented. Decisions are recorded in
[§12](#12-decisions); day-to-day instructions are in
[development.md](development.md).

---

## Contents

1. [Goals and non-goals](#1-goals-and-non-goals)
2. [Background](#2-background)
3. [Design principles](#3-design-principles)
4. [Threat model](#4-threat-model)
5. [Architecture: how Evergreen modifies Firefox](#5-architecture-how-evergreen-modifies-firefox)
6. [The Evergreen UI layer](#6-the-evergreen-ui-layer)
7. [Features](#7-features)
8. [Security and privacy hardening](#8-security-and-privacy-hardening)
9. [Release engineering](#9-release-engineering)
10. [Testing](#10-testing)
11. [Milestones](#11-milestones)
12. [Decisions](#12-decisions)
13. [References](#13-references)

---

## 1. Goals and non-goals

### Goals

| # | Goal | How we measure it |
|---|------|-------------------|
| G1 | An Arc-style sidebar and multitasking workflow on Firefox | Feature set in [§7](#7-features) shipped and usable as a daily driver |
| G2 | **Ship every Firefox security release fast** | Evergreen release within **48 h** of a Firefox release containing security fixes; **24 h** for fixes to actively exploited bugs |
| G3 | Hardened, privacy-respecting defaults with no data collection | No telemetry; Evergreen operates no servers; egress test passes ([§10](#10-testing)) |
| G4 | A small, auditable diff against Firefox | Patch budget in [§5.3](#53-patch-rules-and-budget) respected; every patch documented |
| G5 | **Windows first**, then Linux and macOS | Signed, updatable Windows builds; the tooling stays portable |

G2 is the goal the others are traded against. A browser that ships Firefox
security releases late is less secure than Firefox itself, regardless of how
well its prefs are hardened.

### Non-goals

- **Anonymity.** Evergreen reduces tracking and fingerprinting but does not
  try to make users indistinguishable from each other. People who need that
  should use Tor Browser or Mullvad Browser.
- **Chrome extension support.** Evergreen runs Firefox WebExtensions from
  addons.mozilla.org (AMO) only.
- **Mobile.** Desktop only.
- **Servers of our own.** No Evergreen accounts, no Evergreen sync, no
  Evergreen backend of any kind. Everything that can happen on the device
  does ([§3](#3-design-principles), principle 6).
- **AI features.** Firefox's AI features are blocked by default
  ([§8.3](#83-data-collection-and-sponsored-content-removed)), and Evergreen
  adds none.
- **Engine changes.** We don't modify Gecko, SpiderMonkey, networking or the
  sandbox, except to turn on something more secure that upstream has already
  built.

---

## 2. Background

### Arc

Arc (The Browser Company) introduced the sidebar-first layout with Spaces,
Favorites, pinned and auto-archiving tabs, split view, the command bar, Peek
and Little Arc. It is Chromium-based. Since May 2025 it has been in
maintenance mode with security patches only, following The Browser Company's
acquisition by Atlassian. We are taking its interaction model, not its code.

Arc also shows what can go wrong when a browser has its own cloud features:
CVE-2024-45489 let an attacker run arbitrary JavaScript in other users'
browsers through misconfigured Firebase access rules on **Boosts**.
Evergreen's "no servers" rule and its handling of Boosts
([§7.9](#79-deferred-and-not-planned)) follow from this.

### What Firefox already provides

| Firefox feature | Shipped in | Evergreen uses it for |
|---|---|---|
| Revamped sidebar + vertical tabs | 136 (Mar 2025) | Base of the Evergreen sidebar |
| Tab groups | 138 | Folders inside a Space (planned) |
| AI controls / "Block AI enhancements" | 148 | Off-by-default AI |
| Split view (two tabs side by side) | 149 | Split view |
| "Open Link in Split View", reverse panes | 150 (Apr 2026) | Split view |
| Containers (contextual identities) | long-standing | Sign-in isolation between Spaces |

Every feature we build on upstream is one we don't have to maintain while
rebasing, which directly serves G2 and G4.

### Existing Firefox derivatives

- **Zen Browser** (MPL-2.0) already ships an Arc-like UI on Firefox. It is the
  closest reference implementation. We decided **not** to fork it: forking a
  fork adds a second hop between a Mozilla security fix and our users. Because
  Zen is MPL-2.0, we can still learn from it and reuse individual files,
  keeping their licence headers.
- **LibreWolf** and **arkenfox** are the main public sources for hardened
  Firefox defaults. `prefs/evergreen.js` will be reviewed line by line against
  both (milestone M0).

---

## 3. Design principles

These principles settle design disagreements. Where two conflict, the one
listed first wins.

1. **Stay close to upstream.** Use a build option or a pref before adding a
   file, and add a file before patching an upstream one. Every patch slows
   down every future security release.
2. **Every web page is a normal Firefox tab.** Peek, the mini window, split
   panes and Favorites all show web content in real tabbrowser tabs, so
   Fission site isolation, the content sandbox, Enhanced Tracking Protection
   (ETP), containers and permissions apply to them. Evergreen never creates
   its own `<browser>` elements for web content.
3. **Evergreen's UI code is privileged code.** It runs in the parent process
   with chrome privileges, so a bug in it can bypass the content sandbox. It
   is held to the rules in [§6.2](#62-rules-for-privileged-code).
4. **The user can always see the real origin.** Evergreen may hide or collapse
   chrome, but the current site's origin, connection security and permission
   indicators must stay reachable, and permission prompts must always have a
   visible anchor.
5. **Secure by default, user in control.** Defaults are hardened, but users
   can change them in Settings. We don't silently lock settings, and we tell
   users when they've moved away from Evergreen's defaults.
6. **Client-side only.** A browser should not need a server. Evergreen runs
   no services: builds happen on a developer's own machine, releases and
   update manifests are static files attached to GitHub releases, and every
   Evergreen feature (Spaces, the archive, search defaults) lives in the
   profile. The only network services Evergreen talks to are the ones Firefox
   needs for security (certificate revocation, Safe Browsing lists, add-on
   updates) and the sites the user visits.

---

## 4. Threat model

### Assets

The user's browsing data (history, cookies, sessions, saved passwords, form
data); signed-in accounts on the web; the integrity of the Evergreen binary
and its updates; the user's identity across sites.

### Adversaries and mitigations

| Adversary | Capabilities | Evergreen's mitigations |
|---|---|---|
| **Malicious website** | Runs arbitrary JS/Wasm, attempts engine exploits, phishing and UI spoofing, fingerprinting | Firefox's sandbox and Fission unchanged; fast patching (G2); HTTPS-Only; Safe Browsing; fingerprinting protection; Evergreen UI never renders web-supplied strings as markup; origin always reachable (principle 4) |
| **Network attacker** (hostile Wi-Fi, ISP, on-path) | Observes and tampers with traffic | HTTPS-Only Mode; DNS over HTTPS; HSTS preload; CRLite revocation; release files signed |
| **Trackers / ad tech** | Third-party scripts, cross-site cookies, bounce tracking, referrers | ETP Strict; bundled uBlock Origin; cross-origin referrer trimming; Global Privacy Control; per-Space containers |
| **Malicious or compromised extension** | WebExtension APIs within granted permissions | Signature enforcement compiled in; AMO only; Evergreen adds no privileged extension APIs |
| **Supply chain** | Tampered Firefox source, compromised build machine, stolen signing keys, hijacked update files | Upstream source verified against a pinned Mozilla key **and** signing subkey; signing separated from building; MAR-signed updates; two-person review for patches; reproducible-build goal ([§9](#9-release-engineering)) |
| **Evergreen itself** | — | No servers, so no Evergreen-held data; open source |

### Out of scope

- Malware or an attacker already running as the user on their machine.
- Physical access to an unlocked device (we rely on OS disk encryption and screen lock).
- A user deliberately installing a malicious but validly signed extension; we warn as Firefox does.
- Adversaries who need to de-anonymise users across sessions; that is Tor Browser's threat model.

---

## 5. Architecture: how Evergreen modifies Firefox

### 5.1 Repository layout

The repository **never contains Firefox source**. It contains a pin to a
Firefox release and everything needed to turn that release into Evergreen.

```
eg.py                      # the build tool: python eg.py --help
upstream.json              # pinned Firefox version, tarball SHA-512, Mozilla release key + subkey
tools/eg/                  # eg.py's implementation (fetch, verify, prepare, mach, dev harness, checks)
mozconfigs/                # common + windows / linux / macos build configuration
patches/                   # series + *.patch, each with a rationale header (§5.3)
src/                       # new files copied into the Firefox tree ("overlay")
  browser/components/evergreen/   # all Evergreen UI and startup code (§6)
  browser/locales/en-US/browser/evergreen.ftl
branding/evergreen/        # names, icons, installer text (on top of Firefox's unofficial branding)
prefs/evergreen.js         # Evergreen default preferences (§8.2)
distribution/              # extensions bundled at packaging time (uBlock Origin)
dev/                       # dev harness: run src/ on a stock Firefox without building
tests/                     # unit/ (Node), python/ (tooling), smoke/ (Marionette, real Firefox)
.github/workflows/ci.yml   # fast checks + smoke test; no builds, no servers
```

### 5.2 Ways of changing Firefox, cheapest first

| Rung | Mechanism | Rebase cost | Examples |
|---|---|---|---|
| 1 | **Build configuration** (`mozconfigs/`) | ~none | branding, app name, update channel, crash reporter off |
| 2 | **Default prefs** (`prefs/evergreen.js`, appended by `eg.py prepare` to Evergreen's branding prefs file `firefox-branding.js`; Firefox reads `defaults/preferences` in reverse alphabetical order, so it loads after `firefox.js` and wins, and the package manifest always includes it) | very low | hardening, telemetry off, vertical tabs on |
| 3 | **New files** (`src/` overlay) | low; breaks only if an API we call changes | Evergreen UI modules, CSS, Fluent strings, tests |
| 4 | **Patches to upstream files** | high; can conflict every release | hook points that new files cannot provide |

`eg.py prepare` enforces the ladder: the overlay refuses to replace any file
that exists upstream, so the only way to change an upstream file is a
documented patch.

### 5.3 Patch rules and budget

- **One concern per patch.** Each patch starts with a header:

  ```
  Evergreen-Patch: register-evergreen-component
  Why: Builds browser/components/evergreen ...
  Upstream: not upstreamable (Evergreen-specific)
  Drop-when: never; this is Evergreen's entry point
  Owner: Evergreen maintainers
  ```

- **Prefer hook patches.** A patch adds a small extension point that
  Evergreen's overlay code uses; it does not carry Evergreen logic itself.
- **Security-sensitive paths need extra review.** `eg.py lint` rejects a patch
  touching `security/`, `netwerk/`, `dom/`, `js/`, `ipc/`, the updater or the
  extensions framework unless it carries a `Security-Review:` header naming
  the second reviewer.
- **Upstream what we can.** If a hook is useful beyond Evergreen, file it
  upstream and record the bug number.
- **Budget: ≤ 30 patches and ≤ 2,000 changed upstream lines at v1.0.**
  `eg.py status` and `eg.py lint` report it. **Current: 2 patches, 4 lines**
  (registering Evergreen's component; packaging the bundled extensions).

### 5.4 Tracking upstream

Evergreen follows the Firefox **Release** channel.

- **Version bumps.** Update `upstream.json`, then `eg.py check-patches` and
  `eg.py check-prefs` show within seconds whether the patches still apply and
  every pref still exists (they fetch only the needed files from Mozilla's
  GitHub mirror). `eg.py fetch --pin` then verifies the signed tarball and
  pins its hash.
- **Rebase early against Beta.** `eg.py check-patches --ref <beta tag>`
  catches conflicts weeks before release day.
- **Dot releases** take the same path and normally need no manual work.
- **Measure G2.** Record the time from Mozilla's release to Evergreen's for
  every release.

---

## 6. The Evergreen UI layer

### 6.1 Where the code lives and how it loads

All Evergreen UI code is new files under `browser/components/evergreen/`:

| File | Role |
|---|---|
| `Spaces.sys.mjs`, `Archive.sys.mjs`, `SearchDefaults.sys.mjs` | Pure logic with no Firefox dependencies, unit-tested in Node |
| `SpacesStore.sys.mjs`, `ArchiveStore.sys.mjs` | Persistence in the profile (`evergreen/spaces.json`, `evergreen/archive.jsonlz4`) |
| `EvergreenWindow.sys.mjs` | Per-window UI: sidebar header, Space switcher, editor, Archive panel, tab menu, shortcuts |
| `EvergreenStartup.sys.mjs` | App-wide: first-run defaults, archive timer, history-clearing hooks |
| `evergreen.css`, `Evergreen.manifest`, `moz.build` | Styles, category registrations, build integration |

Modules are `MOZ_SRC_FILES` (served at `moz-src:///browser/components/evergreen/`)
and import each other by relative URL. `Evergreen.manifest` registers them
with Firefox's category manager (`browser-first-window-ready`,
`browser-window-delayed-startup`, `browser-window-unload`,
`browser-quit-application-granted`), which is how Firefox's own components
hook into windows. As a result the **only upstream change needed to load
Evergreen is one line** listing the directory in
`browser/components/moz.build`.

Evergreen avoids importing Firefox modules by URL where it can, because those
URLs move between releases. Instead it uses the globals every browser window
already has (`gBrowser`, `SessionStore`, `ContextualIdentityService`,
`PrivateBrowsingUtils`) and `Services`.

### 6.2 Rules for privileged code

1. **Never parse markup from the web.** Titles, URLs, favicons and
   suggestions are written with `textContent`, attributes or Fluent
   arguments. Enforced by ESLint with `no-unsanitized`.
2. **No remote code**, no `eval` (Firefox already blocks `eval` in the parent
   process).
3. **Favicons come from Firefox's favicon service** (`page-icon:` URLs).
4. **Treat the content process as compromised.** No new `JSWindowActor`s
   without security review; validate every message if one is added.
5. **State stays local**, written atomically with `IOUtils`.
6. **Private windows get no Spaces and persist nothing.**
7. **Re-validate stored data.** Files in the profile can be edited by other
   software, so stored Spaces and archive entries are re-checked on load (for
   example, only `http(s)` archive entries are ever reopened).

### 6.3 Window layout

```
+------------------+--------------------------------------------------+
| o Personal   ... |  <  >  C  [ https://example.org             ]    |
| +--+--+--+--+    |--------------------------------------------------|
| |F |F |F |F |    |                                                  |
| +--+--+--+--+    |   Web content: a normal Firefox tab, inset in a  |
|   tab (kept)     |   rounded frame so the edge of browser chrome is |
|   tab            |   always clear                                   |
|   tab            |                                                  |
| + New Tab        |                                                  |
|                  |                                                  |
| [] . o .     +   |                                                  |
+------------------+--------------------------------------------------+
  F = Favorite (pinned tab)   o = current Space   [] = Archive   + = new Space
```

The prototype keeps Firefox's navigation toolbar at the top, which satisfies
principle 4 directly. Moving navigation into the sidebar (as Arc does) is
planned as a later step and must keep the identity and permission
indicators.

### 6.4 Dev harness

A Firefox build takes hours, so UI work uses `eg.py dev`: it copies an
installed Firefox into `.eg/dev/` (the user's own install is never touched),
adds an autoconfig file that applies `prefs/evergreen.js` and loads
`src/browser/components/evergreen/` straight from the repository, and starts
the copy with its own profile. Edit, restart, see the change. The same files
are compiled into real builds unchanged.

The harness uses Firefox's administrator configuration mechanism with its
sandbox turned off, which runs with full privileges. It is a development
tool only and is never part of a build.

---

## 7. Features

### 7.0 Arc → Evergreen mapping

| Arc | Evergreen | Built on | Status |
|---|---|---|---|
| Sidebar with vertical tabs | Evergreen sidebar | Firefox sidebar + vertical tabs | **Prototype** |
| Spaces | Spaces | Tab hiding + SessionStore values | **Prototype** |
| Profiles (per Space) | Space sign-in identity | Firefox containers | **Prototype** |
| Favorites | Favorites | Firefox pinned tabs | **Prototype** (uses Firefox's pinned-tab grid) |
| Pinned tabs and folders | "Keep in Space" now; folders later | Tab values; native tab groups | **Prototype** (keep) / planned |
| Today tabs + auto-archive | Archive | New (Evergreen) | **Prototype** |
| Split View (up to 4) | Split view (2 panes) | Native split view (149+) | Planned |
| Command Bar | Command bar | Firefox address bar providers | Planned |
| Peek | Peek | Normal tab shown in an overlay | Planned |
| Little Arc | Mini window | Compact window for external links | Planned |
| Air Traffic Control | Link routing | New (Evergreen) | Planned |
| Boosts | Deferred; CSS-only if ever | — | — |
| Easels, Notes, Library, Arc Max (AI) | Not planned | — | — |

### 7.1 Sidebar

Built **on** Firefox's vertical-tabs sidebar, not as a replacement: Firefox's
tab strip already handles drag and drop, multi-select, tab groups,
accessibility and extensions. Evergreen adds the Space header (name, colour,
menu) above it and the Space switcher (archive, Space dots, new Space) below
it, tints the window with the Space's colour, and expands the sidebar on
first run (Firefox starts it collapsed; afterwards Firefox remembers the
user's choice). In the collapsed sidebar the switcher stacks vertically.

**Origin and permission visibility (principle 4).** Web content is inset in
a chrome-drawn frame, so anything that looks like browser UI inside the frame
is visibly part of the page. While the navigation toolbar stays at the top,
the identity box and permission prompts work exactly as in Firefox.

### 7.2 Spaces

A **Space** is a named, coloured set of tabs. Users switch Spaces with the
dots, `Ctrl+Shift+1…9`, or by selecting a tab that belongs to another Space
(for example from the address bar's "Switch to tab").

**Data model**

- Space metadata (id, name, colour, sign-in identity) is in
  `evergreen/spaces.json` in the profile. An empty name shows the localized
  default ("Personal").
- Each tab records its Space with `SessionStore.setCustomTabValue`, so
  membership survives restarts and crash recovery. Each window records its
  active Space the same way.
- Switching hides the other Spaces' tabs with `gBrowser.hideTab`, which also
  takes them out of `Ctrl+Tab` and `Ctrl+1…8`. If an extension reveals one of
  them, Evergreen hides it again.
- **Pinned tabs are Favorites, shared by every Space.** Firefox cannot hide
  pinned tabs, and Arc's Favorites are shared too.

**Sign-in identities (security model)**

- When a user creates a Space, they choose whether it has **separate
  sign-ins**. If so, Evergreen creates a Firefox container for it.
- Containers isolate cookies, local storage, IndexedDB, cache and other site
  data, so being signed in to a site in one Space does not sign you in to it
  in another, and trackers cannot link activity across those Spaces through
  stored state. This is stronger than Arc's default and comes from the engine.
- History, bookmarks, extensions and settings are **shared**; the editor says
  so plainly.
- New tabs (`Ctrl+T`, the + button, bookmarks opened in a new tab) open in
  the active Space's container. In the prototype this wraps the window's
  `openTrustedLinkIn`; a real build will turn it into a small hook patch.
  Links opened from a page inherit that page's container, as in Firefox.
- A container is fixed when a tab is created, so **moving** a tab to a Space
  with different sign-ins reopens the page there.
- **Deleting** a Space archives its tabs. If it had separate sign-ins, its
  container and that container's cookies and site data are deleted; the
  confirmation says so.

**Known limitations**

- Links that arrive from other apps open in the active Space but in the
  default container. Routing them properly is part of the mini window and
  link-routing work (§7.7, §7.8).
- Extensions that also hide tabs (other tab-grouping extensions) can conflict
  with Spaces.

### 7.3 Favorites, kept tabs and the Archive

- **Favorites** are Firefox's pinned tabs, shown as the icon grid at the top
  of the vertical tab strip.
- **Kept tabs** ("Keep in Space" in the tab menu) are never archived and are
  marked in the tab list. Per-Space folders on native tab groups come later.
- **Auto-archive.** A tab that has not been used for
  `evergreen.archive.afterHours` (default **12**; 0 turns it off) is closed and
  added to the **Archive**. Pinned, kept, selected, audible and screen-sharing
  tabs are never archived. The first check runs ten minutes after startup, so
  session restore finishes and the user sees their tabs first.
- **The Archive** stores URL, title, Space and time, compressed, limited to
  500 entries and 30 days. The Archive panel reopens an entry in its Space.
  Only `http(s)` pages are recorded; blank tabs just close.
- **The Archive is browsing history** and is wiped by Clear Recent History,
  clear-on-shutdown and "Forget About This Site"
  (`browser:purge-session-history` and `...-for-domain`). Nothing is archived
  from private windows.

### 7.4 Split view

Evergreen will use **Firefox's native split view** (149+) unchanged and show a
split pair as one row in the sidebar. More than two panes should be built
upstream first.

### 7.5 Command bar

`Ctrl+T` will open a centred, floating instance of the **Firefox address bar**
rather than a new search UI, with providers added for switching Space, moving
a tab to a Space, searching the Archive and Evergreen commands.

### 7.6 Peek

Links from a Favorite to another site will open in **Peek**, a floating panel
that is a normal tab in the same Space and container (principle 2).

### 7.7 Mini window (links from other apps)

Links from other apps will open in a compact window that can be promoted to a
Space. Option: "Open links from other apps in a temporary container",
**off by default** (decision Q8).

### 7.8 Link routing

Rules mapping hosts to Spaces will apply only to **new top-level loads**,
never to navigation inside a tab (that would break OAuth/SSO redirects and let
a site move itself between containers).

### 7.9 Deferred and not planned

- **Boosts**: if built, **CSS only**, local, never synced or shared.
- **Four-pane split view**: upstream-first.
- **Easels, Notes, Library, AI features**: not planned.

---

## 8. Security and privacy hardening

### 8.1 Engine and build: keep upstream's protections

| Must keep | Why |
|---|---|
| Fission and the content-process sandbox at upstream levels | The primary defence against engine exploits |
| Wasm-sandboxed libraries (never `--without-wasm-sandboxed-libraries`) | Isolates font, spelling, media and XML libraries in-process |
| Add-on signature enforcement compiled in (`MOZ_REQUIRE_SIGNING=1`) | Users can't be talked into turning it off |
| Upstream toolchains via `mach bootstrap` | We already trust Mozilla's toolchain |
| The same compiler hardening and allocator as upstream | No "faster" builds that drop hardening |
| The standard Firefox user agent | A distinct UA makes Evergreen users *more* fingerprintable |

`mozconfigs/common.mozconfig` states these rules, and the tooling tests fail
if a mozconfig option disables the sandbox, signing or wasm sandboxing.
Evergreen builds use their own branding, app name (`evergreen.exe`), vendor
and profile location (never sharing a profile with Firefox); the crash
reporter is disabled; telemetry reporting is not compiled in (we never set
`MOZILLA_OFFICIAL`); and the Windows default-browser agent, which reports to
Mozilla, is not built.

### 8.2 Default preferences

`prefs/evergreen.js` holds 63 defaults. `eg.py check-prefs` verifies every one
exists in the pinned Firefox, so typos and removed prefs are caught.
Highlights:

| Area | Default | Rationale |
|---|---|---|
| Layout | vertical tabs, sidebar shown, containers enabled, session restore on | Arc-style sidebar; Spaces live in the session |
| Transport | HTTPS-Only on; TLS 0-RTT off | Upgrade all loads; avoid early-data replay |
| DNS | DoH via **Quad9**, fallback mode (`network.trr.mode` 2) | Encrypted DNS through a non-profit, no-IP-logging resolver; strict mode available |
| Tracking | **ETP Strict** (set at first run), Global Privacy Control, cross-origin referrer trimming, WebRTC default address only | Firefox only applies a tracking-protection category when it is a user value, so Evergreen sets it once at first run unless the user has custom settings |
| Speculation | prefetch, DNS prefetch, speculative connections off | No connections the user didn't ask for |
| Attack surface | PDF scripting off; remote download checks off; logins fill only on request | Smaller surface; less data sent away |
| Search | **Ecosia** default (first run); suggestions off until opted in; trending, Firefox Suggest and sponsored suggestions off | See §8.7 |
| Fingerprinting | `privacy.resistFingerprinting` stays an opt-in | It breaks many sites |

Users can change all of these. A "defaults changed" indicator in Settings is
planned (principle 5).

**No `policies.json` by default.** Enterprise policies would let us lock
settings, but they also show "managed by your organization" and take the file
that real enterprise deployments need.

### 8.3 Data collection and sponsored content removed

- **Telemetry, studies and experiments**: not compiled in; data reporting,
  Normandy/Nimbus and recommendation feeds also off by pref.
- **Sponsored content**: sponsored top sites, stories and Firefox Suggest
  results off; weather and Pocket-style story feeds off.
- **Add-on recommendations**: off.
- **AI features**: Firefox's "Block AI enhancements" control set to blocked
  (`browser.ai.control.default`), and the chatbot, link previews, smart tab
  groups and smart window off. Users can re-enable individual features
  (on-device translations are a good candidate).
- **Crash reports**: crash reporter not built.
- **Firefox onboarding**: off; Evergreen will ship its own. Mozilla's
  first-run Terms of Use modal and data-collection privacy notice describe
  Mozilla's Firefox, not Evergreen, and are skipped (non-official builds
  already skip the Terms of Use modal).

### 8.4 Security services we keep on

Removing data collection must not remove security features that depend on
network updates. Evergreen **keeps** Remote Settings security lists (OneCRL,
CRLite, intermediate preloading, the add-on blocklist), the ETP tracker
lists, **Safe Browsing** with local lists (hashed prefixes only; remote
download checks off), AMO add-on updates, HSTS preload and Mozilla's root
store. These are Mozilla and Google services that Firefox itself uses; they
are not Evergreen servers.

### 8.5 Extensions

- **uBlock Origin** is bundled as a distribution add-on (pinned by URL and
  SHA-256 in `distribution/extensions.json`, copied in at packaging time),
  updates from AMO, and can be removed.
- Only AMO-signed extensions install.
- Evergreen adds **no** privileged extension APIs.

### 8.6 Things we deliberately don't change

The user agent, Mozilla's root store, and Fission, sandbox and process
settings.

### 8.7 Search

At first run Evergreen makes **Ecosia** the default engine, on the device.
Firefox ships Ecosia only for some locales and regions, and that copy
carries Mozilla's partner code. Where it is missing, Evergreen adds its own
Ecosia entry with no partner code. **Google** and **DuckDuckGo** stay in
Firefox's built-in list, one click away in Settings › Search, and **custom
engines** can be added from the same page. The default is applied once:
choosing another engine later is never overridden. Search suggestions (which
send keystrokes to the engine) start off and can be turned on in Settings.

---

## 9. Release engineering

### 9.1 Pipeline

```mermaid
flowchart LR
  A[Bump upstream.json] --> B[check-patches / check-prefs<br/>against Mozilla's mirror]
  B --> C[fetch: tarball, SHA512SUMS, .asc, KEY]
  C --> D{Signed by pinned<br/>key + subkey,<br/>hash matches?}
  D -- no --> X[Stop]
  D -- yes --> E[prepare: extract, patch,<br/>overlay, branding, prefs]
  E --> F[mach build + package<br/>on a Windows runner or PC]
  F --> G[Smoke test the built<br/>evergreen.exe]
  G --> H[Sign separately<br/>(M3)]
  H --> I[GitHub release: installer,<br/>portable zip, checksums]
```

- **Source verification.** `SHA512SUMS` must carry a valid signature from the
  Mozilla primary key **and** one of the signing subkeys pinned in
  `upstream.json`. Pinning the subkey matters: Mozilla rotated its signing
  subkey in August 2026 after the previous one was exposed, so a signature
  from the old subkey must be rejected even if a stale KEY file does not
  carry the revocation. The tarball hash is then pinned in `upstream.json`
  (`eg.py fetch --pin`), so machines without `gpg` verify against a hash that
  was itself established by a signature check.
- **Where builds run.** Release builds run in the *Windows build* workflow
  (`.github/workflows/windows-build.yml`) on GitHub's hosted Windows runners:
  it installs MozillaBuild, fetches and verifies the source, builds, packages
  a portable zip and an NSIS installer, smoke-tests the built `evergreen.exe`,
  and publishes a GitHub release. Developers can run the same steps on their
  own PC. Evergreen operates no build or update servers (principle 6).
- **Build settings.** `mozconfigs/common.mozconfig` + the platform file, plus
  `mozconfigs/ci.mozconfig` in CI (no test programs, no debug symbols, and
  sccache to speed up rebuilds). `--enable-bootstrap` downloads Mozilla's
  toolchains, so no Visual Studio install is needed.
- **Versions.** Evergreen `157.0-12` is Firefox 157.0 plus Evergreen build 12;
  the release tag is `v157.0-12`. The build number is the workflow run
  number, the number given when starting the workflow, or the one in a
  pushed tag.

### 9.2 Signing and keys

| Artifact | Signature |
|---|---|
| Windows installer and binaries | Authenticode (code-signing certificate) |
| Update packages | **MAR signature** with Evergreen's own key pair (public keys compiled into the updater) |
| Release files | Published SHA-256 checksums and a detached signature |

Keys are kept offline or in a hardware token, and signing is a separate step
from building, on a machine that receives only the build outputs.

### 9.3 Distribution

| Platform | Packages | Notes |
|---|---|---|
| **Windows** (first) | Installer (`-setup.exe`) and portable zip, on GitHub releases | x86-64 first; arm64 later. **Not code-signed yet**, so SmartScreen warns on first run |
| Linux | Tarball | The tarball is the reference security configuration; inside Flatpak, Firefox cannot use the user namespaces its Linux sandbox relies on |
| macOS | Signed, notarized DMG | Later |

### 9.4 Updates

- Firefox's updater with **Evergreen's MAR keys**, so a tampered or
  downgraded update is rejected even if the download location is compromised.
- **Static update files on GitHub releases**, no update server.
- The update request sends only what is needed to choose a package (version,
  platform, channel).
- Until the updater is set up (M3), builds use `--disable-updater` (so
  nothing asks Mozilla's update server about a product it does not know) and
  Evergreen is updated by installing a new release.

### 9.5 Reproducibility

Goal for v1.x: reproducible builds, verified by a second independent
builder. Until then, each release records the toolchain and source hashes.

---

## 10. Testing

| Suite | What it checks | Command |
|---|---|---|
| Lint | ESLint incl. `no-unsanitized` on all privileged code | `npm run lint` |
| UI unit tests | Spaces model, archive policy, search defaults (24 tests) | `npm test` |
| Tooling tests | Prefs parser, patch lint, real GPG verification with pinned keys, full prepare pipeline on a fake tarball, dev harness (21 tests) | `python -m unittest discover -s tests/python` |
| Patch / pref checks | Patches apply and prefs exist in the pinned Firefox | `eg.py check-patches`, `eg.py check-prefs` |
| **Smoke test** | Evergreen running in a real Firefox: prefs applied, first run (ETP Strict, Ecosia), Spaces and containers, Ctrl+T container, switching, keyboard shortcut, moving tabs across identities, auto-archive, Archive panel, history clearing, editor, restart persistence, deleting Spaces, private windows, no console errors (16 checks) | `python tests/smoke/smoke_test.py` |
| Prefs audit *(M0)* | Starts the *packaged* build and checks every default and build protection | — |
| Network egress test *(M0)* | Scripted session through a logging proxy; fails on any host outside the allowlist | — |
| Update test *(M3)* | Old → new release through a signed MAR; tampered MAR rejected | — |

CI runs lint, unit tests, tooling tests, patch and pref checks on every push,
and the smoke test in the Firefox installed on GitHub's Windows and Linux
runners (Firefox 156 at the time of writing; the smoke test also passes on
Firefox 136).

---

## 11. Milestones

| Milestone | Scope | Status |
|---|---|---|
| **M0 Foundations** | Build tooling, source verification, branding, prefs, uBO bundling, CI; Windows release builds; prefs audit and egress test; arkenfox/LibreWolf prefs review | Tooling, CI and the Windows build workflow done; prefs audit and egress test pending |
| **M1 Sidebar & Spaces** | Sidebar, Spaces with sign-in identities, Favorites, kept tabs, Archive | **Prototype done** (dev harness, smoke-tested); next: folders, navigation in the sidebar, polish |
| **M2 Multitasking** | Split view integration, command bar, Peek, mini window, link routing | Not started |
| **M3 Ship** | Code signing, MAR updates via GitHub releases, security review, public beta | Unsigned preview releases only |
| **Later** | Linux and macOS packages, CSS-only Boosts, 4-pane split (upstream-first), isolated-profile Spaces | — |

---

## 12. Decisions

| # | Question | Decision |
|---|---|---|
| — | Base | Thin patch layer on upstream Firefox (not a Zen fork, not an extension) |
| — | Channel | Firefox Release |
| Q1 | Platform order | **Windows first**; tooling, mozconfigs and CI stay cross-platform |
| Q2 | Build infrastructure | **No servers of our own.** Release builds run on GitHub's hosted Windows runners (or a developer's PC); downloads and future update files are static GitHub release assets |
| Q3 | Default search | **Ecosia**; Google and DuckDuckGo one click away; custom engines supported; suggestions off until opted in |
| Q4 | DNS over HTTPS | Quad9, fallback mode; strict mode available |
| Q5 | Safe Browsing key | Evergreen is a non-commercial personal project, so Google's free Safe Browsing API terms apply. Revisit if that changes (commercial use requires the paid Web Risk API) |
| Q6 | Mozilla services (Remote Settings, tracker lists, AMO, Sync) | Use them, as other Firefox derivatives do; confirm the terms before a public release |
| Q7 | Sync | Firefox Sync through Mozilla accounts (end-to-end encrypted); Spaces are not synced in v1 |
| Q8 | Temporary container for links from other apps | Off by default, offered in onboarding |
| Q9 | Licence | MPL-2.0 (`LICENSE`) |
| Q10 | Name and trademark | Evergreen; no Mozilla trademarks (handled by the branding). Note that "Evergreen" is a common software term (Microsoft uses it for the auto-updating WebView2 runtime), so check for conflicts before a public launch |
| Q11 | Strict fingerprinting resistance | Opt-in, with an explanation of site breakage |

### Still open

- The code-signing certificate for Windows (needed for M3).
- Whether to ship a privileged Windows update service, or only user-level
  updates.
- Exact search-suggestion and onboarding flow.

---

## 13. References

- Firefox 136 release notes: sidebar and vertical tabs — <https://www.mozilla.org/firefox/136.0/releasenotes>
- Firefox tab groups (138) — <https://heise.de/-10367703>
- Firefox 149 split view — <https://www.gigazine.net/gsc_news/en/20260325-firefox-149/>
- Firefox 148 AI controls / "Block AI enhancements" — <https://www.techspot.com/news/111453-firefox-148-rolls-out-promised-ai-kill-switch.html>
- Mozilla signing-key update, 2026-08-10 — <https://blog.mozilla.org/security/2026/08/10/updated-gpg-key-for-signing-firefox-and-thunderbird-releases/>
- Arc maintenance-mode status and Zen comparison — <https://supasidebar.com/blog/zen-vs-arc>
- Arc Boosts vulnerability CVE-2024-45489 — <https://security-tracker.debian.org/tracker/CVE-2024-45489>
- Google Safe Browsing usage terms — <https://developers.google.com/safe-browsing/v4/usage-limits>
- Zen Browser source — <https://github.com/zen-browser/desktop>
- LibreWolf — <https://librewolf.net/>
- arkenfox user.js — <https://github.com/arkenfox/user.js>
- Firefox source docs — <https://firefox-source-docs.mozilla.org/>
- Mozilla trademark policy — <https://www.mozilla.org/foundation/trademarks/policy/>
