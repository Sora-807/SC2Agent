/** 通用小件（面板/徽标/空态）—— 纯展示，无业务 */
import type { ReactNode } from "react";

export function Card(props: {
  title: string;
  className?: string;
  right?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className={"rounded border border-neutral-800 bg-neutral-900/40 p-3 " + (props.className ?? "")}>
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <h2 className="font-semibold text-neutral-200">{props.title}</h2>
        {props.right}
      </div>
      {props.children}
    </section>
  );
}

export const Pill = (p: { label: string; value: string; tone?: "normal" | "warn" | "live" }) => (
  <span
    className={
      "rounded px-2 py-0.5 text-xs " +
      (p.tone === "warn"
        ? "bg-amber-900/50 text-amber-300"
        : p.tone === "live"
          ? "bg-emerald-900/50 text-emerald-300"
          : "bg-neutral-800")
    }
  >
    <span className="text-neutral-500">{p.label} </span>
    {p.value}
  </span>
);

export const Empty = (p: { text?: string }) => (
  <div className="text-neutral-600">{p.text ?? "该时刻无此帧"}</div>
);

export const sevClass = (s: "info" | "warn" | "error"): string =>
  s === "error" ? "text-red-400" : s === "warn" ? "text-amber-400" : "text-sky-400";

export function fmtTime(t: number): string {
  const s = Math.max(0, Math.round(t));
  return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
}

/** 未实现页面的占位：诚实写清哪个阶段做、现在有什么数据 */
export function Stub(props: { stage: string; title: string; will: string[]; children?: ReactNode }) {
  return (
    <div className="space-y-3">
      <Card title={props.title} right={<span className="text-xs text-neutral-500">{props.stage} 实现</span>}>
        <div className="text-neutral-400">该页在 {props.stage} 落地，将包含：</div>
        <ul className="mt-1 list-inside list-disc text-neutral-300">
          {props.will.map((w) => <li key={w}>{w}</li>)}
        </ul>
      </Card>
      {props.children}
    </div>
  );
}
