/**
 * 投影板（I5/F15/F17）—— 曲线 + 泳道共享**同一条时间轴**，一张图上下两带。
 *
 * 复盘改版（2026-08-24 用户拍板，F17 历史累积退役）：
 * - **只画本帧投影 [T, T+horizon]**：旧的跨帧累积把每次重投影的 t 漂移事件全叠起来，
 *   复盘泳道爆炸（用户实测「堆了特别多的泳道」根因）—— 这一帧不该知道之前的曲线；
 * - **红截断线钉 T**（当前帧 based_on）：左侧为空，只留 LEFT_MARGIN_SECS（30s）
 *   给跨线的在产/在建**部分条**显名字（按 build_progress 反推已耗时长）；
 * - **左缘钳 max(0, T-30)**：拖不过截断线左侧 30s（原「零点钉最左」语义让位）；
 * - **左键拖图 = 平移视野**、**跟随 = 边缘触发**（重锚定时截断线回到左缘 +30s）、
 *   hover/检查面板等其余交互不变。
 *
 * 红线 C7/G5：泳道/在产明细/部分条都是显示层派生（gantt-data.ts，同一套配对），
 * 不算新数值、不入决策路径。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { CatalogStatic, ProjectionFrame } from "../contract";
import { ProjectionChart, PROJECTION_GUTTER } from "./ProjectionChart";
import {
  LEFT_MARGIN_SECS, activeAt, nowAnchoredRange, packPairs, pairEvents,
  toStalls, zoomSpan, type PairedBar, type TimeDomain,
} from "./gantt-data";
import { fmtMMSS } from "./projection-data";
import { T } from "../shell/tokens";
import { useFrames } from "../store/frames";

/** 泳道固定可见行数（用户拍板：7 行足够；超出滚动） */
const LANES_VISIBLE = 7;
const LANE_ROW_PX = 20;
/** 左键拖图判定的位移容差（px）：超过它才算拖动，不是点击 */
const DRAG_SLOP = 4;
/** 平移增益：1.0 = 内容与鼠标 1:1 跟手（二十四轮用户拍板「不跟手」——
 *  0.5 的减半让视图永远追不上鼠标）；跨度大时的速度上限交给 PAN_SPAN_CAP */
const PAN_GAIN = 1.0;
/** 平移速度封顶（秒）：可视跨度大于它时整幅拖满也只有 PAN_SPAN_CAP（半幅 ≈8 分钟，
 *  二十二轮用户拍板的灵敏度上限）；小跨度视图不受影响（仍 1:1 × 增益跟手） */
