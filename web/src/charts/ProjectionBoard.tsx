/**
 * 投影板（I5/F15/F17）—— 曲线 + 泳道共享**同一条时间轴**，一张图上下两带。
 *
 * F17 交互（用户三轮反馈打磨）：
 * - **历史累积**：投影帧只含 [based_on, +horizon]，拖顶层时间轴后左半视窗会空 ——
 *   显示层把走过的每秒累积（accumulateInto），数据像向左流走一样保留；
 * - **左键拖图 = 拖时间轴**：按住图横向拖，时间轴跟着走（可 seek 源才启用）；
 * - **hover 线**：root 级统一几何换算（frac），拖时间轴后线不再滞后错位；
 *   uPlot 自带 legend 隐藏，hover 读数进 footer —— 线也就不会越过时间轴；
 * - **泳道**：bar 自带 zh 名（圆角矩形内文字），全局行打包（跨类型并行），
 *   固定 7 行高度、超出滚动；
 * - **检查面板常驻**：默认显示「现在」，点击任意时刻切换，× 回到现在（面板不移位）。
 *
 * 红线 C7/G5：泳道/在产明细/历史累积都是显示层派生（gantt-data.ts，同一套配对），
 * 不算新数值、不入决策路径。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { CatalogStatic, ProjectionFrame } from "../contract";
import { ProjectionChart, PROJECTION_GUTTER } from "./ProjectionChart";
import {
  accumulateInto, activeAt, anchorRange, centerRange, packBars, toStalls, zoomSpan,
  type TimeDomain,
} from "./gantt-data";
import { fmtMMSS } from "./projection-data";
import { T } from "../shell/tokens";
import { useFrames } from "../store/frames";

/** 泳道固定可见行数（用户拍板：7 行足够；超出滚动） */
const LANES_VISIBLE = 7;
const LANE_ROW_PX = 20;
/** 左键拖图判定的位移容差（px）：超过它才算拖时间轴，不是点击 */
const DRAG_SLOP = 4;

const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v));

/** 点击 → 最近采样点（逐秒、从首个点起；投影没有更细粒度——已拍板逐秒+点击检查） */
function nearestPoint(
  frame: ProjectionFrame, t: number,
): ProjectionFrame["points"][number] | null {
  const pts = frame.points;
  if (pts.length === 0) return null;
  const first = pts[0]!.t;
  const idx = clamp(Math.round(t - first), 0, pts.length - 1);
  return pts[idx]!;
}

