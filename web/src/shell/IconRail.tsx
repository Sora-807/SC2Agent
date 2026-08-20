/** 左侧图标栏：六个一级页面（ADR-0023 的一级结构 + 本计划新增的调试页） */
import { PAGES, type PageKey } from "./route";

export function IconRail(props: { page: PageKey; go: (p: PageKey) => void }) {
  return (
    <nav className="flex w-14 shrink-0 flex-col gap-1 border-r border-neutral-800 pr-1 pt-2">
      {PAGES.map((p) => {
        const active = p.key === props.page;
        return (
          <button
            key={p.key}
            onClick={() => props.go(p.key)}
            title={p.label + "（" + p.stage + "）"}
            className={
              "flex flex-col items-center rounded py-2 text-[11px] " +
              (active ? "bg-neutral-800 text-neutral-100" : "text-neutral-500 hover:bg-neutral-900")
            }
          >
            <span className="text-base leading-none">{p.icon}</span>
            <span className="mt-1">{p.label}</span>
          </button>
        );
      })}
    </nav>
  );
}
