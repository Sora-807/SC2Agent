/**
 * 双投影对比（F7 §6 的杀手功能）——「接受前先看未来」。
 *
 * 两条曲线用**同一个 GameState** 起点（后端保证），所以差异只来自队列本身。
 * 画法上刻意把"当前"画成虚线、"提案后"画成实线：一眼看出提案把未来推到哪去了。
 * 关键读数（终点矿/气/人口上限、卡点数）直接列在下面 —— 曲线看趋势，数字看结论。
 */
import { useMemo, useRef } from "react";
import type { AlignedData } from "uplot";
import type { ProjectionFrame } from "../contract";
import { fmtMMSS, toMarkers, type EventMarker } from "./projection-data";
import { UPlotChart, type ChartOptions } from "./UPlotChart";
import { chartColors, seriesColors } from "./chart-theme";


export function ProjectionPairChart(props: {
  current: ProjectionFrame;
  proposed: ProjectionFrame;
  height?: number;
}) {
  const { current, proposed } = props;
  const markersRef = useRef<EventMarker[]>([]);
  markersRef.current = toMarkers(proposed).filter((m) => m.kind === "stalled");

  const data = useMemo<AlignedData>(() => {
    const n = Math.min(current.points.length, proposed.points.length);
    return [
      current.points.slice(0, n).map((p) => p.t),
      current.points.slice(0, n).map((p) => p.minerals),
      proposed.points.slice(0, n).map((p) => p.minerals),
      current.points.slice(0, n).map((p) => p.gas),
      proposed.points.slice(0, n).map((p) => p.gas),
    ];
  }, [current, proposed]);

  const options = useMemo<ChartOptions>(
    () => {
      const C = { ...seriesColors(), ...chartColors() };
      return ({
      scales: { x: { time: false } },
      padding: [8, 8, 0, 0],
      series: [
        { label: "游戏时间", value: (_u, v) => (v == null ? "--" : fmtMMSS(v)) },
        { label: "矿·当前", stroke: C.current, width: 1.2, dash: [4, 3] },
        { label: "矿·提案后", stroke: C.minerals, width: 1.8 },
        { label: "气·当前", stroke: C.current, width: 1, dash: [2, 3] },
        { label: "气·提案后", stroke: C.gas, width: 1.4 },
      ],
      axes: [
        { stroke: C.axis, grid: { stroke: C.grid }, ticks: { stroke: C.grid },
          font: "11px ui-sans-serif", space: 64,
          values: (_u, ticks) => ticks.map((t) => fmtMMSS(t)) },
        { stroke: C.axis, grid: { stroke: C.grid }, ticks: { stroke: C.grid },
          font: "11px ui-sans-serif", size: 46,
          values: (_u, ticks) => ticks.map((v) => (v >= 1000 ? (v / 1000).toFixed(1) + "k" : String(Math.round(v)))) },
      ],
      legend: { show: true, live: true },
      cursor: { y: false },
      hooks: {
        draw: [
          (u) => {
            // 只标提案后的卡点：用户要判断的是"接受之后会不会卡"
            const { ctx } = u;
            ctx.save();
            ctx.setLineDash([3, 3]);
            ctx.strokeStyle = C.stalled;
            ctx.fillStyle = C.stalled;
            ctx.font = "10px ui-sans-serif";
            for (const m of markersRef.current) {
              const x = u.valToPos(m.t, "x", true);
              if (!Number.isFinite(x)) continue;
              ctx.beginPath();
              ctx.moveTo(x, u.bbox.top);
              ctx.lineTo(x, u.bbox.top + u.bbox.height);
              ctx.stroke();
              ctx.fillText(m.text, x + 3, u.bbox.top + 11);
            }
            ctx.restore();
          },
        ],
      },
      });
    },
    [],
  );

  const last = (f: ProjectionFrame) => f.points.at(-1);
  const stalls = (f: ProjectionFrame) => f.events.filter((e) => e.kind === "stalled").length;
  const done = (f: ProjectionFrame) =>
    new Set(f.events.filter((e) => e.kind === "completed").map((e) => e.stable_id)).size;

  const rows: [string, (f: ProjectionFrame) => string][] = [
    ["终点余矿", (f) => String(Math.round(last(f)?.minerals ?? 0))],
    ["终点余气", (f) => String(Math.round(last(f)?.gas ?? 0))],
    ["人口上限", (f) => String(last(f)?.supply_cap ?? 0)],
    ["建成种类", (f) => String(done(f))],
    ["卡点数", (f) => String(stalls(f))],
  ];

  return (
    <div>
      <UPlotChart data={data} options={options} height={props.height ?? 200} />
      <table className="mt-2 w-full text-left text-note">
        <thead className="text-faint">
          <tr><th>读数</th><th>当前</th><th>提案后</th><th>差</th></tr>
        </thead>
        <tbody>
          {rows.map(([label, get]) => {
            const a = Number(get(current));
            const b = Number(get(proposed));
            const delta = b - a;
            return (
              <tr key={label}>
                <td className="text-dim">{label}</td>
                <td>{get(current)}</td>
                <td>{get(proposed)}</td>
                <td className={delta === 0 ? "text-ghost"
                  : (label === "卡点数" ? delta < 0 : delta > 0) ? "text-[color:var(--ok-fg)]" : "text-[color:var(--warn-fg)]"}>
                  {delta === 0 ? "—" : (delta > 0 ? "+" : "") + delta}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="mt-1 text-note text-ghost">
        虚线 = 当前队列 · 实线 = 提案后 · 红虚线 = 提案后的卡点。
        两条曲线同一个 GameState 起点，所以差异只来自队列本身。
      </div>
    </div>
  );
}
