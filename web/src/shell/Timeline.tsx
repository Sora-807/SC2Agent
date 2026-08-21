/**
 * 时间线（ADR-0023 §3）
 *
 * - 刻度上可见：警报 / 策略转移 / 提案（标记来自帧，前端不编文案）
 * - 拖动 = seek；拖到最右端 = 回到实时
 * - live 源在回看期间**右端仍在生长**（后台继续采集），这条肉眼可见
 */
import { useCallback, useRef } from "react";
import { useFrames } from "../store/frames";
import type { TimelineMarker } from "../source/types";
import { fmtTime } from "./ui";

const MARKER_STYLE: Record<TimelineMarker["kind"], { color: string; zh: string; h: string }> = {
  alert: { color: "bg-amber-400", zh: "警报", h: "h-3" },
  transition: { color: "bg-sky-400", zh: "策略转移", h: "h-2.5" },
  proposal: { color: "bg-fuchsia-400", zh: "提案", h: "h-3" },
  snapshot: { color: "bg-neutral-500", zh: "快照", h: "h-2" },
};

export function Timeline() {
  const { range, position, markers, caps, timeline, seek } = useFrames();
  const trackRef = useRef<HTMLDivElement | null>(null);
  const dragging = useRef(false);

  const span = Math.max(1e-6, range.to - range.from);
  const pct = (t: number): number =>
    Math.min(100, Math.max(0, ((t - range.from) / span) * 100));

  const seekFromEvent = useCallback(
    (clientX: number): void => {
      const el = trackRef.current;
      if (!el || !caps.seek) return;
      const r = el.getBoundingClientRect();
      const ratio = Math.min(1, Math.max(0, (clientX - r.left) / r.width));
      seek(range.from + ratio * span);
    },
    [caps.seek, range.from, span, seek],
  );

  return (
    <div className="select-none py-2">
      <div
        ref={trackRef}
        className={
          "relative h-10 rounded border " +
          (timeline === "review" ? "border-amber-700/60 bg-amber-950/20" : "border-neutral-800 bg-neutral-900/60") +
          (caps.seek ? " cursor-pointer" : " cursor-not-allowed opacity-60")
        }
        onPointerDown={(e) => {
          if (!caps.seek) return;
          dragging.current = true;
          e.currentTarget.setPointerCapture(e.pointerId);
          seekFromEvent(e.clientX);
        }}
        onPointerMove={(e) => dragging.current && seekFromEvent(e.clientX)}
        onPointerUp={(e) => {
          dragging.current = false;
          e.currentTarget.releasePointerCapture(e.pointerId);
        }}
      >
        {/* 已播放区间 */}
        <div
          className={"absolute inset-y-0 left-0 rounded-l " + (timeline === "review" ? "bg-amber-900/25" : "bg-emerald-900/20")}
          style={{ width: pct(position) + "%" }}
        />
        {/* 标记 */}
        {markers.map((m, i) => {
          const st = MARKER_STYLE[m.kind];
          return (
            <div
              key={m.kind + i + m.t}
              title={fmtTime(m.t) + " · " + st.zh + "：" + m.text}
              className={"absolute bottom-1 w-0.5 " + st.h + " " + st.color}
              style={{ left: pct(m.t) + "%" }}
            />
          );
        })}
        {/* 游标 */}
        <div
          className={"absolute inset-y-0 w-0.5 " + (timeline === "review" ? "bg-amber-400" : "bg-emerald-400")}
          style={{ left: pct(position) + "%" }}
        >
          <div className={"absolute -top-0.5 left-1 whitespace-nowrap rounded px-1 text-note " +
            (timeline === "review" ? "bg-amber-400 text-black" : "bg-emerald-400 text-black")}>
            {fmtTime(position)}
          </div>
        </div>
      </div>

      <div className="mt-1 flex items-center justify-between text-note text-faint">
        <span>{fmtTime(range.from)}</span>
        <span className="flex gap-3">
          {(Object.keys(MARKER_STYLE) as TimelineMarker["kind"][])
            .filter((k) => markers.some((m) => m.kind === k))
            .map((k) => (
              <span key={k} className="flex items-center gap-1">
                <i className={"inline-block h-2 w-0.5 " + MARKER_STYLE[k].color} />
                {MARKER_STYLE[k].zh} {markers.filter((m) => m.kind === k).length}
              </span>
            ))}
          {!caps.seek && <span className="text-ghost">该帧源不支持 seek</span>}
        </span>
        <span>{fmtTime(range.to)}</span>
      </div>
    </div>
  );
}