export function ProjectionBoard(props: {
  frame: ProjectionFrame;
  /** catalog zh 名与 produced_by join 都从这里来（C4：前端不自建字典） */
  catalog: CatalogStatic | null;
  zhOf: (id: string | null) => string;
  height?: number;
  /** 初始视窗（缺省 = 围绕 based_on 居中）。规划试算 from=0 起轴，不出一半负时间 */
  initialDomain?: TimeDomain;
}) {
  const { frame } = props;

  // ---- 历史累积（F17）：拖时间轴后左侧内容保留 ----
  const histRef = useRef<{
    points: Map<number, ProjectionFrame["points"][number]>;
    events: Map<string, ProjectionFrame["events"][number]>;
  }>({ points: new Map(), events: new Map() });
  const lastBasedRef = useRef(Number.NEGATIVE_INFINITY);
  const [merged, setMerged] = useState<ProjectionFrame>(frame);
  useEffect(() => {
    const h = histRef.current;
    if (frame.based_on_game_time < lastBasedRef.current - 5) {
      // 帧源大幅回退（向后拖时间轴 / 换源）→ 历史与新帧不再连续，重新累积
      h.points.clear();
      h.events.clear();
    }
    lastBasedRef.current = frame.based_on_game_time;
    setMerged(accumulateInto(h, frame));
  }, [frame]);

  const packed = useMemo(() => packBars(merged), [merged]);
  const stalls = useMemo(() => toStalls(merged), [merged]);

  // ---- 视窗：宽度滚轮调、中心跟顶层时间轴（live 跟随帧的 based_on）----
  const position = useFrames((s) => s.position);
  const seekable = useFrames((s) => s.caps.seek);
  const center = position > 0 ? position : merged.based_on_game_time;
  const [range, setRange] = useState<TimeDomain>(() =>
    props.initialDomain ?? centerRange(center, zoomSpan(Math.max(1, frame.horizon), 1)));
  const rangeRef = useRef(range);
  rangeRef.current = range;
  const positionRef = useRef(position);
  positionRef.current = position;
  // 中心变化（拖顶层时间轴 / live 推进）→ 保持宽度只移中心
  useEffect(() => {
    setRange((r) => centerRange(center, r.to - r.from));
  }, [center]);
  const span = Math.max(1e-9, range.to - range.from);
  const pct = (t: number): number => clamp(((t - range.from) / span) * 100, 0, 100);

  // ---- hover（root 级统一几何换算）/ 点击检查 / 拖图拖时间轴 ----
  const [hoverFrac, setHoverFrac] = useState<number | null>(null);
  const [selectedT, setSelectedT] = useState<number | null>(null);
  const hoverT = hoverFrac !== null ? range.from + hoverFrac * span : null;

  const rootRef = useRef<HTMLDivElement | null>(null);
  const scrub = useRef<{ startX: number; startPos: number; active: boolean; moved: boolean } | null>(null);
  const lastScrubMoved = useRef(false);
  const seekableRef = useRef(seekable);
  seekableRef.current = seekable;

  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const fracFrom = (clientX: number): number | null => {
      const rect = el.getBoundingClientRect();
      const track = rect.width - PROJECTION_GUTTER.left - PROJECTION_GUTTER.right;
      if (track <= 0) return null;
      const f = (clientX - rect.left - PROJECTION_GUTTER.left) / track;
      return f < 0 || f > 1 ? null : f;
    };
    const onMove = (e: PointerEvent): void => {
      const s = scrub.current;
      if (s?.active) {
        const dx = e.clientX - s.startX;
        if (!s.moved && Math.abs(dx) > DRAG_SLOP) s.moved = true;
        if (s.moved && seekableRef.current) {
          // 左键按住图横拖 = 拖时间轴：向左拖（dx<0）时间前进，内容跟手
          const rect = el.getBoundingClientRect();
          const track = rect.width - PROJECTION_GUTTER.left - PROJECTION_GUTTER.right;
          if (track > 0) {
            const dt = -(dx / track) * (rangeRef.current.to - rangeRef.current.from);
            const st = useFrames.getState();
            st.seek(clamp(s.startPos + dt, st.range.from, st.range.to));
          }
        }
        return;
      }
      setHoverFrac(fracFrom(e.clientX));
    };
    const onLeave = (): void => setHoverFrac(null);
    const onDown = (e: PointerEvent): void => {
      if (e.button !== 0) return;
      if (fracFrom(e.clientX) === null) return;   // 只在绘图区才能拖
      scrub.current = {
        startX: e.clientX, startPos: positionRef.current, active: true, moved: false,
      };
    };
    const onUp = (): void => {
      lastScrubMoved.current = scrub.current?.moved ?? false;
      scrub.current = null;
    };
    const onClick = (e: MouseEvent): void => {
      if (lastScrubMoved.current) return;   // 刚拖完时间轴，不是点击
      const f = fracFrom(e.clientX);
      if (f !== null) setSelectedT(rangeRef.current.from + f * (rangeRef.current.to - rangeRef.current.from));
    };
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerleave", onLeave);
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("click", onClick);
    return () => {
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerleave", onLeave);
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("click", onClick);
    };
  }, []);

  // 滚轮缩放：围绕光标，native + passive:false（G3 —— React onWheel 的 preventDefault 是空操作）
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return;
    const handleWheel = (e: WheelEvent): void => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const track = rect.width - PROJECTION_GUTTER.left - PROJECTION_GUTTER.right;
      if (track <= 0) return;
      const frac = clamp(
        (e.clientX - rect.left - PROJECTION_GUTTER.left) / track, 0, 1);
      const cur = rangeRef.current;
      const anchorT = cur.from + frac * (cur.to - cur.from);
      const next = zoomSpan(cur.to - cur.from, e.deltaY > 0 ? 1.25 : 0.8);
      setRange(anchorRange(anchorT, frac, next));
    };
    el.addEventListener("wheel", handleWheel, { passive: false });
    return () => el.removeEventListener("wheel", handleWheel);
  }, []);

  const stallsInView = stalls.filter((s) => s.t >= range.from && s.t <= range.to);

  // ---- 检查面板（常驻：默认「现在」，点击切换） ----
  const inspectT = selectedT ?? merged.based_on_game_time;
  const inspectPt = nearestPoint(merged, inspectT);
  const activeBars = useMemo(() => activeAt(merged, inspectT), [merged, inspectT]);
  const producerOf = useMemo(() => {
    const m = new Map<string, string>();
    for (const e of props.catalog?.entries ?? []) {
      if (e.produced_by) m.set(e.stable_id, e.produced_by);
    }
    return m;
  }, [props.catalog]);

  const hoverPt = hoverT !== null ? nearestPoint(merged, hoverT) : null;
  const laneBodyH = Math.max(1, packed.rows) * LANE_ROW_PX;

  return (
    <div className="flex flex-col gap-3 xl:flex-row">
      {/* 左：图板（泳道在上、曲线在下） */}
      <div className="min-w-0 flex-1">
        <div ref={rootRef} className="relative cursor-crosshair">
          {/* —— 上带：泳道（全局行打包 + bar 内名字 + 固定 7 行滚动） —— */}
          {packed.bars.length === 0 && stalls.length === 0 ? (
            <div className={"flex items-center " + T.note + " text-ghost"}
                 style={{ height: LANE_ROW_PX }}>
              该投影没有开工/完成事件
            </div>
          ) : (
            <div
              className="relative overflow-y-auto rounded bg-neutral-900/40"
              style={{ height: Math.min(packed.rows, LANES_VISIBLE) * LANE_ROW_PX || LANE_ROW_PX }}
            >
              <div className="relative" style={{ height: laneBodyH }}>
                {packed.bars.map((b) => {
                  const l0 = pct(b.from);
                  const l1 = pct(b.to);
                  const label = props.zhOf(b.stableId);
                  return (
                    <div
                      key={b.id}
                      title={
                        `${label} ${fmtMMSS(b.from)} → ` +
                        (b.done ? fmtMMSS(b.to) : "未完（延到视野末端）")
                      }
                      className={
                        "absolute flex items-center overflow-hidden rounded px-1 "
                        + T.note + " "
                        + (b.done
                          ? "bg-emerald-700/70 text-emerald-100"
                          : "bg-amber-700/60 text-amber-100")
                      }
                      style={{
                        left: l0 + "%",
                        width: Math.max(0.6, l1 - l0) + "%",
                        top: b.row * LANE_ROW_PX + 1,
                        height: LANE_ROW_PX - 3,
                      }}
                    >
                      <span className="truncate">{label}</span>
                    </div>
                  );
                })}

                {/* 卡点竖线：卡点是时间点，对全部行生效（曲线里另有红色虚线 + 原因文字） */}
                <div
                  className="pointer-events-none absolute inset-y-0"
                  style={{ left: PROJECTION_GUTTER.left, right: PROJECTION_GUTTER.right }}
                >
                  {stalls.map((s, i) => (
                    <div
                      key={i}
                      className="absolute inset-y-0 w-0.5 bg-red-500/70"
                      style={{ left: pct(s.t) + "%" }}
                      title={`${fmtMMSS(s.t)} 卡点：${props.zhOf(s.stableId)} ${s.reason ?? ""}`}
                    />
                  ))}
                </div>
              </div>
            </div>
          )}
          {packed.rows > LANES_VISIBLE && (
            <div className={"mt-0.5 text-right " + T.note + " text-ghost"}>
              {packed.rows} 行（区内滚动）
            </div>
          )}

          {/* —— 贯穿两带的游标线（hover/选中）：只到时间轴为止（legend 已隐藏） —— */}
          {hoverFrac !== null && (
            <div
              className="pointer-events-none absolute inset-y-0 z-10"
              style={{ left: PROJECTION_GUTTER.left, right: PROJECTION_GUTTER.right }}
            >
              <div className="absolute inset-y-0 w-px bg-slate-400/70" style={{ left: hoverFrac * 100 + "%" }} />
            </div>
          )}
          {selectedT !== null && (
            <div
              className="pointer-events-none absolute inset-y-0 z-10"
              style={{ left: PROJECTION_GUTTER.left, right: PROJECTION_GUTTER.right }}
            >
              <div
                className="absolute inset-y-0 w-px bg-sky-400"
                style={{ left: pct(selectedT) + "%" }}
                title={`选中 ${fmtMMSS(selectedT)}（右侧面板显示该秒状态）`}
              />
            </div>
          )}

          {/* —— 下带：曲线（x 轴在整板底部；legend 隐藏，hover 读数在 footer） —— */}
          <ProjectionChart
            frame={merged}
            height={props.height ?? 190}
            domain={range}
            hideFooter
            hideLegend
          />
        </div>

        {/* 脚注：一行收底 + hover 读数（接管被隐藏的 uPlot legend） */}
        <div className={"mt-1 flex flex-wrap gap-x-4 gap-y-1 " + T.note + " text-faint"}>
          <span>
            视窗 {fmtMMSS(range.from)} → {fmtMMSS(range.to)}（宽 {Math.round(span)}s）
          </span>
          <span>
            滚轮调宽度
            {seekable ? " · 按住拖 = 拖时间轴" : ""}
            {" "}· 中心跟随顶层时间轴 · 点击查看该秒状态
          </span>
          <span>
            采样 {merged.points.length} 点/秒 · 基于 seq {merged.based_on_seq} · 来源{" "}
            {merged.source.kind === "live_queue"
              ? "当前队列 " + merged.source.queue_name
              : "草稿 " + merged.source.plan_id}
          </span>
          {hoverPt && (
            <span className="text-neutral-300">
              {fmtMMSS(hoverPt.t)} · 矿 {Math.round(hoverPt.minerals)} · 气 {Math.round(hoverPt.gas)}
              {" "}· 供给 {hoverPt.supply_used}/{hoverPt.supply_cap}
            </span>
          )}
          {stallsInView.length > 0 && (
            <span className="text-red-400">
              卡点 {stallsInView.length} 处（悬停红竖线看原因）
            </span>
          )}
        </div>
      </div>

      {/* 右：检查面板（常驻固定；默认显示「现在」，点击任意时刻切换） */}
      {inspectPt && (
        <div className={"w-72 shrink-0 space-y-2 " + T.note}>
          <div className="flex items-baseline justify-between">
            <b className="text-neutral-200">
              {fmtMMSS(inspectPt.t)}
              {selectedT === null && (
                <span className="ml-1 text-note text-faint">（现在）</span>
              )}
            </b>
            {selectedT !== null && (
              <button
                className="text-faint hover:text-neutral-300"
                title="回到现在"
                onClick={() => setSelectedT(null)}
              >× 回到现在</button>
            )}
          </div>

          <div className="text-dim">
            矿 {Math.round(inspectPt.minerals)} · 气 {Math.round(inspectPt.gas)} · 供给{" "}
            {inspectPt.supply_used}/{inspectPt.supply_cap}
          </div>
          <div className="text-dim">
            工人 矿 {inspectPt.mineral_workers} · 气 {inspectPt.gas_workers} · 在途{" "}
            {inspectPt.in_flight_count}
          </div>

          <div>
            <div className="text-faint">建筑</div>
            {Object.keys(inspectPt.buildings).length === 0 ? (
              <span className="text-ghost">无</span>
            ) : (
              Object.entries(inspectPt.buildings)
                .map(([id, n]) => `${props.zhOf(id)} ×${n}`)
                .join("、")
            )}
          </div>

          <div>
            <div className="text-faint">战斗单位</div>
            {Object.keys(inspectPt.units).length === 0 ? (
              <span className="text-ghost">无</span>
            ) : (
              Object.entries(inspectPt.units)
                .map(([id, n]) => `${props.zhOf(id)} ×${n}`)
                .join("、")
            )}
          </div>

          <div>
            <div className="text-faint">在产 / 在建</div>
            {activeBars.length === 0 ? (
              <span className="text-ghost">该秒没有在产项</span>
            ) : (
              <ul className="space-y-0.5">
                {activeBars.map((b, i) => {
                  const producer = b.stableId ? producerOf.get(b.stableId) : undefined;
                  return (
                    <li key={i} className="text-dim">
                      {props.zhOf(b.stableId)}
                      {producer && (
                        <span className="text-faint">（{props.zhOf(producer)}）</span>
                      )}
                      <span className={b.done ? " text-faint" : " text-amber-400"}>
                        {" "}剩 {Math.max(0, Math.round(b.to - inspectPt.t))}s
                      </span>
                      {!b.done && <span className="text-amber-400">（未闭合）</span>}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          <div>
            <div className="text-faint">产线（建筑 × 在产）</div>
            <ul className="space-y-0.5">
              {[...producerOf.values()]
                .filter((p, i, arr) => arr.indexOf(p) === i)
                .map((p) => ({
                  producer: p,
                  buildings: inspectPt.buildings[p] ?? 0,
                  active: activeBars.filter(
                    (b) => b.stableId !== null && producerOf.get(b.stableId) === p,
                  ).length,
                }))
                .filter((s) => s.buildings > 0 || s.active > 0)
                .map((s) => (
                  <li key={s.producer} className="text-dim">
                    {props.zhOf(s.producer)} ×{s.buildings}
                    <span className={s.active > 0 ? " text-emerald-400" : " text-faint"}>
                      {" "}· 在产 {s.active}
                    </span>
                  </li>
                ))}
            </ul>
          </div>

          {stallsInView.length > 0 && (
            <div>
              <div className="text-faint">视窗内卡点</div>
              <ul className="space-y-0.5 text-red-400">
                {stallsInView.map((s, i) => (
                  <li key={i}>
                    {fmtMMSS(s.t)} {props.zhOf(s.stableId)} {s.reason ?? ""}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="text-ghost">
            检查的是投影采样点（逐秒）；在产/产线是显示层从事件配对的派生，不是帧字段
          </div>
        </div>
      )}
    </div>
  );
}
