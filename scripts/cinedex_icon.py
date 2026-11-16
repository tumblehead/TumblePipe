#!/usr/bin/env python3
"""Draw Cinedex's application icon: the SVG, the PNGs and the Windows .ico.

    python scripts/cinedex_icon.py          # (re)write src/cinedex/cinedex/icon/
    python scripts/cinedex_icon.py --check  # fail when the files are not what it draws

The icon is a strip of film with the editor's playhead standing in it: the
greys of Cinedex's look (``src/cinedex/cinedex/src/look.rs``: the surround,
a light strip in the text grey) and its one accent, the amber of *now*
(240, 180, 60) that the playhead and the timecode are drawn in.

One list of shapes (rounded rectangles and triangles on a 256-unit square)
is both written as ``cinedex.svg`` and rasterised here, with 4x4
supersampling, into each size: no SVG renderer is needed, so the output is
the same on every machine (pure standard library). Small sizes (32 px and
under) draw a simpler icon, without sprocket holes and with a playhead at
least two pixels wide, so it still reads in a taskbar.

Outputs, in ``src/cinedex/cinedex/icon/``:

- ``cinedex.svg``: the icon at full detail;
- ``cinedex-<size>.png`` for 16, 32, 48, 64, 128, 256 and 512: the X11 window
  icon and the macOS Dock icon embed some of them (``src/app_icon.rs``);
- ``cinedex.ico``: 16 to 256 px, which ``build.rs`` embeds in the Windows
  executable (Explorer, the taskbar and the window's title bar show it).
"""

from __future__ import annotations

import argparse
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'src' / 'cinedex' / 'cinedex' / 'icon'

PNG_SIZES = (16, 32, 48, 64, 128, 256, 512)
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
SAMPLES = 4  # per axis

RIM = (74, 74, 74)        # look::rule, a little lighter: the tile's edge on a dark taskbar
TILE = (30, 30, 30)       # look::surround
STRIP = (225, 225, 225)   # look::text
WINDOW = (52, 52, 52)     # the strip's picture windows
HOLE = (30, 30, 30)
NOW = (240, 180, 60)      # look::now


@dataclass(frozen=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float
    r: float
    colour: tuple[int, int, int]

    def inside(self, x: float, y: float) -> bool:
        if not (self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1):
            return False
        cx = min(max(x, self.x0 + self.r), self.x1 - self.r)
        cy = min(max(y, self.y0 + self.r), self.y1 - self.r)
        return (x - cx) ** 2 + (y - cy) ** 2 <= self.r ** 2

    def bounds(self) -> tuple[float, float, float, float]:
        return self.x0, self.y0, self.x1, self.y1

    def svg(self) -> str:
        return (f'<rect x="{_n(self.x0)}" y="{_n(self.y0)}" width="{_n(self.x1 - self.x0)}" '
                f'height="{_n(self.y1 - self.y0)}" rx="{_n(self.r)}" fill="{_hex(self.colour)}"/>')


@dataclass(frozen=True)
class Triangle:
    points: tuple[tuple[float, float], ...]
    colour: tuple[int, int, int]

    def inside(self, x: float, y: float) -> bool:
        signs = []
        n = len(self.points)
        for i in range(n):
            (ax, ay), (bx, by) = self.points[i], self.points[(i + 1) % n]
            signs.append((bx - ax) * (y - ay) - (by - ay) * (x - ax))
        return all(s >= 0 for s in signs) or all(s <= 0 for s in signs)

    def bounds(self) -> tuple[float, float, float, float]:
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return min(xs), min(ys), max(xs), max(ys)

    def svg(self) -> str:
        points = ' '.join(f'{_n(x)},{_n(y)}' for x, y in self.points)
        return f'<polygon points="{points}" fill="{_hex(self.colour)}"/>'


def _n(value: float) -> str:
    return f'{value:g}'


def _hex(colour: tuple[int, int, int]) -> str:
    return '#{:02x}{:02x}{:02x}'.format(*colour)


def shapes(size: int) -> list:
    """The icon's shapes on the 256-unit square, back to front, as drawn at
    ``size`` pixels: small sizes lose the sprocket holes and keep the
    playhead at least two pixels wide."""
    small = size <= 32
    unit = 256 / size  # units per pixel
    out: list = [
        Rect(8, 8, 248, 248, 48, RIM),
        Rect(14, 14, 242, 242, 42, TILE),
        Rect(30, 72, 226, 184, 10, STRIP),
    ]
    if small:
        out += [Rect(44, 92, 120, 164, 6, WINDOW), Rect(136, 92, 212, 164, 6, WINDOW)]
    else:
        for x in range(42, 214, 28):
            out += [Rect(x, 80, x + 16, 92, 3, HOLE), Rect(x, 164, x + 16, 176, 3, HOLE)]
        out += [Rect(44, 102, 120, 154, 6, WINDOW), Rect(136, 102, 212, 154, 6, WINDOW)]
    half = max(7.0, unit) if small else 7.0
    out += [
        Rect(128 - half, 56, 128 + half, 214, 2, NOW),
        Triangle(((100, 34), (156, 34), (128, 70)), NOW),
    ]
    return out


def svg() -> str:
    body = '\n  '.join(shape.svg() for shape in shapes(256))
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">\n'
        '  <title>Cinedex</title>\n'
        f'  {body}\n'
        '</svg>\n'
    )


