"""Pembuat ikon Force Eject Disk (tanpa dependensi: PNG + ICO ditulis manual).

Jalankan: python make_icon.py
Menghasilkan: ForceEjectDisk.ico (dipakai EXE, tray, dan ikon jendela).
"""

from __future__ import annotations

import struct
import zlib

# ------------------------------------------------------------------ geometri ---
INSET = 0.045  # jarak tile dari tepi kanvas
CORNER = 0.24  # radius sudut tile
TOP_COLOR = (0x3B, 0x82, 0xF6)  # biru terang
BOTTOM_COLOR = (0x12, 0x3F, 0x8F)  # biru tua
GLYPH_COLOR = (0xFF, 0xFF, 0xFF)
# panah eject: segitiga menghadap ke atas + batang di bawahnya
TRIANGLE = ((0.235, 0.615), (0.765, 0.615), (0.5, 0.225))
BAR = (0.235, 0.685, 0.765, 0.795, 0.022)  # x0, y0, x1, y1, radius
SIZES = (16, 24, 32, 48, 64, 128, 256)
SUPERSAMPLE = 4

# -------------------------------------------------------------------- bentuk ---
def _rounded_rect(x: float, y: float, x0: float, y0: float, x1: float, y1: float, r: float) -> bool:
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _triangle(x: float, y: float, triangle) -> bool:
    (ax, ay), (bx, by), (cx, cy) = triangle
    d1 = (x - bx) * (ay - by) - (ax - bx) * (y - by)
    d2 = (x - cx) * (by - cy) - (bx - cx) * (y - cy)
    d3 = (x - ax) * (cy - ay) - (cx - ax) * (y - ay)
    has_negative = d1 < 0 or d2 < 0 or d3 < 0
    has_positive = d1 > 0 or d2 > 0 or d3 > 0
    return not (has_negative and has_positive)


def _sample(x: float, y: float):
    """Warna + alpha untuk satu titik di ruang 0..1 (premultiplied)."""
    if not _rounded_rect(x, y, INSET, INSET, 1 - INSET, 1 - INSET, CORNER):
        return 0.0, 0.0, 0.0, 0.0
    if _triangle(x, y, TRIANGLE) or _rounded_rect(x, y, *BAR):
        red, green, blue = GLYPH_COLOR
    else:
        # gradasi vertikal lembut di dalam tile
        t = (y - INSET) / (1 - 2 * INSET)
        red, green, blue = (
            round(TOP_COLOR[i] + (BOTTOM_COLOR[i] - TOP_COLOR[i]) * t) for i in range(3)
        )
    return float(red), float(green), float(blue), 1.0


def render(size: int) -> bytes:
    """Render ikon RGBA ukuran size x size (byte mentah 8-bit, baris atas dulu)."""
    step = 1.0 / (size * SUPERSAMPLE)
    samples = SUPERSAMPLE * SUPERSAMPLE
    rows = bytearray()
    for py in range(size):
        row = bytearray()
        for px in range(size):
            sum_r = sum_g = sum_b = sum_a = 0.0
            for sy in range(SUPERSAMPLE):
                y = (py * SUPERSAMPLE + sy + 0.5) * step
                for sx in range(SUPERSAMPLE):
                    x = (px * SUPERSAMPLE + sx + 0.5) * step
                    red, green, blue, alpha = _sample(x, y)
                    sum_r += red * alpha
                    sum_g += green * alpha
                    sum_b += blue * alpha
                    sum_a += alpha
            if sum_a:
                row += bytes(
                    (
                        round(sum_r / sum_a),
                        round(sum_g / sum_a),
                        round(sum_b / sum_a),
                        round(255 * sum_a / samples),
                    )
                )
            else:
                row += b"\0\0\0\0"
        rows += b"\0" + row  # filter tipe 0 (None) di awal tiap baris
    return bytes(rows)


# ----------------------------------------------------------------- PNG / ICO ---
def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def png_bytes(size: int, raw_rows: bytes) -> bytes:
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw_rows, 9))
        + _chunk(b"IEND", b"")
    )


def ico_bytes(images: list[tuple[int, bytes]]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(images))
    entries = b""
    offset = 6 + 16 * len(images)
    payload = b""
    for size, data in images:
        entries += struct.pack(
            "<BBBBHHII", size if size < 256 else 0, size if size < 256 else 0, 0, 0, 1, 32, len(data), offset
        )
        payload += data
        offset += len(data)
    return header + entries + payload


def main() -> int:
    images = []
    for size in SIZES:
        images.append((size, png_bytes(size, render(size))))
        print(f"dirender {size}x{size}")
    with open("ForceEjectDisk.ico", "wb") as handle:
        handle.write(ico_bytes(images))
    print("ForceEjectDisk.ico:", sum(len(data) for _, data in images), "byte isi")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
