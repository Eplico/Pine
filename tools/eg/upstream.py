# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Download and verify the pinned Firefox source release.

Trust model:
  * SHA512SUMS must carry a valid OpenPGP signature made by one of the
    signing subkeys pinned in upstream.json, belonging to the pinned Mozilla
    primary key. The KEY file is downloaded next to the release, but it is
    only a transport for the public key: the fingerprints that matter are the
    ones committed in this repository.
  * The tarball's SHA-512 must match its line in the verified SHA512SUMS.
  * Once verified, the hash is pinned in upstream.json (`eg.py fetch --pin`).
    Machines without gpg (typical on Windows) then rely on that pinned hash,
    which was itself established by a signature check.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import EgError, Upstream, save_upstream_sha512, work_dir


@dataclass
class ReleaseFiles:
    tarball: Path
    sums: Path
    sums_sig: Path
    key: Path


def cache_dir(up: Upstream) -> Path:
    return work_dir() / "cache" / f"firefox-{up.version}"


def release_files(up: Upstream) -> ReleaseFiles:
    d = cache_dir(up)
    return ReleaseFiles(
        tarball=d / up.tarball_name,
        sums=d / "SHA512SUMS",
        sums_sig=d / "SHA512SUMS.asc",
        key=d / "KEY",
    )


