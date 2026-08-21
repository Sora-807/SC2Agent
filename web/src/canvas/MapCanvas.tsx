/**
 * 地图渲染（F2/F11）—— 分层 Canvas2D。
 *
 * 三条纪律：
 * 1. **零几何复算**：footprint 直接用帧里的 `tl/br`，槽位直接用 `tl/br`，
 *    ADR-0027 的换算在后端做完了（红线 C2）；
 * 2. **位置可插值、进度绝不插值**（决策 U5）：单位坐标在两个真实采样点之间 lerp，
 *    build_progress / 计数一律用当帧原值；
 * 3. 帧里没有的层置灰并说明原因，不猜、不补默认值（红线 C7）。
 *
 * F11 地图视觉语言：
 * - 地形 = 台地（slate 阶梯）+ 悬崖硬描边 + 斜坡暖色抖动，一张位图烤一次（U15 显示层）；
 * - 建筑 = 矩形 + footprint 内标签（short_name_zh）+ 关键数（B12 的 base_tag join / producing）；
 * - 单位 = 低缩放聚类 chip（canvas/cluster，U18 仅显示层）、高缩放个体；
 * - 槽位默认四角刻度，矿点默认关（「矿区细节」）—— 噪声让位给信息（根因 J）。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { CatalogStatic, EconomyFrame, MapStatic, WorldFrame, ProductionFrame } from "../contract";
import { bakeGrid, bakeTerrain, decodeGrid, regionColor, type Palette } from "./grid";
import { clusterUnits } from "./cluster";
import { ALPHA_BUDGET, COLOR, LOD, SHAPE, fontCss, ownerColor, slotColor } from "./theme";
import { T } from "../shell/tokens";
import {
  fitViewport, resizeViewport, screenToWorld, worldToScreen, zoomAt, type Viewport,
} from "./view";
import type { LayerState } from "./layers";

export interface Selection {
  kind: "unit";
  tag: number;
}

export function MapCanvas(props: {
  map: MapStatic;
  world: WorldFrame | null;
  production: ProductionFrame | null;
  catalog: CatalogStatic | null;
  economy: EconomyFrame | null;
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

  // 视口：**只在换图 / 首次尺寸就绪时 fit**；resize 保住 scale 与屏幕中心（根因 D / 红线 G2）。
  // 之前这里 resize 也 fit，所以改窗口大小、收起对话栏都会把缩放平移丢掉。
  const mw = props.map.size[0];
  const mh = props.map.size[1];
  const mapKey = props.map.map_name + "|" + mw + "x" + mh + "|" + props.map.spawn;
  const fitted = useRef<string | null>(null);
  useEffect(() => {
    const needFit = fitted.current !== mapKey;   // 在 effect 体里判，updater 保持纯函数
    fitted.current = mapKey;
    setVp((old) => {
      if (needFit || old === null) return fitViewport(size.w, size.h, mw, mh);
      if (old.cw === size.w && old.ch === size.h) return old;
      return resizeViewport(old, size.w, size.h);
    });
  }, [size.w, size.h, mapKey, mw, mh]);

  // 静态位图：地形一张烤一次（F11b；缺 height 时为 null → 纯色底降级，不伪造）
  const terrainImage = useMemo(() => {
    const t = props.map.terrain;
    if (!t?.height) return null;
    return bakeTerrain(decodeGrid(t.height), t.pathable ? decodeGrid(t.pathable) : null);
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
      return id ? regionColor(id, Math.round(ALPHA_BUDGET.regions * 255)) : null;
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

  // 滚轮缩放走 **native 监听 + {passive:false}**（根因 A / 红线 G3）。
  // React 18 把 wheel 注册在 root container 上且强制 passive，所以写在 onWheel 里的
  // preventDefault 是空操作 —— 滚轮会同时缩放**和**滚动祖先容器，页面跟着上下动。
  // 用函数式 setVp 读旧视口，listener 因此只挂一次，不随 vp 变化重挂。
  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const handleWheel = (e: WheelEvent): void => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      const sx = e.clientX - r.left;
      const sy = e.clientY - r.top;
      const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
      setVp((old) => (old ? zoomAt(old, sx, sy, factor) : old));
    };
    el.addEventListener("wheel", handleWheel, { passive: false });
    return () => el.removeEventListener("wheel", handleWheel);
  }, []);

  // 绘制循环读**最新** props 与烤好的位图，但不因它们变化重建 rAF（根因 C）。
  // 原来依赖里有 props（每次渲染都是新对象）→ 每渲染拆一次、建一次 rAF 循环。
  const live = useRef({ props, baked: { regionImage, gridImages, terrainImage } });
  useEffect(() => {
    live.current = { props, baked: { regionImage, gridImages, terrainImage } };
  });

  // 绘制循环
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !vp) return;
    let raf = 0;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const draw = (): void => {
      raf = requestAnimationFrame(draw);
      const { props: p, baked } = live.current;
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      if (canvas.width !== Math.floor(vp.cw * dpr)) canvas.width = Math.floor(vp.cw * dpr);
      if (canvas.height !== Math.floor(vp.ch * dpr)) canvas.height = Math.floor(vp.ch * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, vp.cw, vp.ch);
      paint(ctx, vp, p, baked, {
        prevWorld: prev.current?.world ?? null,
        alpha: lerpAlpha(prev.current, cur.current, p.smooth),
      });
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [vp]);

  return (
    <div ref={hostRef} className="relative h-full w-full">
      <canvas
        ref={canvasRef}
        style={{ width: vp?.cw ?? size.w, height: vp?.ch ?? size.h }}
        className="block cursor-crosshair rounded bg-[#0d1117]"
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
      <div className={"pointer-events-none absolute bottom-1 right-2 text-faint " + T.note}>
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
  terrainImage: ImageData | null;
}

interface Motion {
  /** 上一帧（只用它的坐标做插值；其它字段一律取当帧） */
  prevWorld: WorldFrame | null;
  /** 0..1，1 = 完全用当帧坐标 */
  alpha: number;
}

