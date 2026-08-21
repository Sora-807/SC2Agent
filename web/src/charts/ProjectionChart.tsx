/**
 * 投影曲线（planner）—— 带坐标轴、刻度、网格、游标读数、卡点竖线。
 *
 * x 轴 = 绝对游戏时间（mm:ss），所以"到哪了"一眼可见；左边缘即"现在"。
 * 卡点（stalled）画成红色竖线并在下方标注原因，原因文本直接来自帧（红线 C3/C4）。
 *
 * I5：可选 `domain` 把 x 轴锁定到调用方给的定域 —— 曲线与泳道共用同一条时间轴。
 * 锁轴时关掉 x 框选缩放：曲线缩了、泳道跟不上，像素对齐就毁了。
 */
import { useMemo, useRef } from "react";
import type { AlignedData } from "uplot";
import type { ProjectionFrame } from "../contract";
import { fmtMMSS, toMarkers, toSeries, type EventMarker } from "./projection-data";
import { type TimeDomain } from "./gantt-data";
import { UPlotChart, type ChartOptions } from "./UPlotChart";

const COLOR = {
  minerals: "#34d399",
  gas: "#38bdf8",
  axis: "#6b7280",
  grid: "#1f2937",
  stalled: "#f87171",
  started: "#38bdf8",
  completed: "#34d399",
} as const;

/**
 * 绘图区左右留白（px）：y 轴宽 + 右内边距。
 * 泳道（ProjectionBoard）靠它与曲线**像素对齐** —— 单点定义，别在别处再写一份。
 */
export const PROJECTION_GUTTER = { left: 46, right: 8 } as const;

export function ProjectionChart(props: {
  frame: ProjectionFrame;
  height?: number;
  /** 锁定 x 轴定域（I5：与泳道共享一条时间轴时传）；不传 = 自适应数据范围。
   *  F15 起定域由调用方动态控制（滚轮/中心跟随），经 UPlotChart 受控 setScale 生效 */
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

  // 事件竖线随帧变化，但不能进 options（否则重建图）→ 走 ref
  const markersRef = useRef<EventMarker[]>(markers);
  markersRef.current = markers;
  // 锁定定域同理：每帧的 domain 经 ref 喂给 range 函数（进 options 就每帧重建图了）
  const domainRef = useRef(props.domain);
  domainRef.current = props.domain;
  const locked = props.domain != null;

  const data = useMemo<AlignedData>(
    () => [series.t, series.minerals, series.gas],
    [series],
  );

  const options = useMemo<ChartOptions>(
    () => ({
      // x 是游戏秒，不是 unix 时间 → 关掉 uPlot 的时间轴，自己格式化
      scales: {
        x: {
          time: false,
          // I5 锁轴：定域由调用方每帧给出（经 ref），曲线与泳道因此共用同一条时间轴
          ...(locked
            ? {
                range: (_u: unknown, min: number, max: number): [number, number] => {
                  const d = domainRef.current;
                  return d ? [d.from, d.to] : [min, max];
                },
              }
            : {}),
        },
      },
      padding: [8, PROJECTION_GUTTER.right, 0, 0],
      series: [
        { label: "游戏时间", value: (_u, v) => (v == null ? "--" : fmtMMSS(v)) },
        { label: "晶体矿", stroke: COLOR.minerals, width: 1.6,
          value: (_u, v) => (v == null ? "--" : String(Math.round(v))) },
        { label: "高能瓦斯", stroke: COLOR.gas, width: 1.6,
          value: (_u, v) => (v == null ? "--" : String(Math.round(v))) },
      ],
      axes: [
        {
          stroke: COLOR.axis,
          grid: { stroke: COLOR.grid, width: 1 },
          ticks: { stroke: COLOR.grid, width: 1 },
          font: "11px ui-sans-serif, system-ui",
          space: 64,
          values: (_u, ticks) => ticks.map((t) => fmtMMSS(t)),
        },
        {
          stroke: COLOR.axis,
          grid: { stroke: COLOR.grid, width: 1 },
          ticks: { stroke: COLOR.grid, width: 1 },
          font: "11px ui-sans-serif, system-ui",
          size: PROJECTION_GUTTER.left,
          values: (_u, ticks) => ticks.map((v) => (v >= 1000 ? (v / 1000).toFixed(1) + "k" : String(Math.round(v)))),
        },
      ],
      legend: { show: !props.hideLegend, live: true },
      // 锁轴时关掉 x 框选缩放（见文件头注释）
      cursor: locked ? { y: false } : { y: false, drag: { x: true, y: false } },
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
              ctx.strokeStyle = COLOR[m.kind];
              ctx.beginPath();
              if (m.kind === "stalled") {
                // 卡点：全高虚线 + 原因文字（原因来自后端，前端不编文案）
                ctx.setLineDash([3, 3]);
                ctx.moveTo(x, top);
                ctx.lineTo(x, bottom);
                ctx.stroke();
                ctx.fillStyle = COLOR.stalled;
                ctx.fillText(m.text, Math.min(x + 4, u.bbox.left + u.bbox.width - 96), top + 11);
              } else {
                // 开工/完成：底部短刻度（数量多，不带文字，靠游标读）
                ctx.setLineDash([]);
                ctx.globalAlpha = 0.55;
                ctx.moveTo(x, bottom - (m.kind === "completed" ? 9 : 5));
                ctx.lineTo(x, bottom);
                ctx.stroke();
                ctx.globalAlpha = 1;
              }
            }
            ctx.restore();
          },
        ],
      },
    }),
    [locked, props.hideLegend === true],
  );

  return (
    <div>
      <UPlotChart
        data={data}
        options={options}
        height={props.height ?? 190}
        xRange={props.domain}
        onCursor={props.onCursor}
        onPick={props.onPick}
      />
      {!props.hideFooter && (
        <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-faint">
          <span>视野 {fmtMMSS(series.from)} → {fmtMMSS(series.to)}（+{Math.round(series.to - series.from)}s）</span>
          <span>采样 {series.t.length} 点 / 1 秒一点</span>
          <span>基于 seq {props.frame.based_on_seq}</span>
          <span>
            来源 {props.frame.source.kind === "live_queue"
              ? "当前队列 " + props.frame.source.queue_name
              : "草稿 " + props.frame.source.plan_id}
          </span>
          <span className="text-ghost">
            竖虚线=卡点 · 底部长刻度=完成 · 短刻度=开工
          </span>
          {markers.filter((m) => m.kind === "stalled").length > 0 && (
            <span className="text-red-400">
              卡点 {markers.filter((m) => m.kind === "stalled").length} 处
            </span>
          )}
        </div>
      )}
    </div>
  );
}
