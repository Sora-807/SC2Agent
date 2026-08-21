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

/** 屏幕中心对应的世界点。resize 重锚与"回到中心"都以它为基准。 */
export function viewportCenter(v: Viewport): [number, number] {
  return screenToWorld(v, v.cw / 2, v.ch / 2);
}

/**
 * 画布尺寸变了：**保住 scale 与"屏幕中心对应的世界点"**，只重算 pan（红线 G2）。
 *
 * 之前这里是直接 `fitViewport` 重置 —— 于是改窗口大小、收起对话栏、甚至侧栏里多一行字
 * 都会把用户的缩放和平移丢掉。resize 是"窗口变了"，不是"换了张地图"，两者必须分开。
 */
export function resizeViewport(v: Viewport, cw: number, ch: number): Viewport {
  const [wx, wy] = viewportCenter(v);
  return {
    ...v,
    cw,
    ch,
    panX: cw / 2 - wx * v.scale,
    panY: ch / 2 - (v.mh - wy) * v.scale,
  };
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
