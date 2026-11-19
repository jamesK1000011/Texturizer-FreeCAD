# SPDX-License-Identifier: AGPL-3.0-only
# Port of engine/node.js of BumpMesh by Stefan Hermann (CNC Kitchen), AGPL-3.0.
# Reading heightmaps and STL files with no browser: an 8-bit PNG decoder on
# zlib (grey, grey+alpha, RGB, RGBA; Pillow, when FreeCAD's Python has it,
# reads the rest — palette PNGs, JPEG, WebP), and binary or ASCII STL.
import re
import struct
import zlib

import numpy as np

from .limits import TooLarge, inflate, pixels_check
from .maps import CUSTOM_SIZE, Image, fit_image

MAX_IMAGE_BYTES = 256 * 1024 * 1024


def decode_png(buf):
    """decodePNG: an 8-bit non-interlaced PNG (not palette) to RGBA."""
    d = bytes(buf)
    if d[:4] != b"\x89PNG":
        raise ValueError("Not a PNG file.")
    p, idat = 8, []
    w = h = ct = bd = interlace = None
    while p < len(d):
        ln = struct.unpack(">I", d[p:p + 4])[0]
        typ = d[p + 4:p + 8]
        start = p + 8
        if typ == b"IHDR":
            w, h = struct.unpack(">II", d[start:start + 8])
            bd, ct, interlace = d[start + 8], d[start + 9], d[start + 12]
        elif typ == b"IDAT":
            idat.append(d[start:start + ln])
        elif typ == b"IEND":
            break
        p = start + ln + 4
    if bd != 8:
        raise ValueError(f"Only 8-bit PNGs are read here (this one is {bd}-bit) — save it as 8-bit greyscale.")
    if interlace:
        raise ValueError("Interlaced PNGs are not read here — save it without interlacing.")
    ch = {0: 1, 2: 3, 4: 2, 6: 4}.get(ct)
    if not ch:
        raise ValueError("Palette PNGs are not read here — save it as greyscale or RGB.")
    # A PNG from the web: its size capped before the decode, its pixel data
    # unpacked to the size the header says and no more (red team 3, R3-6).
    pixels_check(w, h, "The PNG")
    stride = w * ch
    raw = np.frombuffer(inflate(b"".join(idat), h * (stride + 1), "The PNG's pixel data"), np.uint8)
    if len(raw) < h * (stride + 1):
        raise ValueError("The PNG's pixel data is cut short — the file is damaged.")
    rows = raw[: h * (stride + 1)].reshape(h, stride + 1)
    out = np.zeros((h, stride), np.uint8)
    prev = np.zeros(stride, np.int64)
    for y in range(h):
        f = rows[y, 0]
        r = rows[y, 1:].astype(np.int64)
        if f == 0:
            cur = r
        elif f == 1:
            cur = np.cumsum(r.reshape(w, ch), axis=0).reshape(-1) & 0xFF
        elif f == 2:
            cur = (r + prev) & 0xFF
        else:
            cur = np.zeros(stride, np.int64)
            rl, pl = r.tolist(), prev.tolist()
            cl = cur.tolist()
            for x in range(stride):
                a = cl[x - ch] if x >= ch else 0
                b = pl[x]
                if f == 3:
                    cl[x] = (rl[x] + ((a + b) >> 1)) & 0xFF
                else:
                    c = pl[x - ch] if x >= ch else 0
                    q = a + b - c
                    pa, pb, pc = abs(q - a), abs(q - b), abs(q - c)
                    cl[x] = (rl[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
            cur = np.array(cl, np.int64)
        out[y] = cur
        prev = cur
    px = out.reshape(h, w, ch)
    rgba = np.empty((h, w, 4), np.uint8)
    if ch == 1:
        rgba[..., :3] = px
        rgba[..., 3] = 255
    elif ch == 2:
        rgba[..., :3] = px[..., :1]
        rgba[..., 3] = px[..., 1]
    else:
        rgba[..., :3] = px[..., :3]
        rgba[..., 3] = px[..., 3] if ch == 4 else 255
    return Image(rgba)


def read_image(path):
    """Any heightmap file as RGBA: PNG here; other formats through Pillow."""
    import os

    if os.path.getsize(path) > MAX_IMAGE_BYTES:
        raise TooLarge(f"{os.path.basename(path)[:80]}: larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB; a heightmap is far smaller.")
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:4] == b"\x89PNG":
        try:
            return decode_png(data)
        except TooLarge:
            raise
        except ValueError:
            pass
    try:
        from PIL import Image as PILImage
    except ImportError:
        raise ValueError(f"{path}: only 8-bit greyscale or RGB PNGs are read without Pillow — save it as such a PNG.") from None
    import io

    with PILImage.open(io.BytesIO(data)) as im:
        pixels_check(im.size[0], im.size[1], "The picture")  # before Pillow decodes it (R3-6)
        return Image(np.asarray(im.convert("RGBA")))


def load_image(path):
    """loadImage: your own heightmap, sized as the app sizes uploads (fit 2048 px)."""
    return fit_image(read_image(path), CUSTOM_SIZE)


def read_stl(buf):
    """readSTL: float32 non-indexed positions (9 per triangle) of a binary or ASCII STL."""
    d = bytes(buf)
    n = struct.unpack("<I", d[80:84])[0] if len(d) >= 84 else 0
    if len(d) == 84 + 50 * n:
        rec = np.frombuffer(d, dtype=[("n", "<f4", 3), ("v", "<f4", 9), ("a", "<u2")], count=n, offset=84)
        return np.ascontiguousarray(rec["v"]).reshape(-1).astype(np.float32)
    text = d.decode("utf-8", "replace")
    nums = re.findall(r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", text)
    if not nums:
        raise ValueError("Not an STL file this reads.")
    return np.array([float(v) for m in nums for v in m], np.float32)


def encode_png(img_or_grey, width=None, height=None):
    """An 8-bit PNG (no Pillow): grey for an (h, w) array or an RGBA image whose
    channels are equal and opaque, RGBA otherwise."""
    if isinstance(img_or_grey, Image):
        d = img_or_grey.data
        if (d[:, :, 0] == d[:, :, 1]).all() and (d[:, :, 1] == d[:, :, 2]).all() and (d[:, :, 3] == 255).all():
            px, ct = d[:, :, 0], 0
        else:
            px, ct = d, 6
    else:
        px = np.asarray(img_or_grey, np.uint8)
        if width is not None:
            px = px.reshape(height, width)
        ct = 0 if px.ndim == 2 else 6
    h, w = px.shape[:2]
    rows = px.reshape(h, -1)
    raw = b"".join(b"\0" + rows[y].tobytes() for y in range(h))

    def chunk(t, body):
        return struct.pack(">I", len(body)) + t + body + struct.pack(">I", zlib.crc32(t + body) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, ct, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
