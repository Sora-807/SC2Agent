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

/* ---------------- 地形量化（F11b：台地-悬崖-斜坡，U15 显示层） ---------------- */

/**
 * 高度重方图量化：出现过的离散高度值 → 有序 level 索引（每格一个 level）。
 *
 * SC2 的 height 栅格本来就是离散台地值，直方图取“出现过的值”即可；
 * 频次低于噪声阈值的值（扫描毛刺）并入**最近的**幸存 level，不单独成级。
 * 这是显示层量化（U15）：一旦斜坡要被命名/被谓词引用就必须上移后端（PLAN §12 结论 3）。
 */
export function quantizeLevels(grid: DecodedGrid): { levels: Uint8Array; count: number } {
  const total = grid.w * grid.h;
  const hist = new Map<number, number>();
  for (let i = 0; i < total; i += 1) {
    const v = grid.data[i]!;
    hist.set(v, (hist.get(v) ?? 0) + 1);
  }
  const noiseFloor = Math.max(1, Math.floor(total * 0.001));
  const kept = [...hist.entries()]
    .filter(([, n]) => n >= noiseFloor)
    .map(([v]) => v)
    .sort((a, b) => a - b);
  if (kept.length === 0) return { levels: new Uint8Array(total), count: 0 };
  const levels = new Uint8Array(total);
  for (let i = 0; i < total; i += 1) {
    const v = grid.data[i]!;
    // 二分找最近幸存值（噪声值并入相邻级）
    let lo = 0;
    let hi = kept.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (kept[mid]! < v) lo = mid + 1;
      else hi = mid;
    }
    let idx = lo;
    if (lo > 0 && Math.abs(kept[lo - 1]! - v) <= Math.abs(kept[lo]! - v)) idx = lo - 1;
    levels[i] = idx;
  }
  return { levels, count: kept.length };
}

/** 4 邻域 level 不同的格为真（悬崖/台地边缘）。 */
export function cliffMask(levels: Uint8Array, w: number, h: number): Uint8Array {
  const out = new Uint8Array(w * h);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const i = y * w + x;
      const v = levels[i]!;
      if (
        (x > 0 && levels[i - 1] !== v) ||
        (x < w - 1 && levels[i + 1] !== v) ||
        (y > 0 && levels[i - w] !== v) ||
        (y < h - 1 && levels[i + w] !== v)
      ) {
        out[i] = 1;
      }
    }
  }
  return out;
}

/** 斜坡掩码：pathable 且邻域跨 level 的格（连接台地的可走过渡带）。 */
export function rampMask(
  levels: Uint8Array,
  pathable: DecodedGrid | null,
  w: number,
  h: number,
): Uint8Array {
  const cliffs = cliffMask(levels, w, h);
  const out = new Uint8Array(w * h);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const i = y * w + x;
      if (cliffs[i] && pathable && pathable.at(x, y) > 0) out[i] = 1;
    }
  }
  return out;
}

/** slate 阶梯：level 索引 → 有序深→浅台地色。 */
function levelColor(level: number, count: number): [number, number, number] {
  const stops: Array<[number, number, number]> = [
    [23, 33, 47], // slate-900 附近（最低台地最暗）
    [38, 52, 68], // slate-800
    [56, 74, 92], // slate-700
    [82, 100, 117], // slate-600
    [120, 136, 152], // slate-500
    [168, 180, 194], // slate-400（最高台地最亮）
  ];
  if (count <= 1) return stops[1]!;
  const t = (level / (count - 1)) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(t));
  const f = t - i;
  const a = stops[i]!;
  const b = stops[i + 1]!;
  return [
    Math.round(a[0] + (b[0] - a[0]) * f),
    Math.round(a[1] + (b[1] - a[1]) * f),
    Math.round(a[2] + (b[2] - a[2]) * f),
  ];
}

/**
 * 把三张地形图烤成**一张**位图（只在地图变化时做一次）：
 * - 台地：level 用有序深→浅 slate 阶梯；离散台地不再被连续绿色渐变糊掉（根因 F）；
 * - 悬崖：只画描边不画填充 —— 边缘格压成硬暗线（根因 G 的一半）；
 * - 斜坡：跨 level 的可走格用暖色 + (x+y)%2 低频抖动近似斜纹，从台地里浮出来；
 * - 不可走：不再红涂，只压暗去饱和（饱和色预算留给单位/建筑/标记）。
 *
 * placeable 不进默认地形（它是编辑器背景，F14 在真实地面上放标记时用）。
 */
export function bakeTerrain(
  height: DecodedGrid | null,
  pathable: DecodedGrid | null,
): ImageData | null {
  if (!height) return null;
  const { levels, count } = quantizeLevels(height);
  const w = height.w;
  const h = height.h;
  const cliffs = cliffMask(levels, w, h);
  const ramps = rampMask(levels, pathable, w, h);
  const img = new ImageData(w, h);
  for (let y = 0; y < h; y += 1) {
    const srcRow = h - 1 - y; // 世界 y 向上、canvas y 向下：翻转一次（与 bakeGrid 同约定）
    for (let x = 0; x < w; x += 1) {
      const src = srcRow * w + x;
      let [r, g, b] = levelColor(levels[src]!, count);
      const walk = !pathable || pathable.at(x, srcRow) > 0;
      if (!walk) {
        // 不可走：压暗去饱和（不用红色平涂抢对比度）
        const gray = (r + g + b) / 3;
        r = Math.round((r * 0.35 + gray * 0.65) * 0.62);
        g = Math.round((g * 0.35 + gray * 0.65) * 0.62);
        b = Math.round((b * 0.35 + gray * 0.65) * 0.62);
      }
      if (ramps[src]) {
        // 斜坡：暖色抖动（隔格）—— 低频纹理近似斜纹，烤一次零运行时成本
        if ((x + srcRow) % 2 === 0) {
          r = Math.round(r * 0.4 + 180 * 0.6);
          g = Math.round(g * 0.4 + 130 * 0.6);
          b = Math.round(b * 0.4 + 60 * 0.6);
        } else {
          r = Math.round(r * 0.75 + 180 * 0.25);
          g = Math.round(g * 0.75 + 130 * 0.25);
          b = Math.round(b * 0.75 + 60 * 0.25);
        }
      } else if (cliffs[src]) {
        // 悬崖：硬暗描边
        r = Math.round(r * 0.3);
        g = Math.round(g * 0.3);
        b = Math.round(b * 0.32);
      }
      const i = (y * w + x) * 4;
      img.data[i] = r;
      img.data[i + 1] = g;
      img.data[i + 2] = b;
      img.data[i + 3] = 255;
    }
  }
  return img;
}

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  const f = (n: number): number => {
    const k = (n + h * 12) % 12;
    const a = s * Math.min(l, 1 - l);
    return Math.round(255 * (l - a * Math.max(-1, Math.min(k - 3, Math.min(9 - k, 1)))));
  };
  return [f(0), f(8), f(4)];
}
