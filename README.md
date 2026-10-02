# Evergreen

A security-focused desktop browser built on Firefox, with an Arc-style
sidebar and multitasking: Spaces, Favorites, auto-archiving tabs, split view,
a command bar, Peek and a mini window for links from other apps.

Evergreen is a thin layer on upstream Firefox Release: build configuration,
default prefs, Evergreen's own UI code and a small, documented set of patches
(currently one, a single line). Keeping the diff small lets us ship each Firefox
security release quickly. Evergreen runs no servers; everything it adds
lives on your device.

**Status:** working prototype. Spaces (with optional separate sign-ins),
Favorites, kept tabs, auto-archive, hardened defaults and Ecosia as the
default search engine run on a stock Firefox through the dev harness, and
pass an end-to-end test in Firefox 136 and 156. The first full Evergreen
build is next. Windows is the first platform.

> The repository is still named `Pine`; the product is Evergreen.

## Try it

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
