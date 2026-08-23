/**
 * 投影曲线（planner）—— 带坐标轴、刻度、网格、游标读数、卡点竖线。
 *
 * x 轴 = 绝对游戏时间（mm:ss）。I5 起 x 轴**全时受控**：
 * - 板内（domain prop）：投影板统一给定域（曲线与泳道共用同一条时间轴）；
 * - 独立卡（概览等）：内部持有定域 —— 默认 [0, 数据末端]（零点钉在最左，不出
 *   半截负时间的留白），**按住左键拖 = 平移视野**（2026-08-22 二十轮用户拍板：
 *   框选放大不是想要的效果；uPlot 自带拖拽缩放全时禁用 —— 蓝色选框也随之下线）。
 * 卡点（stalled）画成红色竖线并在下方标注原因，原因文本直接来自帧（红线 C3/C4）。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { AlignedData } from "uplot";
import type { ProjectionFrame } from "../contract";
import { fmtMMSS, toMarkers, toSeries, type EventMarker } from "./projection-data";
import { type TimeDomain } from "./gantt-data";
import { UPlotChart, type ChartOptions } from "./UPlotChart";
import { chartColors, seriesColors } from "./chart-theme";

// 主题色在 options 构建处现取（chart-theme.ts；换主题重跑 memo）

/**
 * 绘图区左右留白（px）：y 轴宽 + 右内边距。
 * 泳道（ProjectionBoard）靠它与曲线**像素对齐** —— 单点定义，别在别处再写一份。
 */
export const PROJECTION_GUTTER = { left: 46, right: 8 } as const;

/** 矿曲线显示上限（用户拍板 2026-08-22）：超过 600 的部分贴顶画（后续没用）。
 *  检查面板/hover 读数仍显示真实值 —— 只是纵轴不再被浮矿撑高。 */
export const MINERAL_CAP = 600;

/** 拖动判定容差（px）：超过才算平移，不是点击/悬停 */
const PAN_SLOP = 4;
/** 平移增益：1.0 = 内容与鼠标 1:1 跟手（二十四轮用户拍板「不跟手」——
 *  0.5 的减半让视图永远追不上鼠标）；跨度大时的速度上限交给 PAN_SPAN_CAP */
const PAN_GAIN = 1.0;
/** 平移速度封顶（秒）：可视跨度大于它时整幅拖满也只有 PAN_SPAN_CAP（半幅 ≈8 分钟，
 *  二十二轮用户拍板的灵敏度上限）；小跨度视图不受影响（仍 1:1 × 增益跟手） */
const PAN_SPAN_CAP = 960;

