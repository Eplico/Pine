# Developing Evergreen

Evergreen is developed Windows-first. Everything here also works on Linux
and macOS unless a step says otherwise. The design and the reasons behind it
are in [design.md](design.md).

There are two ways to run Evergreen:

| | Dev harness (`eg.py dev`) | Real build (`eg.py build`) |
|---|---|---|
| What runs | Your installed Firefox (a copy), with Evergreen's UI and prefs loaded from this repo | Evergreen compiled from Firefox source |
| Setup | Python + Firefox | MozillaBuild, ~60 GB disk, toolchains |
| Edit → see the change | Restart the dev browser | `eg.py prepare --refresh` + `eg.py build --faster` |
| Branding, installer, updater | Firefox's | Evergreen's |
| Use it for | UI work, Spaces, prefs | Release builds, branding, build config |

## What you need

- **Python 3.11+** ([python.org](https://www.python.org/downloads/)). `eg.py` uses only the standard library.
- **Git for Windows**, which also provides `gpg` for verifying Firefox's source signature.
- **Firefox (Release)** installed, for the dev harness.
- **Node.js 22**, for lint and unit tests.
- For real builds only: **MozillaBuild**, Mozilla's Windows build environment
  (see [Building Firefox on Windows](https://firefox-source-docs.mozilla.org/setup/windows_build.html)),
  16 GB RAM or more, and about 60 GB free on an NTFS drive.

## Fast loop: the dev harness

```
python eg.py dev
```

This copies `C:\Program Files\Mozilla Firefox` into `.eg\dev\firefox` (your
own Firefox is never modified), applies `prefs\evergreen.js`, loads
`src\browser\components\evergreen\` straight from the repository, and starts
the copy with its own profile in `.eg\dev\profile`.

Edit the code, close the dev browser, run `python eg.py dev` again.

- `python eg.py dev --fresh` starts with an empty profile (first-run behaviour again).
- `python eg.py dev --firefox "D:\Apps\Firefox"` uses a Firefox installed elsewhere.
- Debug Evergreen's code with the **Browser Toolbox** (`Ctrl+Alt+Shift+I`),
  which is enabled in the dev copy.

The harness works through Firefox's administrator configuration file with
its sandbox turned off, which runs with full privileges. It is a
development tool and never part of a build.

## Tests

```
npm ci                                      # once
npm run lint                                # ESLint, incl. no-unsanitized on privileged code
npm test                                    # UI unit tests (Spaces, Archive, search defaults)
python -m unittest discover -s tests/python # tooling tests
python eg.py lint                           # patch series headers/budget, prefs syntax
python eg.py check-patches                  # patches apply to the pinned Firefox
python eg.py check-prefs                    # every pref in prefs/evergreen.js exists in Firefox
python tests/smoke/smoke_test.py            # Evergreen in your installed Firefox, end to end
python tests/smoke/smoke_test.py --screenshots shots
```

The smoke test drives a real Firefox over Marionette (Firefox's built-in
automation protocol) and checks Spaces, containers, archiving, history
clearing, restarts, private windows and the default prefs. CI runs all of the
above on every push, including the smoke test in the Firefox installed on
GitHub's Windows and Linux runners.

## Real build (Windows)

These are the same steps the Windows build workflow runs.

1. Install MozillaBuild and open `C:\mozilla-build\start-shell.bat`. Run every
   `eg.py` build command from that shell.
2. Use a short working directory, because Firefox's tree is deep:
   `export EG_WORK_DIR=/c/eg`
3. `python eg.py fetch --pin` downloads the Firefox 157.0 source (~600 MB)
   and verifies it. `SHA512SUMS` must be signed by Mozilla's release key
   **and** a signing subkey pinned in `upstream.json`, and the tarball must
   match it. `--pin` records the verified hash in `upstream.json`: commit it.
   Without `gpg`, `fetch` accepts the source only if it matches an already
   pinned hash.
4. `python eg.py prepare` extracts the source, applies `patches/`, and adds
   Evergreen's files, branding, prefs and mozconfig.
5. Toolchains (clang, Rust, the Windows SDK, NSIS, the WASI sysroot for
   sandboxed libraries) are downloaded automatically by the first build
   (`--enable-bootstrap`), several GB once. `python eg.py bootstrap` runs
   Mozilla's full bootstrap if you prefer to do it up front.
6. `python eg.py build`. The first build takes one to several hours depending
   on the machine. After changing only Evergreen's front-end files, run
   `python eg.py prepare --refresh` and `python eg.py build --faster`.
7. `python eg.py run` starts the built Evergreen.
8. `python eg.py fetch-extensions --pin` once (pins uBlock Origin), then
   `python eg.py package` builds the portable zip and, from it, the installer
   (`obj-evergreen\dist\evergreen-157.0.en-US.win64.zip` and
   `...win64.installer.exe`). `python eg.py collect --out release` checks the
   zip and copies both to release names with `SHA256SUMS.txt`.
9. `python tests/smoke/smoke_test.py --binary <unzipped>\evergreen\evergreen.exe`
   runs the end-to-end checks against the build.

`python eg.py status` shows the pinned version, what has been downloaded and
prepared, and the patch budget.

## Publishing a Windows release

Releases are built by the **Windows build** workflow
(`.github/workflows/windows-build.yml`) on GitHub's Windows runners:

1. On GitHub: **Actions › Windows build › Run workflow** (leave *Publish*
   ticked). Or push a tag such as `v157.0-3`.
2. The *build* job installs MozillaBuild, fetches and verifies the Firefox
   source, builds Evergreen (about two hours from scratch; sccache makes
   later builds faster), packages the portable zip and the installer, and
   uploads them as the *evergreen-windows* artifact.
3. The *test-and-publish* job runs the smoke test against the packaged
   `evergreen.exe` and publishes a GitHub pre-release named after the
   Firefox version and the build number, with `SHA256SUMS.txt`.
4. If a step fails, the *build-diagnostics* artifact has the mozconfig,
   `config.log` and a listing of the build's `dist\`; *smoke-diagnostics* has
   the smoke-test screenshots and the browser log.
5. To re-test or re-publish a build without compiling it again (say, after
   fixing the smoke test), run the workflow with **from_run** set to the
   build's run ID (the number in its URL).

The release notes come from `.github/release-notes.md`.

## Moving to a new Firefox release

1. Set the new version in `upstream.json` and set `sha512` to `null`.
2. `python eg.py check-patches` and `python eg.py check-prefs` show in
   seconds whether the patches still apply and every pref still exists.
3. Fix any patch that no longer applies (below), and remove prefs Firefox
   dropped.
4. `python eg.py fetch --pin`, `prepare`, `build`, then run the smoke test.

To catch conflicts early, check against Beta:
`python eg.py check-patches --ref FIREFOX_158_0b5_RELEASE` (any tag on
Mozilla's GitHub mirror works).

## Writing a patch

Prefer, in order: a mozconfig option, a pref, a new file under `src/`, and
only then a patch (design doc §5.2). `eg.py prepare` refuses to let `src/`
overwrite an upstream file.

1. `python eg.py prepare`, then in the prepared tree:
   `git init -q && git add -A && git commit -qm upstream` (takes a few minutes).
2. Make the change and save `git diff` as `patches/000N-short-name.patch`.
3. Add the header (`Evergreen-Patch`, `Why`, `Upstream`, `Drop-when`,
   `Owner`, and `Security-Review` for security-sensitive paths) above the
   diff, and add the file name to `patches/series`.
4. `python eg.py lint` and `python eg.py check-patches`.

## Repository layout

See [design.md §5.1](design.md#51-repository-layout).

## Troubleshooting

- **"run eg.py from the MozillaBuild shell"**: `bootstrap`, `build`,
  `package` and `run` call Mozilla's `mach`, which needs MozillaBuild on
  Windows. `fetch`, `prepare`, `dev` and the checks work from any shell.
- **Signature check failed**: do not build from that source. If Mozilla has
  rotated its signing subkey, confirm the new fingerprint from Mozilla's own
  announcement before adding it to `upstream.json`.
- **Paths too long**: set `EG_WORK_DIR` to a short path such as `C:\eg`.
