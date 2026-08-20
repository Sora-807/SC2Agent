/**
 * 地图渲染（F2）—— 分层 Canvas2D。
 *
 * 三条纪律：
 * 1. **零几何复算**：footprint 直接用帧里的 `tl/br`，槽位直接用 `tl/br`，
 *    ADR-0027 的换算在后端做完了（红线 C2）；
 * 2. **位置可插值、进度绝不插值**（决策 U5）：单位坐标在两个真实采样点之间 lerp，
 *    build_progress / 计数一律用当帧原值；
 * 3. 帧里没有的层置灰并说明原因，不猜、不补默认值（红线 C7）。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { MapStatic, WorldFrame, ProductionFrame } from "../contract";
import { bakeGrid, decodeGrid, regionColor, type Palette } from "./grid";
import { fitViewport, screenToWorld, worldToScreen, zoomAt, type Viewport } from "./view";
import type { LayerState } from "./layers";

const OWNER_COLOR: Record<string, string> = {
  self: "#34d399",
  ally: "#38bdf8",
  enemy: "#f87171",
  neutral: "#94a3b8",
};

const SLOT_COLOR: Record<string, string> = {
  supply: "#a3a3a3",
  production: "#60a5fa",
  addon: "#c084fc",
};

export interface Selection {
  kind: "unit";
  tag: number;
}

export function MapCanvas(props: {
  map: MapStatic;
  world: WorldFrame | null;
  production: ProductionFrame | null;
  layers: LayerState;
  smooth: boolean;
  selection: Selection | null;
  onSelect: (s: Selection | null) => void;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [size, setSize] = useState<{ w: number; h: number }>({ w: 800, h: 520 });
  const [vp, setVp] = useState<Viewport | null>(null);
  const drag = useRef<{ x: number; y: number } | null>(null);

  // 插值用：上一帧与本帧（以及本帧到达的墙钟时刻）
  const prev = useRef<{ world: WorldFrame; at: number } | null>(null);
  const cur = useRef<{ world: WorldFrame; at: number } | null>(null);
  useEffect(() => {
    if (!props.world) return;
    prev.current = cur.current;
    cur.current = { world: props.world, at: performance.now() };
  }, [props.world]);

  // 画布尺寸
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const ro = new ResizeObserver(() => {
      setSize({ w: Math.max(200, host.clientWidth), h: Math.max(200, host.clientHeight) });
    });
    ro.observe(host);
    setSize({ w: Math.max(200, host.clientWidth), h: Math.max(200, host.clientHeight) });
    return () => ro.disconnect();
  }, []);

  // 视口（地图换了或尺寸变了就重置到 fit）
  useEffect(() => {
    setVp(fitViewport(size.w, size.h, props.map.size[0], props.map.size[1]));
  }, [size.w, size.h, props.map.size]);

  // 静态位图：地形三图只烤一次（B4；缺哪张画哪张，terrain=null 时降级纯色底）
  const terrainImages = useMemo(() => {
    const t = props.map.terrain;
    return {
      height: t?.height ? bakeGrid(decodeGrid(t.height), (v) =>
        [46, 72, 46 + Math.min(120, v * 3), 255] as [number, number, number, number]) : null,
      pathable: t?.pathable ? bakeGrid(decodeGrid(t.pathable), (v) =>
        v > 0 ? [24, 64, 30, 70] : [90, 20, 20, 70]) : null,
      placeable: t?.placeable ? bakeGrid(decodeGrid(t.placeable), (v) =>
        v > 0 ? [30, 80, 40, 46] : null) : null,
    };
  }, [props.map]);

  // 静态位图：区域标签网格只烤一次
  const regionImage = useMemo(() => {
    const grid = props.map.regions.leaf_grid ?? props.map.regions.big_grid;
    const index = props.map.regions.leaf_grid
      ? props.map.regions.leaf_index
      : props.map.regions.big_index;
    if (!grid) return null;
    const decoded = decodeGrid(grid);
    const palette: Palette = (label) => {
      const id = index[String(label)];
      return id ? regionColor(id) : null;
    };
    return bakeGrid(decoded, palette);
  }, [props.map]);

  const gridImages = useMemo(() => {
    const w = props.world;
    const vis = w?.grids?.visibility;
    const creep = w?.grids?.creep;
    return {
      visibility: vis
        ? bakeGrid(decodeGrid(vis), (v) => (v === 0 ? [0, 0, 0, 150] : v === 1 ? [0, 0, 0, 70] : null))
        : null,
      creep: creep
        ? bakeGrid(decodeGrid(creep), (v) => (v > 0 ? [147, 51, 234, 60] : null))
        : null,
    };
  }, [props.world]);

  // 绘制循环
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !vp) return;
    let raf = 0;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const draw = (): void => {
      raf = requestAnimationFrame(draw);
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      if (canvas.width !== Math.floor(vp.cw * dpr)) canvas.width = Math.floor(vp.cw * dpr);
      if (canvas.height !== Math.floor(vp.ch * dpr)) canvas.height = Math.floor(vp.ch * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, vp.cw, vp.ch);
      paint(ctx, vp, props, { regionImage, gridImages, terrainImages }, {
        prevWorld: prev.current?.world ?? null,
        alpha: lerpAlpha(prev.current, cur.current, props.smooth),
      });
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [vp, props, regionImage, gridImages]);

  return (
    <div ref={hostRef} className="relative h-full w-full">
      <canvas
        ref={canvasRef}
        style={{ width: vp?.cw ?? size.w, height: vp?.ch ?? size.h }}
        className="block cursor-crosshair rounded bg-[#0d1117]"
        onWheel={(e) => {
          e.preventDefault();
          if (!vp) return;
          const r = e.currentTarget.getBoundingClientRect();
          setVp(zoomAt(vp, e.clientX - r.left, e.clientY - r.top, e.deltaY < 0 ? 1.15 : 1 / 1.15));
        }}
        onPointerDown={(e) => {
          drag.current = { x: e.clientX, y: e.clientY };
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (!drag.current || !vp) return;
          const dx = e.clientX - drag.current.x;
          const dy = e.clientY - drag.current.y;
          drag.current = { x: e.clientX, y: e.clientY };
          setVp({ ...vp, panX: vp.panX + dx, panY: vp.panY + dy });
        }}
        onPointerUp={(e) => {
          const moved = drag.current;
          drag.current = null;
          e.currentTarget.releasePointerCapture(e.pointerId);
          if (!vp || !props.world || !moved) return;
          const r = e.currentTarget.getBoundingClientRect();
          const [wx, wy] = screenToWorld(vp, e.clientX - r.left, e.clientY - r.top);
          const hit = nearestUnit(props.world, wx, wy, 12 / vp.scale);
          props.onSelect(hit === null ? null : { kind: "unit", tag: hit });
        }}
      />
      <div className="pointer-events-none absolute bottom-1 right-2 text-[10px] text-neutral-500">
        滚轮缩放 · 拖动平移 · 点击选中 · {vp ? vp.scale.toFixed(1) : "?"} px/格
      </div>
    </div>
  );
}

/** 插值系数：只在"两帧都在、且开启平滑"时才 >0；进度条不用它 */
function lerpAlpha(
  prev: { world: WorldFrame; at: number } | null,
  cur: { world: WorldFrame; at: number } | null,
  smooth: boolean,
): number {
  if (!smooth || !prev || !cur) return 1;
  const expected = Math.max(60, cur.at - prev.at);
  return Math.max(0, Math.min(1, (performance.now() - cur.at) / expected));
}

