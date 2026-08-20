/**
 * GridB64 解码与位图化。
 *
 * 帧里的栅格是"行主序 uint8 + base64"（契约 C5）：一张位图 + 一份调色板，
 * 所以区域/视野/菌毯都用同一条路径画，前端不需要认识它们的语义。
 */
import type { GridB64 } from "../contract";

export interface DecodedGrid {
  w: number;
  h: number;
  data: Uint8Array;
  /** 取 (x, y) 处的标签值；越界返回 0 */
  at(x: number, y: number): number;
}

export function decodeGrid(g: GridB64): DecodedGrid {
  const bin = atob(g.data_b64);
  const data = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) data[i] = bin.charCodeAt(i);
  return {
    w: g.w,
    h: g.h,
    data,
    at(x, y) {
      if (x < 0 || y < 0 || x >= g.w || y >= g.h) return 0;
      return data[y * g.w + x] ?? 0;
    },
  };
}

export type Palette = (label: number) => [number, number, number, number] | null;

/**
 * 把标签网格烤成一张 ImageBitmap 级别的 ImageData（只在网格变化时做一次）。
 *
 * 注意 y 轴：世界坐标 y 向上，canvas y 向下，所以这里就把行序翻过来 ——
 * 翻转只做一次，后面所有绘制都用同一个 `worldToScreen`，不会两套约定打架。
 */
export function bakeGrid(grid: DecodedGrid, palette: Palette): ImageData {
  const img = new ImageData(grid.w, grid.h);
  for (let y = 0; y < grid.h; y += 1) {
    const srcRow = grid.h - 1 - y; // 翻转
    for (let x = 0; x < grid.w; x += 1) {
      const label = grid.at(x, srcRow);
      const rgba = palette(label);
      const i = (y * grid.w + x) * 4;
      if (rgba === null) {
        img.data[i + 3] = 0;
        continue;
      }
      img.data[i] = rgba[0];
      img.data[i + 1] = rgba[1];
      img.data[i + 2] = rgba[2];
      img.data[i + 3] = rgba[3];
    }
  }
  return img;
}

/** 稳定的区域配色：同一个 region id 每次都得到同一个颜色（不随渲染顺序变） */
export function regionColor(id: string, alpha = 46): [number, number, number, number] {
  let h = 0;
  for (let i = 0; i < id.length; i += 1) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  const hue = h % 360;
  const [r, g, b] = hslToRgb(hue / 360, 0.55, 0.55);
  return [r, g, b, alpha];
}

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  const f = (n: number): number => {
    const k = (n + h * 12) % 12;
    const a = s * Math.min(l, 1 - l);
    return Math.round(255 * (l - a * Math.max(-1, Math.min(k - 3, Math.min(9 - k, 1)))));
  };
  return [f(0), f(8), f(4)];
}
