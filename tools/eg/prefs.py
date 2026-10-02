# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Evergreen default prefs: parsing, checking against Firefox, and conversion.

prefs/evergreen.js deliberately uses a strict subset of the pref-file syntax
(one `pref("name", value);` per line, comments, no preprocessor) so it can be
audited line by line and reused unchanged by the dev harness.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .config import EgError

PREF_LINE = re.compile(r'^pref\(\s*"([^"]+)"\s*,\s*(.+?)\s*\)\s*;\s*(?://(.*))?$')
DYNAMIC_MARK = "eg:dynamic"


@dataclass
class Pref:
    name: str
    value: bool | int | str
    line: int
    dynamic: bool  # defined at runtime by Firefox code, not in a prefs file


def _parse_value(raw: str, where: str) -> bool | int | str:
    if raw == "true":
        return True
    if raw == "false":
        return False
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if raw.startswith('"') and raw.endswith('"'):
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise EgError(f"{where}: bad string literal {raw}") from e
    raise EgError(f"{where}: unsupported value {raw} (use true/false/integers/\"strings\")")


def parse_prefs(text: str, filename: str = "evergreen.js") -> list[Pref]:
    prefs: list[Pref] = []
    names: set[str] = set()
    in_block_comment = False
    for lineno, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        where = f"{filename}:{lineno}"
        if in_block_comment:
            if "*/" in line:
                in_block_comment = False
            continue
        if not line or line.startswith("//"):
            continue
        if line.startswith("/*"):
            in_block_comment = "*/" not in line
            continue
        if line.startswith("#"):
            raise EgError(f"{where}: preprocessor directives are not allowed in Evergreen prefs")
        m = PREF_LINE.match(line)
        if not m:
            raise EgError(f"{where}: expected `pref(\"name\", value);`, got: {line}")
        name, raw_value, comment = m.group(1), m.group(2), m.group(3) or ""
        if name in names:
            raise EgError(f"{where}: {name} is set twice")
        names.add(name)
        prefs.append(
            Pref(name, _parse_value(raw_value, where), lineno, DYNAMIC_MARK in comment)
        )
    return prefs


def load_prefs(path: Path) -> list[Pref]:
    return parse_prefs(path.read_text(encoding="utf-8"), path.name)


def to_autoconfig(prefs: list[Pref]) -> str:
    """Render prefs as autoconfig `defaultPref()` calls for the dev harness."""
    return "\n".join(
        f"defaultPref({json.dumps(p.name)}, {json.dumps(p.value)});" for p in prefs
    )


# Firefox files that declare prefs, relative to the source root. Prefs from
# the newtab and urlbar components are declared without their branch prefix.
PREF_SOURCES = (
    "modules/libpref/init/StaticPrefList.yaml",
    "modules/libpref/init/all.js",
    "browser/app/profile/firefox.js",
    "toolkit/components/pdfjs/PdfJsDefaultPrefs.js",
    "browser/extensions/newtab/lib/ActivityStream.sys.mjs",
    "browser/components/urlbar/UrlbarPrefs.sys.mjs",
)
BRANCH_SOURCES = {
    "browser.newtabpage.activity-stream.": "browser/extensions/newtab/lib/ActivityStream.sys.mjs",
    "browser.urlbar.": "browser/components/urlbar/UrlbarPrefs.sys.mjs",
}


def known_in_sources(name: str, sources: dict[str, str]) -> bool:
    """Is `name` declared anywhere in the given {path: text} Firefox sources?"""
    quoted = f'"{name}"'
    for path, text in sources.items():
        if path.endswith(".yaml"):
            if re.search(rf"^\s*-\s*name:\s*{re.escape(name)}\s*$", text, re.M):
                return True
        elif quoted in text:
            return True
    for branch, path in BRANCH_SOURCES.items():
        if name.startswith(branch) and f'"{name[len(branch):]}"' in sources.get(path, ""):
            return True
    return False


def unknown_prefs(prefs: list[Pref], sources: dict[str, str]) -> list[Pref]:
    return [
        p for p in prefs
        if not p.dynamic and not p.name.startswith("evergreen.")
        and not known_in_sources(p.name, sources)
    ]
