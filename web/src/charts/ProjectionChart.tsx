/**
 * 投影曲线（planner）—— 带坐标轴、刻度、网格、游标读数、卡点竖线。
 *
 * x 轴 = 绝对游戏时间（mm:ss），所以"到哪了"一眼可见；左边缘即"现在"。
 * 卡点（stalled）画成红色竖线并在下方标注原因，原因文本直接来自帧（红线 C3/C4）。
 */
import { useMemo, useRef } from "react";
import type { AlignedData } from "uplot";
import type { ProjectionFrame } from "../contract";
import { fmtMMSS, toMarkers, toSeries, type EventMarker } from "./projection-data";
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

export function ProjectionChart(props: { frame: ProjectionFrame; height?: number }) {
  const series = useMemo(() => toSeries(props.frame), [props.frame]);
  const markers = useMemo(() => toMarkers(props.frame), [props.frame]);

  // 事件竖线随帧变化，但不能进 options（否则重建图）→ 走 ref
  const markersRef = useRef<EventMarker[]>(markers);
  markersRef.current = markers;

  const data = useMemo<AlignedData>(
    () => [series.t, series.minerals, series.gas],
    [series],
  );

  const options = useMemo<ChartOptions>(
    () => ({
      // x 是游戏秒，不是 unix 时间 → 关掉 uPlot 的时间轴，自己格式化
      scales: { x: { time: false } },
      padding: [8, 8, 0, 0],
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
          size: 46,
          values: (_u, ticks) => ticks.map((v) => (v >= 1000 ? (v / 1000).toFixed(1) + "k" : String(Math.round(v)))),
        },
      ],
      legend: { show: true, live: true },
      cursor: { y: false, drag: { x: true, y: false } },
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
    [],
  );

  return (
    <div>
      <UPlotChart data={data} options={options} height={props.height ?? 190} />
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
    </div>
  );
}
