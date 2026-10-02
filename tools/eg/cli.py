# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Command-line entry point for eg.py."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import checks, dev, extensions, mach, prepare, release, upstream
from . import patches as patchlib
from .config import PREFS_FILE, EgError, host_platform, load_upstream, work_dir
from .prefs import load_prefs

PLATFORMS = ("windows", "linux", "macos")


def cmd_status(args) -> None:
    up = load_upstream()
    tree = prepare.tree_dir(up)
    state = prepare.read_state(tree)
    files = upstream.release_files(up)
    print(f"Firefox pin:     {up.version} (sha512 {'pinned' if up.sha512 else 'NOT pinned'})")
    print(f"Host platform:   {host_platform()}")
    print(f"Work dir:        {work_dir()}")
    print(f"Source tarball:  {'downloaded' if files.tarball.exists() else 'not downloaded'}")
    print(f"Prepared tree:   {tree if state else 'not prepared'}")
    print(patchlib.budget_report(patchlib.load_series()))


def cmd_fetch(args) -> None:
    up = load_upstream()
    files = upstream.fetch(up, force=args.force)
    upstream.verify(up, files, pin=args.pin)


def cmd_prepare(args) -> None:
    up = load_upstream()
    platform = args.platform or host_platform()
    if args.refresh:
        prepare.refresh(up, platform)
        return
    files = upstream.release_files(up)
    if not files.tarball.exists():
        raise EgError("Source not downloaded; run `eg.py fetch` first.")
    upstream.verify(up, files)
    prepare.prepare(up, files.tarball, platform)


def _tree():
    up = load_upstream()
    tree = prepare.tree_dir(up)
    if not prepare.read_state(tree):
        raise EgError("No prepared tree; run `eg.py fetch` and `eg.py prepare` first.")
    return tree


def cmd_bootstrap(args) -> None:
    mach.bootstrap(_tree())


def cmd_build(args) -> None:
    mach.build(_tree(), faster=args.faster)


def cmd_package(args) -> None:
    mach.package(_tree(), installer=not args.no_installer)


def cmd_installer(args) -> None:
    mach.build_installer(_tree())


def cmd_collect(args) -> None:
    up = load_upstream()
    print("Collecting release files")
    release.collect(_tree(), up, args.platform or host_platform(), args.build, Path(args.out))


def cmd_run(args) -> None:
    mach.run(_tree(), args.extra)


def cmd_dev(args) -> None:
    dev.dev(args.firefox, args.fresh, args.extra, dry_run=args.dry_run)


def cmd_lint(args) -> None:
    problems = patchlib.lint()
    prefs = load_prefs(PREFS_FILE)  # raises on syntax errors
    print(f"prefs/evergreen.js: {len(prefs)} prefs, syntax OK")
    print(patchlib.budget_report(patchlib.load_series()))
    if problems:
        raise EgError("Patch series problems:\n  " + "\n  ".join(problems))
    print("Patch series OK")


def cmd_check_patches(args) -> None:
    up = load_upstream()
    if args.tree:
        patchlib.apply_series(prepare.tree_dir(up), check=True)
    else:
        checks.check_patches_against_mirror(up, args.ref)


def cmd_check_prefs(args) -> None:
    up = load_upstream()
    if args.tree:
        sources = checks.read_pref_sources_from_tree(prepare.tree_dir(up))
    else:
        sources = checks.read_pref_sources_from_mirror(up, args.ref or up.mirror_tag)
    checks.check_prefs(sources)


def cmd_fetch_extensions(args) -> None:
    extensions.fetch_extensions(pin=args.pin)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="eg.py", description="Evergreen build tool")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="show pin, work dir and patch budget").set_defaults(func=cmd_status)

    s = sub.add_parser("fetch", help="download and verify the pinned Firefox source")
    s.add_argument("--force", action="store_true", help="download again even if cached")
    s.add_argument("--pin", action="store_true", help="pin the verified sha512 in upstream.json")
    s.set_defaults(func=cmd_fetch)

    s = sub.add_parser("prepare", help="extract, patch and overlay Evergreen onto the source")
    s.add_argument("--platform", choices=PLATFORMS, help="target platform (default: host)")
    s.add_argument("--refresh", action="store_true",
                   help="only re-copy Evergreen files into the prepared tree")
    s.set_defaults(func=cmd_prepare)

    sub.add_parser("bootstrap", help="install Firefox build toolchains (mach bootstrap)").set_defaults(func=cmd_bootstrap)

    s = sub.add_parser("build", help="build Evergreen (mach build)")
    s.add_argument("--faster", action="store_true", help="front-end only rebuild (mach build faster)")
    s.set_defaults(func=cmd_build)

    s = sub.add_parser("package", help="package the build (and the Windows installer)")
    s.add_argument("--no-installer", action="store_true")
    s.set_defaults(func=cmd_package)

    sub.add_parser("installer", help="build the Windows installer from the package").set_defaults(func=cmd_installer)

    s = sub.add_parser("collect", help="copy packages to release names with SHA256SUMS")
    s.add_argument("--out", required=True, help="output directory")
    s.add_argument("--build", default="1", help="Evergreen build number (default 1)")
    s.add_argument("--platform", choices=PLATFORMS, help="target platform (default: host)")
    s.set_defaults(func=cmd_collect)

    s = sub.add_parser("run", help="run the built browser (mach run)")
    s.add_argument("extra", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("dev", help="run Evergreen's UI on a copy of an installed Firefox")
    s.add_argument("--firefox", help="Firefox install directory (default: standard location)")
    s.add_argument("--fresh", action="store_true", help="start from an empty dev profile")
    s.add_argument("--dry-run", action="store_true", help="set up but do not launch")
    s.add_argument("extra", nargs=argparse.REMAINDER, help="extra Firefox arguments after --")
    s.set_defaults(func=cmd_dev)

    sub.add_parser("lint", help="check the patch series and prefs file").set_defaults(func=cmd_lint)

    s = sub.add_parser("check-patches", help="check patches apply (mirror or prepared tree)")
    s.add_argument("--tree", action="store_true", help="check against the prepared tree")
    s.add_argument("--ref", help="mirror ref (default: the pinned release tag)")
    s.set_defaults(func=cmd_check_patches)

    s = sub.add_parser("check-prefs", help="check every Evergreen pref exists in Firefox")
    s.add_argument("--tree", action="store_true", help="check against the prepared tree")
    s.add_argument("--ref", help="mirror ref (default: the pinned release tag)")
    s.set_defaults(func=cmd_check_prefs)

    s = sub.add_parser("fetch-extensions", help="download bundled extensions")
    s.add_argument("--pin", action="store_true", help="pin unpinned extensions' URL and SHA-256")
    s.set_defaults(func=cmd_fetch_extensions)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "extra", None) and args.extra[:1] == ["--"]:
        args.extra = args.extra[1:]
    try:
        args.func(args)
    except EgError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0
