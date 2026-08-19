"""调色板 PNG 解码 + 区域层 PNG 加载（ADR-0029 D3 authoring 形态）。"""
import struct
import zlib

import pytest

from game import Point2
from tactical_map import load_region_layer
from tactical_map.pnggrid import decode_palette_png


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload))


def _png(width: int, height: int, rows: list[list[int]]) -> bytes:
    """构造 8-bit palette PNG（filter 全 0，简单起见）。"""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 3, 0, 0, 0)
    plte = b"".join(bytes((i, i, i)) for i in range(256))
    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", ihdr)
            + _chunk(b"PLTE", plte)
            + _chunk(b"IDAT", zlib.compress(raw))
            + _chunk(b"IEND", b""))


def test_decode_roundtrip():
    rows = [[1, 2], [2, 0]]
    assert decode_palette_png(_png(2, 2, rows)) == rows


def test_reject_non_palette_png():
    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)  # color type 2（RGB）
    png = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IEND", b"")
    with pytest.raises(ValueError, match="palette"):
        decode_palette_png(png)


def test_reject_not_png():
    with pytest.raises(ValueError, match="PNG"):
        decode_palette_png(b"not a png at all")


def test_load_layer_from_png(tmp_path):
    big = tmp_path / "big.png"
    leaf = tmp_path / "leaf.png"
    big.write_bytes(_png(4, 4, [[1, 1, 1, 1], [1, 1, 1, 1], [2, 2, 2, 2], [2, 2, 2, 2]]))
    leaf.write_bytes(_png(4, 4, [[0, 0, 0, 0], [0, 1, 1, 0], [0, 0, 0, 0], [0, 0, 0, 0]]))
    yaml_str = """
map_name: png_map
size: [4, 4]
big_palette: {1: main_base, 2: field}
big_grid_png: big.png
leaf_palette: {1: main_ramp}
leaf_grid_png: leaf.png
big_regions:
  main_base: {anchor: [1, 0]}
  field: {anchor: [3, 3]}
regions:
  main_ramp: {parent: main_base, anchor: [1, 1]}
"""
    layer = load_region_layer(yaml_str, base_dir=tmp_path)
    assert layer.map_name == "png_map"
    assert layer.region_at(Point2(1.5, 1.5)) == ("main_base", "main_ramp")
    assert layer.region_at(Point2(0.5, 3.5)) == ("field", None)

