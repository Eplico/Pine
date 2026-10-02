# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Repository paths, the upstream pin, and host detection."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
UPSTREAM_JSON = REPO / "upstream.json"
PATCHES_DIR = REPO / "patches"
SRC_DIR = REPO / "src"
BRANDING_DIR = REPO / "branding" / "evergreen"
PREFS_FILE = REPO / "prefs" / "evergreen.js"
MOZCONFIGS_DIR = REPO / "mozconfigs"
DISTRIBUTION_DIR = REPO / "distribution"
DEV_DIR = REPO / "dev"

APP_NAME = "evergreen"
APP_DISPLAY_NAME = "Evergreen"


class EgError(Exception):
    """An error with a message meant for the person running eg.py."""


@dataclass
class ReleaseKey:
    primary: str
    signing_subkeys: list[str] = field(default_factory=list)


@dataclass
class Upstream:
    version: str
    sha512: str | None
    archive: str
    mirror: str
    release_key: ReleaseKey

    @property
    def release_url(self) -> str:
        return f"{self.archive.rstrip('/')}/{self.version}/"

    @property
    def tarball_name(self) -> str:
        return f"firefox-{self.version}.source.tar.xz"

    @property
    def tarball_path_in_sums(self) -> str:
        # How the tarball is listed in the release's SHA512SUMS file.
        return f"source/{self.tarball_name}"

    @property
    def mirror_tag(self) -> str:
        # Release tags on the GitHub mirror look like FIREFOX_157_0_RELEASE.
        return "FIREFOX_" + self.version.replace(".", "_") + "_RELEASE"

    @property
    def major(self) -> int:
        return int(self.version.split(".")[0])


def normalize_fingerprint(fpr: str) -> str:
    return fpr.replace(" ", "").upper()


def load_upstream(path: Path = UPSTREAM_JSON) -> Upstream:
    data = json.loads(path.read_text(encoding="utf-8"))
    ff = data["firefox"]
    key = data["release_key"]
    return Upstream(
        version=ff["version"],
        sha512=ff.get("sha512") or None,
        archive=data["archive"],
        mirror=data["mirror"],
        release_key=ReleaseKey(
            primary=normalize_fingerprint(key["primary"]),
            signing_subkeys=[
                normalize_fingerprint(k) for k in key.get("signing_subkeys", [])
            ],
        ),
    )


def save_upstream_sha512(sha512: str, path: Path = UPSTREAM_JSON) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["firefox"]["sha512"] = sha512
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def work_dir() -> Path:
    """Where downloads, the prepared tree and dev profiles live.

    Defaults to `.eg/` in the repo. On Windows, Firefox's tree is deep, so a
    short path such as C:\\eg is recommended via EG_WORK_DIR.
    """
    override = os.environ.get("EG_WORK_DIR")
    return Path(override).resolve() if override else REPO / ".eg"


def host_platform() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"