def download(url: str, dest: Path, quiet: bool = False) -> None:
    """Download `url` to `dest` atomically (via a .part file)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "evergreen-eg"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(part, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if not quiet and total and sys.stderr.isatty():
                    pct = done * 100 // total
                    sys.stderr.write(f"\r  {dest.name}: {pct:3d}% of {total >> 20} MiB")
        if not quiet and total and sys.stderr.isatty():
            sys.stderr.write("\n")
    except OSError as e:
        part.unlink(missing_ok=True)
        raise EgError(f"Download failed: {url}\n  {e}") from e
    os.replace(part, dest)


def sha512_file(path: Path) -> str:
    h = hashlib.sha512()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_sums(text: str) -> dict[str, str]:
    """Parse a SHA512SUMS file into {path: hexdigest}."""
    sums: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts
        sums[name.lstrip("*")] = digest.lower()
    return sums


def parse_validsig(status: str) -> tuple[str, str] | None:
    """Return (signing key fingerprint, primary key fingerprint) from gpg's
    --status-fd output, or None if there is no VALIDSIG line.

    VALIDSIG format: <fpr> <date> <ts> <expire> <ver> <rsvd> <pk-algo>
    <hash-algo> <sig-class> [<primary-key-fpr>]
    """
    for line in status.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[0] == "[GNUPG:]" and fields[1] == "VALIDSIG":
            signing = fields[2].upper()
            primary = fields[11].upper() if len(fields) > 11 else signing
            return signing, primary
    return None


BAD_STATUS = ("BADSIG", "ERRSIG", "EXPKEYSIG", "REVKEYSIG", "EXPSIG")


def find_gpg() -> str | None:
    found = shutil.which("gpg") or shutil.which("gpg2")
    if found:
        return found
    # Git for Windows ships gpg.
    for candidate in (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git/usr/bin/gpg.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Git/usr/bin/gpg.exe",
    ):
        if candidate.exists():
            return str(candidate)
    return None


def is_msys_gpg(gpg: str) -> bool:
    """Git for Windows ships an MSYS2 build of gpg, which only understands
    POSIX-style paths such as /c/Users/... ."""
    if not sys.platform.startswith("win"):
        return False
    try:
        out = subprocess.run([gpg, "--version"], capture_output=True, text=True).stdout
    except OSError:
        return False
    return any(line.startswith("Home: /") for line in out.splitlines())


def gpg_path(path: Path | str, msys: bool) -> str:
    """Spell a path the way this gpg build expects it."""
    path = Path(path)
    if not msys:
        return str(path)
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    rest = resolved.as_posix()[len(resolved.drive):]
    return f"/{drive}{rest}" if drive else rest


def gpg_verify(gpg: str, key: Path, sig: Path, data: Path, up: Upstream) -> str:
    """Verify `sig` over `data` with an isolated keyring holding only `key`.

    Returns the signing subkey fingerprint. Raises EgError unless the signature
    is good and made by a pinned subkey of the pinned primary key.
    """
    msys = is_msys_gpg(gpg)
    with tempfile.TemporaryDirectory(prefix="eg-gnupg-") as home:
        base = [gpg, "--batch", "--homedir", gpg_path(home, msys)]
        imported = subprocess.run(
            [*base, "--quiet", "--import", gpg_path(key, msys)],
            capture_output=True, text=True,
        )
        if imported.returncode != 0:
            raise EgError(f"gpg could not import {key.name}:\n{imported.stderr.strip()}")
        result = subprocess.run(
            [*base, "--status-fd", "1", "--verify", gpg_path(sig, msys), gpg_path(data, msys)],
            capture_output=True, text=True,
        )
    status = result.stdout
    bad = [s for s in BAD_STATUS if f"[GNUPG:] {s}" in status]
    sig_info = parse_validsig(status)
    if result.returncode != 0 or bad or not sig_info:
        raise EgError(
            "Signature check of SHA512SUMS FAILED. Do not build from this source.\n"
            f"  gpg status: {', '.join(bad) or 'no valid signature'}\n{result.stderr.strip()}"
        )
    signing, primary = sig_info
    if primary != up.release_key.primary:
        raise EgError(
            f"SHA512SUMS is signed by primary key {primary}, but upstream.json pins "
            f"{up.release_key.primary}. Refusing to continue."
        )
    if up.release_key.signing_subkeys and signing not in up.release_key.signing_subkeys:
        raise EgError(
            f"SHA512SUMS is signed by subkey {signing}, which is not pinned in "
            "upstream.json (release_key.signing_subkeys).\n"
            "If Mozilla has rotated its signing subkey, confirm the new fingerprint from "
            "Mozilla's own announcement (and `gpg --show-keys KEY`), then add it to "
            "upstream.json in a reviewed commit."
        )
    return signing


def fetch(up: Upstream, force: bool = False) -> ReleaseFiles:
    files = release_files(up)
    wanted = [
        (up.release_url + "SHA512SUMS", files.sums),
        (up.release_url + "SHA512SUMS.asc", files.sums_sig),
        (up.release_url + "KEY", files.key),
        (up.release_url + up.tarball_path_in_sums, files.tarball),
    ]
    for url, dest in wanted:
        if dest.exists() and not force:
            continue
        print(f"Downloading {url}")
        download(url, dest)
    return files


def verify(up: Upstream, files: ReleaseFiles, pin: bool = False) -> str:
    """Verify the downloaded release. Returns the tarball's SHA-512."""
    actual = sha512_file(files.tarball)
    gpg = find_gpg()
    if gpg:
        signing = gpg_verify(gpg, files.key, files.sums_sig, files.sums, up)
        print(f"Signature OK: SHA512SUMS signed by {signing} (primary {up.release_key.primary})")
        listed = parse_sums(files.sums.read_text(encoding="utf-8")).get(
            up.tarball_path_in_sums
        )
        if listed is None:
            raise EgError(f"{up.tarball_path_in_sums} is not listed in SHA512SUMS")
        if listed != actual:
            raise EgError(
                f"Tarball hash does not match the signed SHA512SUMS.\n"
                f"  expected {listed}\n  actual   {actual}"
            )
        print("Tarball hash matches the signed SHA512SUMS.")
    elif not up.sha512:
        raise EgError(
            "gpg was not found and upstream.json has no pinned sha512, so the source "
            "cannot be verified.\nInstall gpg (Git for Windows includes it), or pull an "
            "upstream.json whose sha512 was pinned by `eg.py fetch --pin` on a machine with gpg."
        )
    else:
        print("gpg not found; verifying against the hash pinned in upstream.json.")

    if up.sha512 and up.sha512.lower() != actual:
        raise EgError(
            "Tarball hash does not match upstream.json's pinned sha512.\n"
            f"  pinned {up.sha512}\n  actual {actual}"
        )
    if pin and up.sha512 != actual:
        if not gpg:
            raise EgError("--pin requires gpg so the pinned hash comes from a signature check.")
        save_upstream_sha512(actual)
        print("Pinned the verified sha512 in upstream.json. Commit it.")
    return actual