def rasterise(size: int) -> bytes:
    """The icon at ``size`` x ``size`` pixels: straight (not premultiplied)
    RGBA rows, top first."""
    scale = 256 / size
    # Premultiplied accumulation, then straight alpha out.
    pixels = [[0.0, 0.0, 0.0, 0.0] for _ in range(size * size)]
    offsets = [(i + 0.5) / SAMPLES for i in range(SAMPLES)]
    for shape in shapes(size):
        x0, y0, x1, y1 = shape.bounds()
        px0, py0 = max(0, int(x0 / scale)), max(0, int(y0 / scale))
        px1, py1 = min(size, int(x1 / scale) + 1), min(size, int(y1 / scale) + 1)
        r, g, b = (c / 255 for c in shape.colour)
        for py in range(py0, py1):
            for px in range(px0, px1):
                hits = sum(shape.inside((px + ox) * scale, (py + oy) * scale) for oy in offsets for ox in offsets)
                if not hits:
                    continue
                a = hits / (SAMPLES * SAMPLES)
                p = pixels[py * size + px]
                p[0] = r * a + p[0] * (1 - a)
                p[1] = g * a + p[1] * (1 - a)
                p[2] = b * a + p[2] * (1 - a)
                p[3] = a + p[3] * (1 - a)
    out = bytearray()
    for pr, pg, pb, pa in pixels:
        if pa <= 0:
            out += b'\0\0\0\0'
            continue
        out += bytes(round(min(1.0, c / pa) * 255) for c in (pr, pg, pb)) + bytes([round(pa * 255)])
    return bytes(out)


def png(size: int, rgba: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = b''.join(b'\0' + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))
    header = struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b'')


def dib(size: int, rgba: bytes) -> bytes:
    """An icon entry as a 32-bit DIB: the classic form every resource
    compiler and Windows version reads (the 256 px entry is a PNG)."""
    header = struct.pack('<IiiHHIIiiII', 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    rows = []
    for y in reversed(range(size)):
        row = rgba[y * size * 4:(y + 1) * size * 4]
        rows.append(b''.join(bytes((row[i + 2], row[i + 1], row[i], row[i + 3])) for i in range(0, len(row), 4)))
    mask_row = ((size + 31) // 32) * 4
    return header + b''.join(rows) + b'\0' * (mask_row * size)


def ico(images: dict[int, bytes]) -> bytes:
    entries = []
    for size in ICO_SIZES:
        data = png(size, images[size]) if size >= 256 else dib(size, images[size])
        entries.append((size, data))
    out = struct.pack('<HHH', 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    blobs = b''
    for size, data in entries:
        dim = 0 if size >= 256 else size
        out += struct.pack('<BBBBHHII', dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs += data
    return out + blobs


def outputs() -> dict[str, bytes]:
    images = {size: rasterise(size) for size in sorted(set(PNG_SIZES) | set(ICO_SIZES))}
    files = {'cinedex.svg': svg().encode(), 'cinedex.ico': ico(images)}
    for size in PNG_SIZES:
        files[f'cinedex-{size}.png'] = png(size, images[size])
    return files


def png_pixels(data: bytes) -> tuple[bytes, bytes] | None:
    """A PNG's header and its rows, decompressed: what it shows, whichever
    zlib (or level) compressed it. None when it is no PNG."""
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):
        return None
    header, idat, at = b'', b'', 8
    try:
        while at + 8 <= len(data):
            (length,) = struct.unpack('>I', data[at:at + 4])
            kind, body = data[at + 4:at + 8], data[at + 8:at + 8 + length]
            if kind == b'IHDR':
                header = body
            elif kind == b'IDAT':
                idat += body
            at += 12 + length
        return header, zlib.decompress(idat)
    except (struct.error, zlib.error):
        return None


def _ico_entries(data: bytes) -> list[tuple[bytes, bytes]] | None:
    """An .ico's entries: each one's directory fields (without its size and
    offset) and its image. None when it is no icon file."""
    try:
        _, kind, count = struct.unpack('<HHH', data[:6])
        if kind != 1:
            return None
        entries = []
        for index in range(count):
            row = data[6 + 16 * index:22 + 16 * index]
            size, offset = struct.unpack('<II', row[8:16])
            entries.append((row[:8], data[offset:offset + size]))
        return entries
    except struct.error:
        return None


def _same_image(a: bytes, b: bytes) -> bool:
    pixels = png_pixels(a)
    return pixels == png_pixels(b) if pixels is not None else a == b


def same_file(name: str, committed: bytes, drawn: bytes) -> bool:
    """Whether a committed icon file shows what this draws: PNGs (also the
    PNG entry of the .ico) compare by pixels, since another zlib may pack the
    same rows to other bytes; everything else byte for byte."""
    if name.endswith('.png'):
        return png_pixels(committed) is not None and png_pixels(committed) == png_pixels(drawn)
    if name.endswith('.ico'):
        mine, theirs = _ico_entries(committed), _ico_entries(drawn)
        return (
            mine is not None and theirs is not None and len(mine) == len(theirs)
            and all(fa == fb and _same_image(ia, ib) for (fa, ia), (fb, ib) in zip(mine, theirs))
        )
    return committed == drawn


def stale(files: dict[str, bytes]) -> list[str]:
    """The icon files missing from ``OUT`` or not showing what ``files`` do."""
    return [name for name, data in files.items()
            if not (OUT / name).is_file() or not same_file(name, (OUT / name).read_bytes(), data)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--check', action='store_true', help='fail when the icon files differ from what this draws')
    args = parser.parse_args()
    files = outputs()
    if args.check:
        stale_files = stale(files)
        if stale_files:
            print(f'stale icon files (run scripts/cinedex_icon.py): {", ".join(stale_files)}', file=sys.stderr)
            return 1
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (OUT / name).write_bytes(data)
        print(f'wrote {OUT / name} ({len(data)} bytes)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
