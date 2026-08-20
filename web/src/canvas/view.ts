/**
 * 世界坐标 ↔ 屏幕坐标。
 *
 * 世界是左下原点、y 向上（`game.geometry` 的约定）；canvas 是左上原点、y 向下。
 * 转换只在这里做一次，任何绘制都必须经过它 —— 两套约定混用是地图类 bug 的头号来源。
 */
export interface Viewport {
  /** 画布像素尺寸 */
  cw: number;
  ch: number;
  /** 地图格数 */
  mw: number;
  mh: number;
  /** 缩放（像素/格）与平移（像素） */
  scale: number;
  panX: number;
  panY: number;
}

export function fitViewport(cw: number, ch: number, mw: number, mh: number): Viewport {
  const scale = Math.min(cw / mw, ch / mh);
  return {
    cw, ch, mw, mh, scale,
    panX: (cw - mw * scale) / 2,
    panY: (ch - mh * scale) / 2,
  };
}

export function worldToScreen(v: Viewport, x: number, y: number): [number, number] {
  return [v.panX + x * v.scale, v.panY + (v.mh - y) * v.scale];
}

export function screenToWorld(v: Viewport, sx: number, sy: number): [number, number] {
  return [(sx - v.panX) / v.scale, v.mh - (sy - v.panY) / v.scale];
}

export function zoomAt(v: Viewport, sx: number, sy: number, factor: number): Viewport {
  const next = Math.max(0.4, Math.min(24, v.scale * factor));
  const [wx, wy] = screenToWorld(v, sx, sy);
  // 让缩放锚定在鼠标下的世界点：先算新 scale，再反解出需要的 pan
  return {
    ...v,
    scale: next,
    panX: sx - wx * next,
    panY: sy - (v.mh - wy) * next,
  };
}
