# Evergreen

A security-focused desktop browser built on Firefox, with an Arc-style
sidebar and multitasking: Spaces, Favorites, auto-archiving tabs, split view,
a command bar, Peek and a mini window for links from other apps.

Evergreen is a thin layer on upstream Firefox Release: build configuration,
default prefs, Evergreen's own UI code and a small, documented set of patches
(currently four, eight lines in all). Keeping the diff small lets us ship each
Firefox security release quickly. Evergreen runs no servers; everything it adds
lives on your device.

**Status:** Evergreen 0.1, an early preview built on Firefox 157.0. Spaces
(with optional separate sign-ins), Favorites, kept tabs, auto-archive, a
start page and an Arc-style new-tab search box, hardened defaults and Ecosia
as the default search engine work, and pass an end-to-end test both in the
built Evergreen and through the dev harness in Firefox 136 and 156. Windows
is the first platform.

## Download (Windows)

Get the latest **[release](https://github.com/Eplico/Pine/releases)**:

- **`Evergreen-0.1-win64-setup.exe`**: the installer.
- **`Evergreen-0.1-win64-portable.zip`**: unzip anywhere and run `evergreen\evergreen.exe`.

These are early preview builds, made by this repository's
[Windows build workflow](.github/workflows/windows-build.yml) from the
signature-checked Firefox source. They are **not code-signed yet**, so
Windows SmartScreen shows "Windows protected your PC": choose **More info**,
then **Run anyway**. Each release lists SHA-256 checksums. There are no
automatic updates yet; install a newer release to update.

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

## Licence

[MPL-2.0](LICENSE), like Firefox.
