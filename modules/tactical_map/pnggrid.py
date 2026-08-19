"""tactical_map.pnggrid：调色板 PNG → 整数格点层（区域 authoring 的格点来源，零第三方依赖）。

仅支持 8-bit、color type 3（indexed/palette）、非隔行 PNG。
palette 色表本身不参与解码——我们取的是索引值（palette 索引 = 区域 key）。
输出 list[list[int]]，data[y][x] 索引约定与 game.Grid 一致。
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path


def load_palette_png(path: str | Path) -> list[list[int]]:
    """从文件读调色板 PNG → 索引格点层。"""
    return decode_palette_png(Path(path).read_bytes())


def decode_palette_png(data: bytes) -> list[list[int]]:
    """解码 8-bit palette PNG（color type 3, 非隔行）→ list[list[int]]。"""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    pos = 8
    width = height = bit_depth = color_type = interlace = None
    idat = b""
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            width, height, bit_depth, color_type, _comp, _filt, interlace = struct.unpack(">IIBBBBB", payload)
        elif tag == b"IDAT":
            idat += payload
        elif tag == b"IEND":
            break
        # PLTE 及其他 chunk 忽略
    if width is None:
        raise ValueError("PNG 缺 IHDR")
    if bit_depth != 8 or color_type != 3:
        raise ValueError(f"仅支持 8-bit palette PNG（got bit_depth={bit_depth}, color_type={color_type}）")
    if interlace != 0:
        raise ValueError("不支持隔行（interlaced）PNG")
    raw = zlib.decompress(idat)
    stride = width + 1
    if len(raw) != stride * height:
        raise ValueError(f"PNG raw 尺寸不符：{len(raw)} != {stride}*{height}")
    rows: list[list[int]] = []
    prev = [0] * width
    for y in range(height):
        line = raw[y * stride:(y + 1) * stride]
        f = line[0]
        cur = list(line[1:])
        if f == 1:  # Sub
            for x in range(1, width):
                cur[x] = (cur[x] + cur[x - 1]) & 0xFF
        elif f == 2:  # Up
            for x in range(width):
                cur[x] = (cur[x] + prev[x]) & 0xFF
        elif f == 3:  # Average
            for x in range(width):
                left = cur[x - 1] if x > 0 else 0
                cur[x] = (cur[x] + (left + prev[x]) // 2) & 0xFF
        elif f == 4:  # Paeth
            for x in range(width):
                a = cur[x - 1] if x > 0 else 0
                b = prev[x]
                c = prev[x - 1] if x > 0 else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                cur[x] = (cur[x] + pred) & 0xFF
        elif f != 0:
            raise ValueError(f"未知 PNG filter 类型 {f}")
        rows.append(cur)
        prev = cur
    return rows