export function ProjectionChart(props: {
  frame: ProjectionFrame;
  height?: number;
  /** 锁定 x 轴定域（I5：与泳道共享一条时间轴时传，平移由板统一处理）；
   *  不传 = 独立卡：内部定域 [0, 数据末端]，拖图平移 */
  domain?: TimeDomain | null;
  /** 光标横移回调（t = 游标处游戏时间）；离开绘图区回调 null（F15 泳道联动） */
  onCursor?: (t: number | null) => void;
  /** 点击回调（t = 点击处游戏时间；F15 点击检查） */
  onPick?: (t: number) => void;
  /** 板内组合时隐藏自带脚注（F15：脚注由 ProjectionBoard 统一收底） */
  hideFooter?: boolean;
  /** 板内组合时隐藏 uPlot 自带 legend（F17：游标线不许越过时间轴；
   *  hover 读数由板级 footer 承担）。legend 是 canvas 下方的独立 div，
   *  不藏的话板级 inset-y-0 游标线会穿过它） */
  hideLegend?: boolean;
}) {
  const series = useMemo(() => toSeries(props.frame), [props.frame]);
  const markers = useMemo(() => toMarkers(props.frame), [props.frame]);

  // 独立卡（无 domain prop）：内部定域。null = 还没拖过 → 跟数据（零点钉 0）。
  const [ownRange, setOwnRange] = useState<TimeDomain | null>(null);
  const standalone = props.domain == null;
  const domain: TimeDomain = props.domain
    ?? ownRange
    ?? { from: Math.max(0, series.from), to: Math.max(series.to, series.from + 1) };
  // 拖过之后数据仍在长（live/回放推进）：保持左端不动、右端延伸
  useEffect(() => {
    if (!standalone || !ownRange || series.to <= ownRange.to) return;
    setOwnRange((r) => (r ? { from: r.from, to: Math.max(r.to, series.to) } : r));
  }, [series.to, standalone, ownRange]);

  // 独立卡的拖图平移：wrapper 级 native 指针（板内的平移由板统一处理，这里不抢）
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const pan = useRef<{ startX: number; from: number; to: number } | null>(null);
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || !standalone) return;
    const onDown = (e: PointerEvent): void => {
      if (e.button !== 0) return;
      const d = domain;
      pan.current = { startX: e.clientX, from: d.from, to: d.to };
    };
    const onMove = (e: PointerEvent): void => {
      const p = pan.current;
      if (!p) return;
      // 左键已松（元素外/别的窗口松手）→ 手势就地收尾（防御：陈旧 startX 锁死平移方向）
      if ((e.buttons & 1) === 0) { pan.current = null; return; }
      const dx = e.clientX - p.startX;
      if (Math.abs(dx) < PAN_SLOP) return;
      const width = el.getBoundingClientRect().width - PROJECTION_GUTTER.left - PROJECTION_GUTTER.right;
      if (width <= 0) return;
      const span = p.to - p.from;
      // 向左拖（dx<0）= 看未来；零点是最左边界，拖不过去
      const panSpan = Math.min(span, PAN_SPAN_CAP);
      let from = p.from - (dx / width) * panSpan * PAN_GAIN;
      let to = p.to - (dx / width) * panSpan * PAN_GAIN;
      if (from < 0) { to -= from; from = 0; }
      // 右端钳数据末端（二十七轮）：右侧不许出现空白 —— 与左缘零点对称
      if (to > series.to) { from -= to - series.to; to = series.to; }
      if (from < 0) from = 0;
      setOwnRange({ from, to: Math.max(from + 1, to) });
    };
    const onUp = (): void => { pan.current = null; };
    el.addEventListener("pointerdown", onDown);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      el.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [standalone, domain.from, domain.to]);

  // 事件竖线随帧变化，但不能进 options（否则重建图）→ 走 ref
  const markersRef = useRef<EventMarker[]>(markers);
  markersRef.current = markers;
  // 定域同理：每帧的 domain 经 ref 喂给 range 函数（进 options 就每帧重建图了）
  const domainRef = useRef(domain);
  domainRef.current = domain;

  const data = useMemo<AlignedData>(
    () => [series.t, series.minerals.map((v) => Math.min(v, MINERAL_CAP)), series.gas],
    [series],
  );

  const options = useMemo<ChartOptions>(
    () => {
      // 图表色随主题（轴/网格读 CSS 变量；系列色亮色降饱和）—— theme 在依赖里
      const C = { ...seriesColors(), ...chartColors() };
      return ({
      scales: {
        y: { range: (): [number, number] => [0, MINERAL_CAP] },
        x: {
          time: false,
          // x 轴全时受控（I5 + 二十轮）：定域由调用方/内部持有，经 ref 喂进来
          range: (_u: unknown, min: number, max: number): [number, number] => {
            const d = domainRef.current;
            return d ? [Math.max(0, d.from), Math.max(d.from + 1, d.to)] : [min, max];
          },
        },
      },
      padding: [8, PROJECTION_GUTTER.right, 0, 0],
      series: [
        { label: "游戏时间", value: (_u, v) => (v == null ? "--" : fmtMMSS(v)) },
        { label: "晶体矿", stroke: C.minerals, width: 1.6,
          value: (_u, v) => (v == null ? "--" : String(Math.round(v))) },
        { label: "高能瓦斯", stroke: C.gas, width: 1.6,
          value: (_u, v) => (v == null ? "--" : String(Math.round(v))) },
      ],
      axes: [
        {
          stroke: C.axis,
          grid: { stroke: C.grid, width: 1 },
          ticks: { stroke: C.grid, width: 1 },
          font: "11px ui-sans-serif, system-ui",
          space: 64,
          values: (_u, ticks) => ticks.map((t) => fmtMMSS(t)),
        },
        {
          stroke: C.axis,
          grid: { stroke: C.grid, width: 1 },
          ticks: { stroke: C.grid, width: 1 },
          font: "11px ui-sans-serif, system-ui",
          size: PROJECTION_GUTTER.left,
          values: (_u, ticks) => ticks.map((v) => (v >= 1000 ? (v / 1000).toFixed(1) + "k" : String(Math.round(v)))),
        },
      ],
      legend: { show: !props.hideLegend, live: true },
      // uPlot 自带拖拽缩放全时禁用（二十轮用户拍板：拖动是平移，蓝色选框下线）
      cursor: { y: false, drag: { x: false, y: false } },
      hooks: {
        draw: [
          (u) => {
            const { ctx } = u;
            const top = u.bbox.top;
            const bottom = u.bbox.top + u.bbox.height;
            ctx.save();
            ctx.lineWidth = 1;
            ctx.font = "10px ui-sans-serif, system-ui";
            ctx.textAlign = "left";
            for (const m of markersRef.current) {
              const x = u.valToPos(m.t, "x", true);
              if (!Number.isFinite(x)) continue;
              if (m.kind === "stalled") {
                // 卡点：红色全高虚线 + 原因文字（原因来自后端，前端不编文案）
                ctx.strokeStyle = C.stalled;
                ctx.setLineDash([3, 3]);
                ctx.moveTo(x, top);
                ctx.lineTo(x, bottom);
                ctx.stroke();
                ctx.fillStyle = C.stalled;
                ctx.fillText(m.text, Math.min(x + 4, u.bbox.left + u.bbox.width - 96), top + 11);
              } else if (m.kind === "assign") {
                // 瞬时目标值动作（维持采气…）：黄色虚线标记执行位置，不带文字
                // （开工/完成的底部短刻度已删 —— 密集时像毛刺，泳道已表达工期）
                ctx.strokeStyle = C.assign;
                ctx.setLineDash([2, 4]);
                ctx.moveTo(x, top);
                ctx.lineTo(x, bottom);
                ctx.stroke();
              }
              ctx.setLineDash([]);
            }
            ctx.restore();
          },
        ],
      },
      });
    },
    [props.hideLegend === true],
  );

  return (
    <div ref={wrapRef}>
      <UPlotChart
        data={data}
        options={options}
        height={props.height ?? 190}
        xRange={domain}
        onCursor={props.onCursor}
        onPick={props.onPick}
      />
      {!props.hideFooter && (
        <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-label text-faint">
          <span>视野 {fmtMMSS(series.from)} → {fmtMMSS(series.to)}（+{Math.round(series.to - series.from)}s）</span>
          <span>采样 {series.t.length} 点 / 1 秒一点</span>
          <span>基于 seq {props.frame.based_on_seq}</span>
          <span>
            来源 {props.frame.source.kind === "live_queue"
              ? "当前队列 " + props.frame.source.queue_name
              : "草稿 " + props.frame.source.plan_id}
          </span>
          <span className="text-ghost">
            竖虚线=卡点 · 底部长刻度=完成 · 短刻度=开工 · 按住拖=平移
          </span>
          {markers.filter((m) => m.kind === "stalled").length > 0 && (
            <span className="text-[color:var(--err-fg)]">
              卡点 {markers.filter((m) => m.kind === "stalled").length} 处
            </span>
          )}
        </div>
      )}
    </div>
  );
}
