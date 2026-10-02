# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""The patch series: parsing, linting, budget and application.

Every patch carries a header (design doc §5.3) and is listed in
patches/series. Patches only add hook points; Evergreen logic lives in new
files under src/.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import PATCHES_DIR, EgError

REQUIRED_HEADERS = ("Evergreen-Patch", "Why", "Upstream", "Drop-when", "Owner")
BUDGET_PATCHES = 30
BUDGET_LINES = 2000

# Changes under these paths need two reviewers and a written justification.
SENSITIVE_PREFIXES = (
    "security/", "netwerk/", "dom/", "js/", "ipc/",
    "toolkit/mozapps/update/", "toolkit/components/extensions/",
)


@dataclass
class Patch:
    name: str
    path: Path
    headers: dict[str, str]
    files: list[str]
    added: int
    removed: int

    @property
    def changed_lines(self) -> int:
        return self.added + self.removed

    @property
    def sensitive_files(self) -> list[str]:
        return [f for f in self.files if f.startswith(SENSITIVE_PREFIXES)]


def read_series(patches_dir: Path = PATCHES_DIR) -> list[str]:
    series = patches_dir / "series"
    if not series.exists():
        return []
    names = []
    for line in series.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            names.append(line)
    return names


def parse_headers(text: str) -> dict[str, str]:
    """Read `Key: value` lines before the diff starts."""
    headers: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith(("diff --git", "--- ", "Index: ")):
            break
        m = re.match(r"^([A-Za-z][A-Za-z-]*):\s*(.*)$", line)
        if m:
            headers[m.group(1)] = m.group(2).strip()
    return headers


def parse_diff(text: str) -> tuple[list[str], int, int]:
    """Return (touched files, added lines, removed lines) for a unified diff."""
    files: list[str] = []
    added = removed = 0
    in_hunk = False
    for line in text.splitlines():
        if line.startswith("diff --git "):
            in_hunk = False
            continue
        if line.startswith("+++ "):
            in_hunk = False
            target = line[4:].split("\t")[0].strip()
            if target != "/dev/null":
                files.append(target[2:] if target.startswith("b/") else target)
            continue
        if line.startswith("--- "):
            in_hunk = False
            continue
        if line.startswith("@@"):
            in_hunk = True
            continue
        if in_hunk:
            if line.startswith("+"):
                added += 1
            elif line.startswith("-"):
                removed += 1
    return files, added, removed


def load_patch(path: Path) -> Patch:
    text = path.read_text(encoding="utf-8")
    files, added, removed = parse_diff(text)
    return Patch(path.name, path, parse_headers(text), files, added, removed)


def load_series(patches_dir: Path = PATCHES_DIR) -> list[Patch]:
    return [load_patch(patches_dir / name) for name in read_series(patches_dir)]


def lint(patches_dir: Path = PATCHES_DIR) -> list[str]:
    """Return a list of problems with the patch series (empty if clean)."""
    problems: list[str] = []
    names = read_series(patches_dir)
    on_disk = {p.name for p in patches_dir.glob("*.patch")}
    for name in sorted(on_disk - set(names)):
        problems.append(f"{name}: not listed in patches/series")
    seen = set()
    for name in names:
        if name in seen:
            problems.append(f"{name}: listed twice in patches/series")
        seen.add(name)
        path = patches_dir / name
        if not path.exists():
            problems.append(f"{name}: listed in series but missing")
            continue
        raw = path.read_bytes()
        if b"\r\n" in raw:
            problems.append(f"{name}: has CRLF line endings")
        patch = load_patch(path)
        for key in REQUIRED_HEADERS:
            if not patch.headers.get(key):
                problems.append(f"{name}: missing '{key}:' header")
        if not patch.files:
            problems.append(f"{name}: contains no file changes")
        if patch.sensitive_files and not patch.headers.get("Security-Review"):
            problems.append(
                f"{name}: touches security-sensitive paths "
                f"({', '.join(patch.sensitive_files)}) without a 'Security-Review:' header"
            )
    return problems


def budget(patches: list[Patch]) -> tuple[int, int]:
    return len(patches), sum(p.changed_lines for p in patches)


def budget_report(patches: list[Patch]) -> str:
    count, lines = budget(patches)
    flag = "" if count <= BUDGET_PATCHES and lines <= BUDGET_LINES else "  ** OVER BUDGET **"
    return (
        f"Patch budget: {count}/{BUDGET_PATCHES} patches, "
        f"{lines}/{BUDGET_LINES} changed upstream lines{flag}"
    )


def _apply_cmd(patch: Path, check: bool) -> list[str]:
    git = shutil.which("git")
    if git:
        cmd = [git, "apply", "--whitespace=nowarn", "-p1"]
        if check:
            cmd.append("--check")
        return cmd + [str(patch)]
    patch_tool = shutil.which("patch")
    if patch_tool:
        cmd = [patch_tool, "-p1", "--forward", "--batch", "-i", str(patch)]
        if check:
            cmd.insert(1, "--dry-run")
        return cmd
    raise EgError("Neither git nor patch was found; one is needed to apply patches.")


def apply_series(tree: Path, patches_dir: Path = PATCHES_DIR, check: bool = False) -> None:
    for patch in load_series(patches_dir):
        result = subprocess.run(
            _apply_cmd(patch.path, check), cwd=tree, capture_output=True, text=True
        )
        if result.returncode != 0:
            raise EgError(
                f"Patch {patch.name} does not apply to {tree}.\n"
                f"{result.stdout}{result.stderr}\n"
                "Rebase it against the new Firefox version (see docs/development.md)."
            )
        print(f"  {'checked' if check else 'applied'} {patch.name}")