function nearestUnit(world: WorldFrame, wx: number, wy: number, radius: number): number | null {
  let best: number | null = null;
  let bestD = radius * radius;
  for (const u of world.units) {
    const dx = u.pos[0] - wx;
    const dy = u.pos[1] - wy;
    const d = dx * dx + dy * dy;
    if (d <= bestD) {
      bestD = d;
      best = u.tag;
    }
  }
  return best;
}

interface Baked {
  regionImage: ImageData | null;
  gridImages: { visibility: ImageData | null; creep: ImageData | null };
  terrainImages: { height: ImageData | null; pathable: ImageData | null; placeable: ImageData | null };
}

interface Motion {
  /** 上一帧（只用它的坐标做插值；其它字段一律取当帧） */
  prevWorld: WorldFrame | null;
  /** 0..1，1 = 完全用当帧坐标 */
  alpha: number;
}

function paint(
  ctx: CanvasRenderingContext2D,
  vp: Viewport,
  props: Parameters<typeof MapCanvas>[0],
  baked: Baked,
  motion: Motion,
): void {
  const { map, world, production, layers } = props;
  const alpha = motion.alpha;

  // 底：terrain 为 null 时用纯色 —— 后端 B4 之前不伪造地形（不静默）
  ctx.fillStyle = "#111820";
  const [x0, y0] = worldToScreen(vp, 0, map.size[1]);
  ctx.fillRect(x0, y0, map.size[0] * vp.scale, map.size[1] * vp.scale);
  if (baked.terrainImages.height) {
    drawImage(ctx, vp, baked.terrainImages.height, map);
  }
  if (baked.terrainImages.pathable) {
    drawImage(ctx, vp, baked.terrainImages.pathable, map);
  }
  if (baked.terrainImages.placeable) {
    drawImage(ctx, vp, baked.terrainImages.placeable, map);
  }

  if (layers.regions && baked.regionImage) drawImage(ctx, vp, baked.regionImage, map);
  if (layers.creep && baked.gridImages.creep) drawImage(ctx, vp, baked.gridImages.creep, map);
  if (layers.visibility && baked.gridImages.visibility) {
    drawImage(ctx, vp, baked.gridImages.visibility, map);
  }

  if (layers.slots) {
    for (const s of map.build_slots) {
      const [sx, sy] = worldToScreen(vp, s.tl[0], s.br[1] + 1);
      ctx.strokeStyle = SLOT_COLOR[s.kind] ?? "#666";
      ctx.globalAlpha = 0.55;
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 2]);
      ctx.strokeRect(sx, sy, s.size * vp.scale, s.size * vp.scale);
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    }
  }

  if (layers.resources && world) {
    const byTag = new Map(world.resource_state.map((r) => [r.tag, r]));
    for (const n of map.resource_nodes) {
      const [sx, sy] = worldToScreen(vp, n.pos[0], n.pos[1]);
      const st = byTag.get(n.tag);
      ctx.beginPath();
      ctx.arc(sx, sy, Math.max(2, vp.scale * 0.45), 0, Math.PI * 2);
      ctx.fillStyle = n.kind === "geyser" ? "#22c55e" : "#7dd3fc";
      ctx.globalAlpha = 0.85;
      ctx.fill();
      ctx.globalAlpha = 1;
      if (st && st.workers > 0) {
        ctx.fillStyle = "#e5e7eb";
        ctx.font = "9px ui-sans-serif";
        ctx.fillText(String(st.workers), sx + 4, sy - 4);
      }
    }
  }

  if (world) {
    // 只有位置插值：从上一帧取同 tag 的坐标（进度/计数一律用当帧原值，决策 U5）
    const prevPos = new Map<number, [number, number]>();
    if (alpha < 1 && motion.prevWorld) {
      for (const u of motion.prevWorld.units) prevPos.set(u.tag, u.pos);
    }

    if (layers.buildings) {
      for (const u of world.units) {
        if (!u.footprint) continue;
        const [sx, sy] = worldToScreen(vp, u.footprint.tl[0], u.footprint.br[1] + 1);
        const w = (u.footprint.br[0] - u.footprint.tl[0] + 1) * vp.scale;
        const h = (u.footprint.br[1] - u.footprint.tl[1] + 1) * vp.scale;
        const done = u.build_progress >= 1;
        ctx.fillStyle = done ? "rgba(52,211,153,0.16)" : "rgba(251,191,36,0.16)";
        ctx.fillRect(sx, sy, w, h);
        ctx.strokeStyle = OWNER_COLOR[u.owner] ?? "#888";
        ctx.lineWidth = done ? 1.2 : 1;
        ctx.strokeRect(sx, sy, w, h);
        if (!done) {
          // 进度条：**用当帧原值，绝不插值**（决策 U5 / ADR-0017 禁止伪造进度动画）
          ctx.fillStyle = "#fbbf24";
          ctx.fillRect(sx, sy + h - 2, w * u.build_progress, 2);
        }
        if (u.producing && u.producing.length > 0) {
          ctx.fillStyle = "#e5e7eb";
          ctx.font = "9px ui-sans-serif";
          ctx.fillText(String(u.producing.length), sx + 2, sy + 9);
        }
      }
    }

    if (layers.units) {
      for (const u of world.units) {
        if (u.footprint) continue;
        const from = prevPos.get(u.tag);
        const px = from ? from[0] + (u.pos[0] - from[0]) * alpha : u.pos[0];
        const py = from ? from[1] + (u.pos[1] - from[1]) * alpha : u.pos[1];
        const [sx, sy] = worldToScreen(vp, px, py);
        const r = Math.max(1.5, vp.scale * 0.3);
        ctx.beginPath();
        ctx.arc(sx, sy, r, 0, Math.PI * 2);
        ctx.fillStyle = OWNER_COLOR[u.owner] ?? "#888";
        ctx.fill();
        if (layers.groups && u.group_id) {
          ctx.strokeStyle = "#fde68a";
          ctx.lineWidth = 1;
          ctx.stroke();
        }
        if (u.form) {                       // 形态变体（如架起的坦克）画个方框区分
          ctx.strokeStyle = "#fca5a5";
          ctx.strokeRect(sx - r - 1, sy - r - 1, (r + 1) * 2, (r + 1) * 2);
        }
        if (layers.orders && u.order?.target_pos) {
          const [tx, ty] = worldToScreen(vp, u.order.target_pos[0], u.order.target_pos[1]);
          ctx.strokeStyle = "rgba(148,163,184,0.35)";
          ctx.setLineDash([3, 3]);
          ctx.beginPath();
          ctx.moveTo(sx, sy);
          ctx.lineTo(tx, ty);
          ctx.stroke();
          ctx.setLineDash([]);
        }
      }
    }

    if (layers.clusters && world.enemy_clusters) {
      for (const c of world.enemy_clusters) {
        const [sx, sy] = worldToScreen(vp, c.center[0], c.center[1]);
        ctx.beginPath();
        ctx.arc(sx, sy, c.radius * vp.scale, 0, Math.PI * 2);
        ctx.strokeStyle = "rgba(248,113,113,0.7)";
        ctx.stroke();
      }
    }
  }

  // 摆放调试：在途建造的期望落点 + 已试过的槽位（F5 的叠加层，数据在 frame/production 里）
  if (layers.placement && production) {
    for (const f of production.in_flight) {
      if (f.expect_pos) {
        const [sx, sy] = worldToScreen(vp, f.expect_pos[0], f.expect_pos[1]);
        ctx.strokeStyle = "#f472b6";
        ctx.beginPath();
        ctx.arc(sx, sy, f.radius * vp.scale, 0, Math.PI * 2);
        ctx.stroke();
        ctx.fillStyle = "#f472b6";
        ctx.font = "9px ui-sans-serif";
        ctx.fillText(f.stable_id + " " + f.frames_waited + "f", sx + 4, sy - 4);
      }
      for (const name of f.attempted_slots) {
        const slot = props.map.build_slots.find((s) => s.name === name);
        if (!slot) continue;
        const [sx, sy] = worldToScreen(vp, slot.tl[0], slot.br[1] + 1);
        ctx.strokeStyle = "rgba(244,114,182,0.6)";
        ctx.setLineDash([1, 2]);
        ctx.strokeRect(sx, sy, slot.size * vp.scale, slot.size * vp.scale);
        ctx.setLineDash([]);
      }
    }
  }

  // 选中高亮
  if (props.selection && world) {
    const u = world.units.find((x) => x.tag === props.selection!.tag);
    if (u) {
      const [sx, sy] = worldToScreen(vp, u.pos[0], u.pos[1]);
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(sx, sy, Math.max(5, vp.scale * 0.7), 0, Math.PI * 2);
      ctx.stroke();
    }
  }
}

/** ImageData → 离屏 canvas。缓存起来：位图只在帧变化时重烤，不能每个动画帧都重建。 */
const offscreen = new WeakMap<ImageData, HTMLCanvasElement>();

function drawImage(
  ctx: CanvasRenderingContext2D,
  vp: Viewport,
  img: ImageData,
  map: MapStatic,
): void {
  let off = offscreen.get(img);
  if (!off) {
    off = document.createElement("canvas");
    off.width = img.width;
    off.height = img.height;
    off.getContext("2d")?.putImageData(img, 0, 0);
    offscreen.set(img, off);
  }
  const [sx, sy] = worldToScreen(vp, 0, map.size[1]);
  ctx.imageSmoothingEnabled = false;   // 格点位图要锐利，不要被插值糊掉
  ctx.drawImage(off, sx, sy, map.size[0] * vp.scale, map.size[1] * vp.scale);
}