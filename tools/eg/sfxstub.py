# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Evergreen's copy of the Windows installer's self-extracting stub.

The installer .exe is Firefox's 7-Zip stub (other-licenses/7zstub/firefox/
7zSD.Win32.sfx) followed by the packaged files, so Windows shows the stub's
icon for it: a box with the Firefox logo. `eg.py prepare` writes a copy with
Evergreen's icon (branding/evergreen/source/installer-stub.ico) into the
branding directory, and patch 0003 points the installer build at it.

The icons are replaced in place: each new image must be no larger than the
one it replaces, so no part of the executable moves. installer-stub.ico has
the same sizes and formats as the stub's icons (make_installer_images.py).
"""

from __future__ import annotations

import struct

from .config import EgError

RT_ICON = 3
RT_GROUP_ICON = 14


def read_ico(data: bytes) -> dict[int, bytes]:
    """Images in an .ico file, by width in pixels (256 for the 0 entry)."""
    reserved, kind, count = struct.unpack_from("<HHH", data)
    if reserved != 0 or kind != 1:
        raise EgError("not an .ico file")
    images = {}
    for i in range(count):
        width, _h, _c, _r, _planes, _bpp, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        images[width or 256] = data[offset:offset + size]
    return images


class _Pe:
    """Just enough of a PE file to find and rewrite its resources."""

    def __init__(self, data: bytearray):
        self.data = data
        if data[:2] != b"MZ":
            raise EgError("not a Windows executable")
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            raise EgError("not a PE executable")
        # COFF header: machine, sections, timestamp, symbols, symbol count, optional header size, flags.
        _machine, sections, _ts, _syms, _nsyms, opt_size, _flags = struct.unpack_from("<HHIIIHH", data, pe + 4)
        opt = pe + 24
        magic = struct.unpack_from("<H", data, opt)[0]
        dirs = opt + (96 if magic == 0x10B else 112)  # PE32 / PE32+
        self.rsrc_rva = struct.unpack_from("<I", data, dirs + 2 * 8)[0]
        self.sections = []
        table = opt + opt_size
        for i in range(sections):
            vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", data, table + 40 * i + 8)
            self.sections.append((vaddr, max(vsize, rawsize), rawptr))
        if not self.rsrc_rva:
            raise EgError("the executable has no resources")
        self.rsrc = self.offset(self.rsrc_rva)

    def offset(self, rva: int) -> int:
        for vaddr, size, rawptr in self.sections:
            if vaddr <= rva < vaddr + size:
                return rva - vaddr + rawptr
        raise EgError(f"RVA {rva:#x} is outside every section")

    def _entries(self, directory: int):
        named, ids = struct.unpack_from("<HH", self.data, directory + 12)
        for i in range(named + ids):
            name, target = struct.unpack_from("<II", self.data, directory + 16 + 8 * i)
            yield name, target

    def resources(self, rtype: int):
        """(id, offset of its IMAGE_RESOURCE_DATA_ENTRY) for each resource of a type."""
        for name, target in self._entries(self.rsrc):
            if name != rtype or not target & 0x80000000:
                continue
            for rid, sub in self._entries(self.rsrc + (target & 0x7FFFFFFF)):
                for _lang, leaf in self._entries(self.rsrc + (sub & 0x7FFFFFFF)):
                    if not leaf & 0x80000000:
                        yield rid, self.rsrc + leaf


def replace_icons(stub: bytes, ico: bytes) -> bytes:
    """`stub` with its icons replaced by the same-sized images from `ico`."""
    data = bytearray(stub)
    pe = _Pe(data)
    images = read_ico(ico)
    icons = dict(pe.resources(RT_ICON))
    groups = list(pe.resources(RT_GROUP_ICON))
    if not groups or not icons:
        raise EgError("the executable has no icon")
    replaced = 0
    for _gid, group_entry in groups:
        rva, size = struct.unpack_from("<II", data, group_entry)
        group = pe.offset(rva)
        count = struct.unpack_from("<H", data, group + 4)[0]
        for i in range(count):
            entry = group + 6 + 14 * i
            width, _h, _c, _r, _planes, _bpp, _bytes, icon_id = struct.unpack_from("<BBBBHHIH", data, entry)
            width = width or 256
            if width not in images:
                raise EgError(f"the icon has no {width} px image for the installer stub")
            new = images[width]
            leaf = icons[icon_id]
            icon_rva, capacity = struct.unpack_from("<II", data, leaf)
            if len(new) > capacity:
                raise EgError(f"the {width} px image is {len(new)} bytes; the stub has room for {capacity}")
            start = pe.offset(icon_rva)
            data[start:start + capacity] = new + bytes(capacity - len(new))
            struct.pack_into("<I", data, leaf + 4, len(new))
            struct.pack_into("<I", data, entry + 8, len(new))
            replaced += 1
    if not replaced:
        raise EgError("no icons were replaced")
    return bytes(data)
