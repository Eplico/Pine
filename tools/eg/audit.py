# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Prefs audit: Evergreen's defaults against arkenfox's user.js.

arkenfox (https://github.com/arkenfox/user.js) is the best-known hardening
list for Firefox. `eg.py audit-prefs` fetches its user.js, lists every active
`user_pref` and says, for each, whether Evergreen sets the same value, a
different one, or nothing. Which differences are intended is recorded in
docs/prefs-audit.md; this tool only finds them, it changes nothing.
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass

from .config import PREFS_FILE, EgError
from .prefs import load_prefs

ARKENFOX_URL = "https://raw.githubusercontent.com/arkenfox/user.js/master/user.js"
USER_PREF = re.compile(r'^user_pref\(\s*"([^"]+)"\s*,\s*(.+?)\s*\)\s*;')


@dataclass
class Row:
    name: str
    arkenfox: object
    evergreen: object  # a value, or MISSING
    section: str


MISSING = object()


def _value(raw: str):
    raw = raw.strip()
    if raw == "true":
        return True
    if raw == "false":
        return False
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if raw.startswith('"'):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw  # an expression (arkenfox has a few); compared as text


def parse_arkenfox(text: str) -> list[tuple[str, object, str]]:
    """Active (not commented out) user_pref lines as (name, value, section)."""
    rows = []
    section = ""
    in_comment = False
    for line in text.splitlines():
        stripped = line.strip()
        m = re.match(r"^/\*\*\s*(.+?)\s*\*\*\*/$", stripped)
        if m:
            section = m.group(1)
            continue
        if in_comment:
            in_comment = "*/" not in stripped
            continue
        if stripped.startswith("/*") and "*/" not in stripped:
            in_comment = True
            continue
        m = USER_PREF.match(stripped)
        if m:
            rows.append((m.group(1), _value(m.group(2)), section))
    return rows


def compare(arkenfox: list[tuple[str, object, str]], evergreen: dict[str, object]) -> list[Row]:
    return [Row(name, value, evergreen.get(name, MISSING), section) for name, value, section in arkenfox]


def summarize(rows: list[Row]) -> dict[str, list[Row]]:
    out = {"same": [], "different": [], "missing": []}
    for row in rows:
        if row.evergreen is MISSING:
            out["missing"].append(row)
        elif row.evergreen == row.arkenfox:
            out["same"].append(row)
        else:
            out["different"].append(row)
    return out


def fetch(url: str = ARKENFOX_URL) -> str:
    try:
        with urllib.request.urlopen(url, timeout=60) as r:  # noqa: S310 (a fixed https URL)
            return r.read().decode("utf-8")
    except OSError as e:
        raise EgError(f"could not fetch {url}: {e}") from e


def report(text: str, show_missing: bool = True) -> str:
    prefs = {p.name: p.value for p in load_prefs(PREFS_FILE)}
    summary = summarize(compare(parse_arkenfox(text), prefs))
    lines = [
        f"arkenfox sets {sum(len(v) for v in summary.values())} prefs; "
        f"Evergreen agrees on {len(summary['same'])}, "
        f"differs on {len(summary['different'])}, "
        f"sets {len(summary['missing'])} not at all.",
        "",
        "Different values (arkenfox -> Evergreen):",
    ]
    for r in summary["different"]:
        lines.append(f"  {r.name}: {r.arkenfox!r} -> {r.evergreen!r}   [{r.section}]")
    if show_missing:
        lines += ["", "Not set by Evergreen (arkenfox value):"]
        section = None
        for r in sorted(summary["missing"], key=lambda r: (r.section, r.name)):
            if r.section != section:
                section = r.section
                lines.append(f"  [{section}]")
            lines.append(f"    {r.name} = {r.arkenfox!r}")
    return "\n".join(lines)
