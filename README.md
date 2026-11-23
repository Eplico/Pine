# Evergreen

A security-focused desktop browser built on Firefox, with an Arc-style
sidebar and multitasking: Spaces, Favorites, auto-archiving tabs, split view,
a command bar, Peek and a mini window for links from other apps.

Evergreen is a thin layer on upstream Firefox Release: build configuration,
default prefs, Evergreen's own UI code and a small, documented set of patches
(currently five, ten lines in all). Keeping the diff small lets us ship each
Firefox security release quickly. Evergreen runs no servers; everything it adds
lives on your device.

**Status:** an early preview (0.2), built on Firefox 160.0.1. Spaces
(with optional separate sign-ins), big Favorites tiles, kept tabs, auto-archive, a
compact Arc-style address bar, Arc-style popup windows, a
start page and an Arc-style new-tab search box, hardened defaults and Ecosia
as the default search engine work, and pass an end-to-end test both in the
built Evergreen and through the dev harness in Firefox 136 and 156. Windows
is the first platform; Linux (x86-64) has its own build workflow (below).

## Download (Windows)

Get the **[latest release](https://github.com/Eplico/Pine/releases/latest)**:

- **`Evergreen-<version>-win64-setup.exe`**: the installer.
- **`Evergreen-<version>-win64-portable.zip`**: unzip anywhere and run `evergreen\evergreen.exe`.

These are early preview builds, made by this repository's
[Windows build workflow](.github/workflows/windows-build.yml) from the
signature-checked Firefox source. They are **not code-signed yet**, so
Windows SmartScreen shows "Windows protected your PC": choose **More info**,
then **Run anyway**. Each release lists SHA-256 checksums. On Windows, Evergreen
tells you when a newer release is out (a bar in the window) and opens its
download page; you install it over the old one and your data stays.

## Download (Linux)

Get **`evergreen-install`** from the newest release tagged
`v<version>-linux` (on the [releases page](https://github.com/Eplico/Pine/releases))
and run it:

```
python3 evergreen-install
```

It downloads the release, checks it against the release's `SHA256SUMS.txt`,
installs it under `~/.local/share/evergreen` and adds **Evergreen to your app
menu** with its icon (pin it from there). When a newer release is out,
Evergreen shows a bar: **Update**, then **Restart**. Your tabs, Spaces and
logins stay: the profile lives outside the install folder, and updates keep the
install folder's path (Firefox ties a profile to it). Moving from a copy you
unpacked yourself? The first start offers to use your earlier data.

The daily check is one request to GitHub's public release list, with no
account, cookies or identifiers. Set `evergreen.update.check` to `false` in
`about:config` to turn it off.

> The repository is still named `Pine`; the product is Evergreen.

## Try the UI without installing

You need Python 3.11+ and Firefox installed.

```
python eg.py dev
```

This runs Evergreen's UI in a copy of your installed Firefox with its own
profile. Your own Firefox and profile are not touched.

## Documentation

- [Design document](docs/design.md): goals, threat model, architecture, features, decisions
- [Development guide](docs/development.md): dev harness, tests, building on Windows, patches
- [Handoff](docs/handoff.md): current state, how to release, known issues and next steps

## Licence

[MPL-2.0](LICENSE), like Firefox.