/** stable_id → 短名（catalog 是 zh 文案唯一真相源，红线 C4/B13）；unknown/* 无条目 */
function shortNameMap(catalog: CatalogStatic | null): Map<string, string> {
  const m = new Map<string, string>();
  for (const e of catalog?.entries ?? []) m.set(e.stable_id, e.short_name_zh);
  return m;
}

/** 挂件字段（"reactor"/"techlab"）→ catalog stable_id，短名仍来自后端（C4） */
const ADDON_STABLE: Record<string, string> = {
  reactor: "terran/reactor",
  techlab: "terran/techlab",
};

function paint(
  ctx: CanvasRenderingContext2D,
  vp: Viewport,
  props: Parameters<typeof MapCanvas>[0],
  baked: Baked,
  motion: Motion,
): void {
  const { map, world, production, economy } = props;
  const alpha = motion.alpha;
  const names = shortNameMap(props.catalog);

  // 底：terrain 为 null 时用纯色 —— 后端 B4 之前不伪造地形（不静默）
  ctx.fillStyle = COLOR.void;
  const [x0, y0] = worldToScreen(vp, 0, map.size[1]);
  ctx.fillRect(x0, y0, map.size[0] * vp.scale, map.size[1] * vp.scale);
  if (layersOn(props, "terrain") && baked.terrainImage) {
    drawImage(ctx, vp, baked.terrainImage, map);
  }

  // 格点参考线：建筑/槽位坐标对齐用（用户反馈：没格点对不齐）；LOD 防小缩放糊成灰
  if (layersOn(props, "grid")) {
    drawGridLines(ctx, vp, map);
  }

  if (layersOn(props, "regions") && baked.regionImage) drawImage(ctx, vp, baked.regionImage, map);
  if (layersOn(props, "creep") && baked.gridImages.creep) drawImage(ctx, vp, baked.gridImages.creep, map);
  if (layersOn(props, "visibility") && baked.gridImages.visibility) {
    drawImage(ctx, vp, baked.gridImages.visibility, map);
  }

  // 槽位：默认四角小刻度（低权）；「有在途建造指向它」或摆放调试开时才画实线虚线框（根因 J / F11e）
  if (layersOn(props, "slots")) {
    const active = new Set<string>();
    if (production) {
      for (const f of production.in_flight) {
        for (const name of f.attempted_slots) active.add(name);
      }
    }
    const debug = layersOn(props, "placement");
    for (const s of map.build_slots) {
      const [sx, sy] = worldToScreen(vp, s.tl[0], s.br[1] + 1);
      const side = s.size * vp.scale;
      const color = slotColor(s.kind);
      if (active.has(s.name) || debug) {
        ctx.strokeStyle = color;
        ctx.globalAlpha = ALPHA_BUDGET.slotsActive;
        ctx.lineWidth = 1;
        ctx.setLineDash(SHAPE.slot.dash);
        ctx.strokeRect(sx, sy, side, side);
        ctx.setLineDash([]);
        ctx.globalAlpha = 1;
      } else {
        ctx.strokeStyle = color;
        ctx.globalAlpha = ALPHA_BUDGET.slotsIdle;
        ctx.lineWidth = 1;
        cornerTicks(ctx, sx, sy, side, SHAPE.slot.cornerTick);
        ctx.globalAlpha = 1;
      }
    }
  }

  if (layersOn(props, "resources") && world) {
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
        ctx.fillStyle = COLOR.text;
        ctx.font = fontCss("note");
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
    const posOf = (u: WorldFrame["units"][number]): [number, number] => {
      const from = prevPos.get(u.tag);
      if (!from || alpha >= 1) return u.pos;
      return [
        from[0] + (u.pos[0] - from[0]) * alpha,
        from[1] + (u.pos[1] - from[1]) * alpha,
      ];
    };

    if (layersOn(props, "buildings")) {
      for (const u of world.units) {
        if (!u.footprint) continue;
        const [sx, sy] = worldToScreen(vp, u.footprint.tl[0], u.footprint.br[1] + 1);
        const w = (u.footprint.br[0] - u.footprint.tl[0] + 1) * vp.scale;
        const h = (u.footprint.br[1] - u.footprint.tl[1] + 1) * vp.scale;
        const done = u.build_progress >= 1;
        ctx.fillStyle = done
          ? rgba("#34d399", ALPHA_BUDGET.buildingFill)
          : rgba(COLOR.warn, ALPHA_BUDGET.buildingFill);
        ctx.fillRect(sx, sy, w, h);
        ctx.strokeStyle = ownerColor(u.owner);
        ctx.lineWidth = done ? 1.2 : 1;
        ctx.strokeRect(sx, sy, w, h);
        if (!done) {
          // 进度条：**用当帧原值，绝不插值**（决策 U5 / ADR-0017 禁止伪造进度动画）
          ctx.fillStyle = COLOR.warn;
          ctx.fillRect(sx, sy + h - 2, w * u.build_progress, 2);
        }
        // F11c：footprint 内标签（LOD 阶梯防糊成一团）
        drawBuildingLabel(ctx, vp, u, { sx, sy, w, h }, names, economy);
      }
    }

    if (layersOn(props, "units")) {
      // F11d：低缩放画聚类 chip（U18：仅显示层；选中/命中仍按个体）
      if (vp.scale < LOD.unitChip) {
        const items = world.units
          .filter((u) => !u.footprint)
          .map((u) => ({ tag: u.tag, owner: u.owner, stable_id: u.stable_id,
                         pos: posOf(u), group_id: u.group_id }));
        for (const c of clusterUnits(items, 3.5)) {
          drawUnitChip(ctx, vp, c, names);
        }
      } else {
        for (const u of world.units) {
          if (u.footprint) continue;
          const [px, py] = posOf(u);
          const [sx, sy] = worldToScreen(vp, px, py);
          const r = Math.max(1.5, vp.scale * 0.3);
          ctx.beginPath();
          ctx.arc(sx, sy, r, 0, Math.PI * 2);
          ctx.fillStyle = ownerColor(u.owner);
          ctx.fill();
          if (layersOn(props, "groups") && u.group_id) {
            ctx.strokeStyle = "#fde68a";
            ctx.lineWidth = 1;
            ctx.stroke();
          }
          if (u.form) {                       // 形态变体（如架起的坦克）画个方框区分
            ctx.strokeStyle = "#fca5a5";
            ctx.strokeRect(sx - r - 1, sy - r - 1, (r + 1) * 2, (r + 1) * 2);
          }
          if (layersOn(props, "orders") && u.order?.target_pos) {
            const [tx, ty] = worldToScreen(vp, u.order.target_pos[0], u.order.target_pos[1]);
            ctx.strokeStyle = rgba("#94a3b8", ALPHA_BUDGET.orderLine);
            ctx.setLineDash(SHAPE.order.dash);
            ctx.beginPath();
            ctx.moveTo(sx, sy);
            ctx.lineTo(tx, ty);
            ctx.stroke();
            ctx.setLineDash([]);
          }
        }
      }
    }

    if (layersOn(props, "clusters") && world.enemy_clusters) {
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
  if (layersOn(props, "placement") && production) {
    for (const f of production.in_flight) {
      if (f.expect_pos) {
        const [sx, sy] = worldToScreen(vp, f.expect_pos[0], f.expect_pos[1]);
        ctx.strokeStyle = "#f472b6";
        ctx.beginPath();
        ctx.arc(sx, sy, f.radius * vp.scale, 0, Math.PI * 2);
        ctx.stroke();
        ctx.fillStyle = "#f472b6";
        ctx.font = fontCss("note");
        ctx.fillText(f.stable_id + " " + f.frames_waited + "f", sx + 4, sy - 4);
      }
      // 已试槽位画在摆放调试层里（不受「建造槽位」层开关影响：调试时必须看见）
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

  // 选中高亮（命中/选中始终按个体 —— chip 只是显示，U18）
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

function layersOn(props: Parameters<typeof MapCanvas>[0], key: keyof LayerState): boolean {
  return props.layers[key] === true;
}

function rgba(hex: string, a: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${a})`;
}

/**
 * 格点参考线：每 1 格细线、每 5 格粗线（对齐建筑坐标的主参考）。
 * 纯显示层；缩放 < LOD.gridMinor 时细线太密，只保留粗线。
 * 线色低 alpha（不抢单位/建筑的对比度）；+0.5 对齐像素中心，1px 线不模糊。
 */
function drawGridLines(ctx: CanvasRenderingContext2D, vp: Viewport, map: MapStatic): void {
  const minor = vp.scale >= LOD.gridMinor;
  const step = minor ? 1 : LOD.gridMajorStep;
  const [x0, y0] = worldToScreen(vp, 0, map.size[1]);   // 地图左上角
  const w = map.size[0] * vp.scale;
  const h = map.size[1] * vp.scale;
  ctx.lineWidth = 1;
  for (let gx = 0; gx <= map.size[0]; gx += step) {
    const major = gx % LOD.gridMajorStep === 0;
    ctx.strokeStyle = `rgba(148,163,184,${major ? 0.16 : 0.07})`;
    const x = Math.round(x0 + gx * vp.scale) + 0.5;
    ctx.beginPath();
    ctx.moveTo(x, y0);
    ctx.lineTo(x, y0 + h);
    ctx.stroke();
  }
  for (let gy = 0; gy <= map.size[1]; gy += step) {
    const major = gy % LOD.gridMajorStep === 0;
    ctx.strokeStyle = `rgba(148,163,184,${major ? 0.16 : 0.07})`;
    const y = Math.round(y0 + (map.size[1] - gy) * vp.scale) + 0.5;
    ctx.beginPath();
    ctx.moveTo(x0, y);
    ctx.lineTo(x0 + w, y);
    ctx.stroke();
  }
}

/** 槽位降权画法：只画四个角的短刻度（不是完整虚线框） */
function cornerTicks(
  ctx: CanvasRenderingContext2D, x: number, y: number, side: number, len: number,
): void {
  ctx.beginPath();
  // 左上
  ctx.moveTo(x, y + len); ctx.lineTo(x, y); ctx.lineTo(x + len, y);
  // 右上
  ctx.moveTo(x + side - len, y); ctx.lineTo(x + side, y); ctx.lineTo(x + side, y + len);
  // 右下
  ctx.moveTo(x + side, y + side - len); ctx.lineTo(x + side, y + side); ctx.lineTo(x + side - len, y + side);
  // 左下
  ctx.moveTo(x + len, y + side); ctx.lineTo(x, y + side); ctx.lineTo(x, y + side - len);
  ctx.stroke();
}

/**
 * F11c：建筑 footprint 内标签（LOD 阶梯：两行文字 → 1 字色块字形 → 只有色块）。
 * 第二行是该类建筑唯一重要的数，全部当帧原值（进度/计数绝不插值，U5）：
 * - 指挥中心：矿 Σ/Σ 气 Σ/Σ（economy.nodes 按 base_tag===tag join，B12）；
 * - 训练建筑：训 n/slots（反应堆 ×2；producing 是当帧订单数）；
 * - 在建：百分比（进度条已画，这里给数字）。
 */
function drawBuildingLabel(
  ctx: CanvasRenderingContext2D,
  vp: Viewport,
  u: WorldFrame["units"][number],
  box: { sx: number; sy: number; w: number; h: number },
  names: Map<string, string>,
  economy: EconomyFrame | null,
): void {
  const short = names.get(u.stable_id) ?? "未知";
  if (vp.scale < LOD.buildingLabelGlyph) return;      // 太小：只有色块（上面已画）

  const cx = box.sx + box.w / 2;
  const cy = box.sy + box.h / 2;
  if (vp.scale < LOD.buildingLabelFull) {
    // 字形级：1 字 + 状态色（放不下两行也要能认出这是什么）
    ctx.font = fontCss("metric", 600);
    ctx.fillStyle = COLOR.text;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(short.slice(0, 1), cx, cy);
    ctx.textAlign = "left";
    ctx.textBaseline = "alphabetic";
    return;
  }

  const line2 = buildingKeyLine(u, economy);
  ctx.font = fontCss("label", 600);
  if (ctx.measureText(short).width > box.w - 2) {
    // 宽度不够放全名：退到字形级
    ctx.font = fontCss("metric", 600);
    ctx.fillStyle = COLOR.text;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(short.slice(0, 1), cx, cy);
    ctx.textAlign = "left";
    ctx.textBaseline = "alphabetic";
    return;
  }
  ctx.fillStyle = COLOR.text;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  if (line2 !== null && box.h >= 26) {
    ctx.fillText(short, cx, cy - 7);
    ctx.font = fontCss("note");
    ctx.fillStyle = u.build_progress < 1 ? COLOR.warn : "rgba(229,231,235,0.85)";
    ctx.fillText(line2, cx, cy + 7);
  } else {
    ctx.fillText(short, cx, cy);
  }
  // 挂件角标（挂件不单独画，作母建筑角标）
  if (u.addon) {
    const addonShort = names.get(ADDON_STABLE[u.addon] ?? "") ?? u.addon;
    ctx.font = fontCss("note");
    ctx.fillStyle = slotColor("addon");
    ctx.textAlign = "right";
    ctx.fillText(addonShort.slice(0, 1), box.sx + box.w - 2, box.sy + box.h - 3);
  }
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
}

/** 该建筑「唯一重要的数」（当帧原值）；没有可给的数 → null（不编造） */
function buildingKeyLine(
  u: WorldFrame["units"][number],
  economy: EconomyFrame | null,
): string | null {
  if (u.build_progress < 1) {
    return `${Math.round(u.build_progress * 100)}%`;
  }
  if (u.stable_id === "terran/commandcenter") {
    // B12：economy.nodes 按 base_tag join（前端零空间匹配）
    let mW = 0; let mC = 0; let gW = 0; let gC = 0; let any = false;
    if (economy) {
      for (const n of economy.nodes) {
        if (n.base_tag !== u.tag) continue;
        any = true;
        if (n.kind === "gas") { gW += n.workers; gC += n.capacity; }
        else { mW += n.workers; mC += n.capacity; }
      }
    }
    return any ? `矿 ${mW}/${mC} 气 ${gW}/${gC}` : null;
  }
  if (u.stable_id === "terran/barracks" || u.stable_id === "terran/factory") {
    const slots = u.addon === "reactor" ? 2 : 1;
    return `训 ${u.producing?.length ?? 0}/${slots}`;
  }
  return null;
}

/** F11d：聚类 chip —— 圆角矩形 + owner 色边 + 短名字形 + 数量；同组带组标签 */
function drawUnitChip(
  ctx: CanvasRenderingContext2D,
  vp: Viewport,
  c: ReturnType<typeof clusterUnits>[number],
  names: Map<string, string>,
): void {
  const short = names.get(c.stable_id) ?? "未知";
  const text = `${short}${c.count > 1 ? ` ${c.count}` : ""}`;
  ctx.font = fontCss("label", 600);
  const tw = ctx.measureText(text).width;
  const padX = SHAPE.unit.chipPadX;
  const w = tw + padX * 2;
  const h = FONT_LINE_LABEL;
  const [sx, sy] = worldToScreen(vp, c.center[0], c.center[1]);
  const x = sx - w / 2;
  const y = sy - h / 2;

  ctx.fillStyle = "rgba(13,17,23,0.82)";
  roundRect(ctx, x, y, w, h, SHAPE.unit.chipRadius);
  ctx.fill();
  ctx.strokeStyle = ownerColor(c.owner);
  ctx.lineWidth = 1.25;
  roundRect(ctx, x, y, w, h, SHAPE.unit.chipRadius);
  ctx.stroke();
  ctx.fillStyle = COLOR.text;
  ctx.textBaseline = "middle";
  ctx.fillText(text, x + padX, y + h / 2 + 0.5);
  if (c.group_id) {
    ctx.font = fontCss("note");
    ctx.fillStyle = "#fde68a";
    ctx.fillText(c.group_id, x + w + 3, y + h / 2 + 0.5);
    ctx.font = fontCss("label", 600);
  }
  ctx.textBaseline = "alphabetic";
}

/** label 档 chip 的高度（与 FONT_PX.label + 上下 pad 对齐；常量避免每帧量测） */
const FONT_LINE_LABEL = 18;

function roundRect(
  ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number,
): void {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
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
