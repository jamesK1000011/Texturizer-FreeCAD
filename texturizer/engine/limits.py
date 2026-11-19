# SPDX-License-Identifier: AGPL-3.0-only
# Part of the engine ported from BumpMesh by Stefan Hermann (CNC Kitchen), AGPL-3.0.
# Caps on what Texturizer unpacks from a file (red team 3, 19 Nov 2026,
# R3-6): a .bumpmesh project (a ZIP) and a heightmap PNG (zlib) can come
# from the web, and a small file can unpack to many gigabytes.
#  - ZIP: a total unpacked cap, a ratio cap and a member count cap, checked
#    before a byte is unpacked; each member read in chunks to a byte limit.
#  - zlib: inflated with decompressobj and max_length, to a byte limit.
#  - PNG: width × height capped before the decode.
# Plain Python: no FreeCAD, no Agent Link import.

import zipfile
import zlib

MAX_TOTAL = 1024 * 1024 * 1024  # all members of one ZIP, unpacked
MAX_MEMBERS = 1000
MAX_RATIO = 1000  # unpacked to packed, for a member over 1 MB
RATIO_FLOOR = 1024 * 1024
CHUNK = 1024 * 1024
MAX_PIXELS = 8192 * 8192  # a heightmap's width × height
MAX_SIDE = 32768


class TooLarge(ValueError):
    """A file refused before it is unpacked: too large, too many members,
    packed too tightly (a ZIP bomb) or a picture with too many pixels."""


def _mb(n):
    return f"{n / (1024 * 1024):.0f} MB"


def _name(n):
    return "".join(c for c in str(n) if c.isprintable())[:80]


def zip_check(z, what="the archive", total=None):
    """The file members of the open ZipFile z, refused before a byte is
    unpacked when they are more than MAX_MEMBERS, unpack to more than total,
    or one is packed more than MAX_RATIO to one."""
    total = MAX_TOTAL if total is None else total
    infos = [i for i in z.infolist() if not i.is_dir()]
    if len(infos) > MAX_MEMBERS:
        raise TooLarge(f"{what} holds {len(infos)} files, more than {MAX_MEMBERS}: not opened.")
    size = sum(i.file_size for i in infos)
    if size > total:
        raise TooLarge(f"{what} unpacks to {_mb(size)}, more than {_mb(total)}: not opened.")
    for i in infos:
        if i.file_size > RATIO_FLOOR and i.file_size > MAX_RATIO * max(1, i.compress_size):
            raise TooLarge(f"{what}: {_name(i.filename)} is packed more than {MAX_RATIO} to one (a ZIP bomb?): not opened.")
    return infos


def zip_read(z, member, what="the archive", limit=None):
    """One member's bytes, read in chunks; refused when it gives more than
    limit bytes (whatever its header says)."""
    limit = MAX_TOTAL if limit is None else limit
    info = member if isinstance(member, zipfile.ZipInfo) else z.getinfo(member)
    if info.file_size > limit:
        raise TooLarge(f"{what}: {_name(info.filename)} unpacks to {_mb(info.file_size)}, more than {_mb(limit)}: not read.")
    parts, got = [], 0
    with z.open(info) as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            got += len(b)
            if got > limit:
                raise TooLarge(f"{what}: {_name(info.filename)} unpacks to more than {_mb(limit)}: not read.")
            parts.append(b)
    return b"".join(parts)


def zip_members(z, what="the archive", total=None):
    """{name: bytes} of every file member, checked by zip_check and read by
    zip_read inside the total."""
    total = MAX_TOTAL if total is None else total
    out, left = {}, total
    for i in zip_check(z, what, total):
        b = zip_read(z, i, what, left)
        left -= len(b)
        out[i.filename] = b
    return out


def inflate(data, limit, what="the data"):
    """zlib data unpacked to at most limit bytes; more is refused."""
    limit = MAX_TOTAL if limit is None else limit
    d = zlib.decompressobj()
    out = d.decompress(data, limit + 1)
    if len(out) <= limit and not d.unconsumed_tail:
        out += d.flush()
    if len(out) > limit or d.unconsumed_tail:
        raise TooLarge(f"{what} unpacks to more than {_mb(limit)}: not read.")
    return out


def pixels_check(w, h, what="the picture"):
    """A picture's size refused before its pixels are decoded."""
    if not w or not h or w > MAX_SIDE or h > MAX_SIDE or w * h > MAX_PIXELS:
        raise TooLarge(f"{what} is {w} × {h} pixels: more than {MAX_PIXELS:,} pixels, or a side over {MAX_SIDE}. "
                       "Make it smaller (2048 px is enough for a texture).")