const PAN_SPAN_CAP = 960;
/** bar 文字的最小像素宽（二十六轮：密事件下窄条的字符糊成一团，宁可留白靠 title） */
const BAR_LABEL_MIN_PX = 30;

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
}) {
  const { frame } = props;

  // ---- 复盘改版（2026-08-24）：F17 累积退役 —— 板上只有本帧投影 [T, T+horizon] ----
  const T0 = frame.based_on_game_time;

  // 在建/在训部分条（截断线左延伸，G2+G3）：
  // - 在建建筑：0<build_progress<1 + 目录 build_time 反推已耗/剩余；
  // - 在训单位：production 帧的 training 账本（SC2 订单不带进度 —— rev13 收窄 None，
  //   开始时刻由 runtime 在 emit 时自己记，rev 17）。
  const world = useFrames((s) => s.world);
  const production = useFrames((s) => s.production);
  const partials = useMemo(() => {
    const bt = new Map((props.catalog?.entries ?? [])
      .map((e) => [e.stable_id, e.build_time] as const));
    const out: PairedBar[] = [];
    if (world && props.catalog) {
      for (const u of world.units) {
        if (u.owner !== "self" || u.build_progress <= 0 || u.build_progress >= 1) continue;
        const total = bt.get(u.stable_id);
        if (!total) continue;
        out.push({ stableId: u.stable_id, from: T0 - total * u.build_progress,
                   to: T0 + total * (1 - u.build_progress), done: false });
      }
    }
    for (const t of production?.training ?? []) {
      const total = bt.get(t.stable_id);
      if (!total) continue;
      out.push({ stableId: t.stable_id, from: t.started_at,
                 to: t.started_at + total, done: false });
    }
    return out;
  }, [world, production, props.catalog, T0]);

  const packed = useMemo(() => {
    const pairs = pairEvents(frame);
    for (const p of partials) {
      // 投影已含这条跨线在途项 → 只把它的左端延出去；没有才单开一条
      const hit = pairs.find((m) => m.stableId === p.stableId && m.from <= T0 && m.to > T0);
      if (hit) hit.from = Math.min(hit.from, p.from);
      else pairs.push(p);
    }
    return packPairs(pairs);
  }, [frame, partials, T0]);
  const stalls = useMemo(() => toStalls(frame), [frame]);

  // ---- 视窗（复盘改版）：截断线钉 T、左缘 max(0, T-30)、右缘不出数据末端空白 ----
  const position = useFrames((s) => s.position);
  const dataEnd = Math.max(
    frame.points.length ? frame.points[frame.points.length - 1]!.t : 0,
    T0,
  );
  const [range, setRange] = useState<TimeDomain>(() =>
    nowAnchoredRange(T0, zoomSpan(Math.max(1, frame.horizon), 1), Math.max(1, dataEnd)));
  const rangeRef = useRef(range);
  rangeRef.current = range;
  // dataEnd / T0 的同步镜像（pan 处理器挂在 [] 依赖的 effect 里，闭包里的值会陈旧）
  const dataEndRef = useRef(dataEnd);
  dataEndRef.current = dataEnd;
  const t0Ref = useRef(T0);
  t0Ref.current = T0;
  const panningRef = useRef(false);
  // 时间位置滑出视窗（回放 seek / live 推进到边缘）→ 重锚定：截断线回到左缘 +30s。
  // 拖动中不动（拖动就是用户在定视窗）。
  useEffect(() => {
    if (panningRef.current) return;
    setRange((r) => {
      const span = r.to - r.from;
      const margin = span * 0.08;
      if (position <= r.from + margin || position >= r.to - margin) {
        return nowAnchoredRange(position, span, dataEndRef.current);
      }
      return r;
    });
  }, [position]);
  const span = Math.max(1e-9, range.to - range.from);
  const pct = (t: number): number => clamp(((t - range.from) / span) * 100, 0, 100);

  // ---- hover（root 级统一几何换算）/ 点击检查 / 拖图拖时间轴 ----
  const [hoverFrac, setHoverFrac] = useState<number | null>(null);
  const [selectedT, setSelectedT] = useState<number | null>(null);
  const hoverT = hoverFrac !== null ? range.from + hoverFrac * span : null;

  const rootRef = useRef<HTMLDivElement | null>(null);
  // 绘图区像素宽（泳道标签按宽度隐藏的判据）：ResizeObserver 跟随容器
  const [trackW, setTrackW] = useState(0);
  useEffect(() => {
    const el = rootRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const measure = () => {
      const rect = el.getBoundingClientRect();
      setTrackW(Math.max(0, rect.width - PROJECTION_GUTTER.left - PROJECTION_GUTTER.right));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const scrub = useRef<{ startX: number; active: boolean; moved: boolean } | null>(null);
  const lastScrubMoved = useRef(false);

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
        // 手势标记还活着但左键已经不在了（在元素外/别的窗口松手时 el 收不到
        // pointerup）→ 就地收尾。不收的话陈旧的 startX 会把之后的**纯悬停**也当成
        // 拖动：dx 恒对一个老锚点取号，只要鼠标还在锚点一侧，平移方向就永远不变
        //（二十七轮用户实测的「方向锁死」）。
        if ((e.buttons & 1) === 0) {
          lastScrubMoved.current = s.moved;
          scrub.current = null;
          window.setTimeout(() => { panningRef.current = false; }, 400);
          return;
        }
        const dx = e.clientX - s.startX;
        if (!s.moved && Math.abs(dx) > DRAG_SLOP) s.moved = true;
        if (s.moved) {
          // 左键按住图横拖 = 平移视野：向左拖（dx<0）看未来，内容跟手；
          // 零点是最左边界（from 钉 0，拖不过去 —— 二十轮用户拍板）
          const rect = el.getBoundingClientRect();
          const track = rect.width - PROJECTION_GUTTER.left - PROJECTION_GUTTER.right;
          if (track > 0) {
            const r = rangeRef.current;
            const span = r.to - r.from;
            const end = dataEndRef.current;
            const dt = -(dx / track) * Math.min(span, PAN_SPAN_CAP) * PAN_GAIN;
            setRange(() => {
              // 双侧钳制（复盘改版）：from ≥ max(0, T-30)（截断线左侧只留 30s）且
              // to ≤ 数据末端（右侧不出空白——滚到头就是头）
              const left = Math.max(0, t0Ref.current - LEFT_MARGIN_SECS);
              const from = clamp(r.from + dt, left, Math.max(left, end - span));
              return { from, to: Math.max(from + 1, Math.min(from + span, Math.max(1, end))) };
            });
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
      panningRef.current = true;
      // 指针捕获：拖出元素外松手，pointerup 也回到 el（手势必然收尾，startX 不滞留）
      el.setPointerCapture?.(e.pointerId);
      scrub.current = { startX: e.clientX, active: true, moved: false };
    };
    const onUp = (): void => {
      lastScrubMoved.current = scrub.current?.moved ?? false;
      scrub.current = null;
      // 松手后短暂冻结跟随，避免最后一次 position 帧把视窗拽回去
      window.setTimeout(() => { panningRef.current = false; }, 400);
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

  const stallsInView = stalls.filter((s) => s.t >= range.from && s.t <= range.to);

  // ---- 检查面板（常驻：默认「现在」，点击切换） ----
  const inspectT = selectedT ?? T0;
  const inspectPt = nearestPoint(frame, inspectT);
  const activeBars = useMemo(() => activeAt(frame, inspectT), [frame, inspectT]);
  const producerOf = useMemo(() => {
    const m = new Map<string, string>();
    for (const e of props.catalog?.entries ?? []) {
      if (e.produced_by) m.set(e.stable_id, e.produced_by);
    }
    return m;
  }, [props.catalog]);

  const hoverPt = hoverT !== null ? nearestPoint(frame, hoverT) : null;
  const laneBodyH = Math.max(1, packed.rows) * LANE_ROW_PX;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 xl:flex-row">
      {/* 左：图板（泳道在上、曲线在下） */}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div ref={rootRef} className="relative flex min-h-0 flex-1 cursor-crosshair flex-col">
          {/* —— 上带：泳道（全局行打包 + bar 内名字 + 固定 7 行滚动） —— */}
          {packed.bars.length === 0 && stalls.length === 0 ? (
            <div className={"flex items-center " + T.note + " text-ghost"}
                 style={{ height: LANE_ROW_PX }}>
              该投影没有开工/完成事件
            </div>
          ) : (
            <div
              className="relative min-h-0 flex-1 overflow-x-hidden overflow-y-auto rounded bg-panel"
              style={{ minHeight: LANES_VISIBLE * LANE_ROW_PX }}
            >
              <div className="relative" style={{ height: laneBodyH }}>
                {/* 行间灰色细虚线（二十三轮用户拍板：纯白泳道太素）—— 只铺绘图区宽度 */}
                <div className="pointer-events-none absolute inset-y-0"
                     style={{ left: PROJECTION_GUTTER.left, right: PROJECTION_GUTTER.right }}>
                  {Array.from({ length: Math.max(0, packed.rows) }, (_, i) => (
                    <div key={i} className="absolute w-full border-t border-dashed border-l1"
                         style={{ top: (i + 1) * LANE_ROW_PX }} />
                  ))}
                </div>
                {/* bar 在 gutter 内层定位（二十三轮修复：旧版按整箱百分比定位，
                    泳道 0 点比曲线 0 点靠左 46px —— 「轴没对齐」就是这来的）。
                    只渲染与视窗相交的 bar（until_complete 后整局几百条，全渲染是 DOM 浪费）；
                    窄于 BAR_LABEL_MIN_PX 的 bar 隐藏文字（密事件下字比条还宽，读了也是糊的，
                    title 悬停仍可看全名 —— 二十六轮用户反馈「事件太密看不见字」） */}
                <div className="absolute inset-y-0"
                     style={{ left: PROJECTION_GUTTER.left, right: PROJECTION_GUTTER.right }}>
                {packed.bars
                  .filter((b) => b.to >= range.from && b.from <= range.to)
                  .map((b) => {
                  const l0 = pct(b.from);
                  const l1 = pct(b.to);
                  const label = props.zhOf(b.stableId);
                  const wPx = ((l1 - l0) / 100) * trackW;
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
                          ? "bg-[color:var(--ok-fg)] text-white"
                          : "bg-[color:var(--warn-fg)] text-white")
                      }
                      style={{
                        left: l0 + "%",
                        width: Math.max(0.6, l1 - l0) + "%",
                        top: b.row * LANE_ROW_PX + 1,
                        height: LANE_ROW_PX - 3,
                      }}
                    >
                      {wPx >= BAR_LABEL_MIN_PX && <span className="truncate">{label}</span>}
                    </div>
                  );
                })}
                </div>

                {/* 卡点竖线：卡点是时间点，对全部行生效（曲线里另有红色虚线 + 原因文字） */}
                <div
                  className="pointer-events-none absolute inset-y-0"
                  style={{ left: PROJECTION_GUTTER.left, right: PROJECTION_GUTTER.right }}
                >
                  {stallsInView.map((s, i) => (
                    <div
                      key={i}
                      className="absolute inset-y-0 w-0.5 bg-[color:var(--err-fg)]"
                      style={{ left: pct(s.t) + "%" }}
                      title={`${fmtMMSS(s.t)} 卡点：${props.zhOf(s.stableId)} ${s.reason ?? ""}`}
                    />
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* —— 贯穿两带的截断线（复盘改版）：红实线钉「现在」—— 左侧只有 30s 在产/在建余量 —— */}
          {T0 >= range.from && T0 <= range.to && (
            <div
              className="pointer-events-none absolute inset-y-0 z-10"
              style={{ left: PROJECTION_GUTTER.left, right: PROJECTION_GUTTER.right }}
            >
              <div
                className="absolute inset-y-0 w-0.5 bg-[color:var(--err-fg)]"
                style={{ left: pct(T0) + "%" }}
                title={`${fmtMMSS(T0)} 现在（截断）：这一帧只知道 T 之后的投影；左侧 30s 只画在产/在建条`}
              />
            </div>
          )}

          {/* —— 贯穿两带的游标线（hover/选中）：只到时间轴为止（legend 已隐藏） —— */}
          {hoverFrac !== null && (
            <div
              className="pointer-events-none absolute inset-y-0 z-10"
              style={{ left: PROJECTION_GUTTER.left, right: PROJECTION_GUTTER.right }}
            >
              <div className="absolute inset-y-0 w-px bg-[color:var(--text-faint)]/70" style={{ left: hoverFrac * 100 + "%" }} />
            </div>
          )}
          {selectedT !== null && (
            <div
              className="pointer-events-none absolute inset-y-0 z-10"
              style={{ left: PROJECTION_GUTTER.left, right: PROJECTION_GUTTER.right }}
            >
              <div
                className="absolute inset-y-0 w-px bg-[color:var(--accent-blue-fg)]"
                style={{ left: pct(selectedT) + "%" }}
                title={`选中 ${fmtMMSS(selectedT)}（右侧面板显示该秒状态）`}
              />
            </div>
          )}

          {/* —— 下带：曲线（x 轴在整板底部；legend 隐藏，hover 读数在 footer） —— */}
          <ProjectionChart
            frame={frame}
            height={props.height ?? 190}
            domain={range}
            hideFooter
            hideLegend
          />
        </div>

        {/* 脚注：一行收底 + hover 读数（接管被隐藏的 uPlot legend） */}
        <div className={"mt-1 flex flex-wrap gap-x-4 gap-y-1 " + T.note + " text-faint"}>
          <span>按住拖 = 平移视野 · 点击查看该秒状态</span>
          {hoverPt && (
            <span className="text-dim">
              {fmtMMSS(hoverPt.t)} · 矿 {Math.round(hoverPt.minerals)} · 气 {Math.round(hoverPt.gas)}
              {" "}· 供给 {hoverPt.supply_used}/{hoverPt.supply_cap}
            </span>
          )}
          {stallsInView.length > 0 && (
            <span className="text-[color:var(--err-fg)]">
              卡点 {stallsInView.length} 处（悬停红竖线看原因）
            </span>
          )}
        </div>
      </div>

      {/* 右：检查面板（常驻固定；默认显示「现在」，点击任意时刻切换） */}
      {inspectPt && (
        <div className={"w-72 shrink-0 self-stretch space-y-2 overflow-y-auto pr-1 " + T.note}>
          <div className="flex items-baseline justify-between">
            <b className="text-strong">
              {fmtMMSS(inspectPt.t)}
              {selectedT === null && (
                <span className="ml-1 text-note text-faint">（现在）</span>
              )}
            </b>
            {selectedT !== null && (
              <button
                className="text-faint hover:text-dim"
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
                      <span className={b.done ? " text-faint" : " text-[color:var(--warn-fg)]"}>
                        {" "}剩 {Math.max(0, Math.round(b.to - inspectPt.t))}s
                      </span>
                      {!b.done && <span className="text-[color:var(--warn-fg)]">（未闭合）</span>}
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
                    <span className={s.active > 0 ? " text-[color:var(--ok-fg)]" : " text-faint"}>
                      {" "}· 在产 {s.active}
                    </span>
                  </li>
                ))}
            </ul>
          </div>

          {stallsInView.length > 0 && (
            <div>
              <div className="text-faint">视窗内卡点</div>
              <ul className="space-y-0.5 text-[color:var(--err-fg)]">
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
