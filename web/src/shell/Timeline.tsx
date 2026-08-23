/**
 * 时间轴（ADR-0023 §3；2026-08-22 下沉进主列 TimeStrip，不再横跨顶栏）
 *
 * - 刻度上可见：警报 / 策略转移 / 提案（标记来自帧，前端不编文案，title 悬停看全文）
 * - 拖动 = seek；拖到最右端 = 回到实时
 * - live 源在回看期间**右端仍在生长**（后台继续采集），这条肉眼可见
 * - 形态收敛（I11）：一条细轨（h-7），不再是自带卡片的粗块；回看态染粉、跟随态蓝
 */
import { useCallback, useRef } from "react";
import { useFrames } from "../store/frames";
import type { TimelineMarker } from "../source/types";
import { fmtTime } from "./ui";

const MARKER_STYLE: Record<TimelineMarker["kind"], { color: string; zh: string; h: string }> = {
  alert: { color: "bg-[color:var(--warn-fg)]", zh: "警报", h: "h-3" },
  transition: { color: "bg-[color:var(--accent-blue-fg)]", zh: "策略转移", h: "h-2.5" },
  proposal: { color: "bg-[color:var(--accent-pink-fg)]", zh: "提案", h: "h-3" },
  snapshot: { color: "bg-[color:var(--border-l2)]", zh: "快照", h: "h-2" },
};

export function Timeline() {
  const { range, position, markers, caps, timeline, seek } = useFrames();
  const trackRef = useRef<HTMLDivElement | null>(null);
  const dragging = useRef(false);
  const review = timeline === "review";

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
    <div
      ref={trackRef}
      className={
        "relative h-7 min-w-0 flex-1 select-none rounded border " +
        (review
          ? "border-accent-pink bg-pink-soft"
          : "border-l1 bg-panel") +
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
      {/* 已播放区间：跟随态蓝洗 / 回看态粉洗（更深的同一色，肉眼可分） */}
      <div
        className="absolute inset-y-0 left-0 rounded-l"
        style={{
          width: pct(position) + "%",
          background: review
            ? "color-mix(in srgb, var(--accent-pink) 34%, transparent)"
            : "color-mix(in srgb, var(--accent-blue) 26%, transparent)",
        }}
      />
      {/* 标记（title 悬停看全文；图例随形态收敛退役，标记本身仍可见） */}
      {markers.map((m, i) => {
        const st = MARKER_STYLE[m.kind];
        return (
          <div
            key={m.kind + i + m.t}
            title={fmtTime(m.t) + " · " + st.zh + "：" + m.text}
            className={"absolute bottom-0.5 w-0.5 " + st.h + " " + st.color}
            style={{ left: pct(m.t) + "%" }}
          />
        );
      })}
      {/* 游标：回看粉 / 跟随蓝，时刻标签骑在游标上 */}
      <div
        className={"absolute inset-y-0 w-0.5 " + (review
          ? "bg-[color:var(--accent-pink-fg)]" : "bg-[color:var(--accent-blue-fg)]")}
        style={{ left: pct(position) + "%" }}
      >
        <div className={"absolute -top-0.5 left-1 whitespace-nowrap rounded px-1 text-note tabular-nums text-white " + (review
          ? "bg-[color:var(--accent-pink-fg)]" : "bg-[color:var(--accent-blue-fg)]")}>
          {fmtTime(position)}
        </div>
      </div>
    </div>
  );
}
