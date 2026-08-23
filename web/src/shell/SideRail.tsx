/**
 * 左侧图标栏（2026-08-22 十五轮：接在顶栏下面，贴视口左边）—— 窄列图标+小字，
 * 蓝外围上的一列白色浮起按钮。分组仍是模式的函数（rail.ts），角标只读帧字段。
 * 工作面板（地图/生产规划的工具区）不在这 —— 十五轮起移到右侧固定列
 * （靠近对话框，鼠标距离短，用户拍板）。
 */
import { Fragment } from "react";
import { useFrames } from "../store/frames";
import { badgeFor, badgeCounters, railGroups } from "./rail";
import { PAGE_GROUP_LABEL, type PageKey } from "./route";
import { T } from "./tokens";

export function SideRail(props: { page: PageKey; go: (p: PageKey) => void }) {
  const { mode, proposals, alerts, production } = useFrames();
  const groups = railGroups(mode);
  const badges = badgeCounters(proposals, alerts, production);

  return (
    <nav className="flex w-[4.5rem] shrink-0 flex-col gap-1 border-r border-chrome bg-panel p-1.5">
      {groups.map((g, gi) => (
        <Fragment key={g.key}>
          {gi > 0 && <div className="my-0.5 border-t border-l1" />}
          {g.items.map((p) => {
            const active = p.key === props.page;
            const badge = badgeFor(p.key, badges);
            return (
              <button
                key={p.key}
                onClick={() => props.go(p.key)}
                title={PAGE_GROUP_LABEL[g.key] + " · " + p.label
                  + (p.key === "plan-map" || p.key === "plan-production"
                    ? "（工具面板在右侧）" : "")}
                className={
                  "relative flex flex-col items-center gap-1 whitespace-nowrap rounded-lg px-1 py-1.5 "
                  + T.note + " " +
                  (active
                    ? "bg-select font-semibold text-strong"
                    : "text-faint hover:bg-raised hover:text-dim")
                }
              >
                <span className="text-base leading-none">{p.icon}</span>
                <span className="leading-none">{p.label}</span>
                {badge && (
                  <span
                    className={
                      "absolute right-1 top-1 h-1.5 w-1.5 rounded-full " +
                      (badge.tone === "error"
                        ? "bg-[color:var(--err-fg)]"
                        : "bg-[color:var(--accent-yellow-fg)]")
                    }
                    title={badge.text}
                  />
                )}
              </button>
            );
          })}
        </Fragment>
      ))}
    </nav>
  );
}
