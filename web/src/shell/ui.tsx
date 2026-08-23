/** 通用小件（面板/徽标/空态）—— 纯展示，无业务 */
import type { ReactNode } from "react";
import { T } from "./tokens";

/**
 * 页面滚动区（红线 G1 / 决策 U13）。
 *
 * 外壳固定一屏且 `main` 是 `overflow-hidden`，所以**每个页面根节点必须自己声明滚动**——
 * 否则内容会被裁掉。文档型页面直接套它；分栏页（如地图页）自己给各 pane 分配滚动权。
 * `pb-6` 是给右下角状态浮层留的净空。
 *
 * 抽成常量而不是各页手写：`web/tests/layout.test.ts` 靠它机械断言"没有页面忘记接管滚动"。
 */
export const PAGE_SCROLL = "h-full min-h-0 overflow-y-auto pb-6";

export function Card(props: {
  title: string;
  /** 标题旁的 ？ 帮助（I4）：hover 看本面板怎么读（title 文案来自后端或本页语境） */
  help?: string;
  className?: string;
  right?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className={"rounded-lg border border-l1 bg-panel p-3 shadow-sm " + (props.className ?? "")}>
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <h2 className="flex items-center gap-1.5 font-semibold text-strong">
          {props.title}
          {props.help && (
            <span
              title={props.help}
              className={"inline-flex h-4 w-4 cursor-help items-center justify-center "
                + "rounded-full border border-l2 font-normal leading-none text-faint "
                + "hover:bg-inset hover:text-dim " + T.note}
            >?</span>
          )}
        </h2>
        {props.right}
      </div>
      {props.children}
    </section>
  );
}

export const Empty = (p: { text?: string }) => (
  <div className="text-ghost">{p.text ?? "该时刻无此帧"}</div>
);

export const sevClass = (s: "info" | "warn" | "error"): string =>
  s === "error" ? "text-[color:var(--err-fg)]" : s === "warn" ? "text-[color:var(--warn-fg)]" : "text-blue-fg";

export function fmtTime(t: number): string {
  const s = Math.max(0, Math.round(t));
  return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
}

/** 未实现页面的占位：诚实写清哪个阶段做、现在有什么数据 */
export function Stub(props: { stage: string; title: string; will: string[]; children?: ReactNode }) {
  return (
    <div className="space-y-3">
      <Card title={props.title} right={<span className="text-label text-faint">{props.stage} 实现</span>}>
        <div className="text-dim">该页在 {props.stage} 落地，将包含：</div>
        <ul className="mt-1 list-inside list-disc text-dim">
          {props.will.map((w) => <li key={w}>{w}</li>)}
        </ul>
      </Card>
      {props.children}
    </div>
  );
}
