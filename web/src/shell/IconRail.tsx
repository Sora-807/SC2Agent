/**
 * 左侧导航栏（P1 重做）—— 按模式重组 + 加宽横排。
 *
 * - 分组是**模式的函数**（rail.ts：离线=规划工作台，实时/复盘=驾驶四页，诊断常驻）——
 *   用户拍板「离线编辑不应有驾驶栏、复盘不应有规划栏」，导航结构 = 协作流程的形状；
 * - 角标：待审批 N / 警报 N / 队首阻塞 —— 计数只读帧字段（shell/rail.ts，不本地推断）；
 * - 加宽（w-44）：图标 + 完整标签横排，规划/驾驶页名一眼可读（用户四轮反馈）。
 */
import { useFrames } from "../store/frames";
import { badgeFor, badgeCounters, railGroups } from "./rail";
import type { PageKey } from "./route";
import { T } from "./tokens";

export function IconRail(props: { page: PageKey; go: (p: PageKey) => void }) {
  const { mode, proposals, alerts, production } = useFrames();
  const groups = railGroups(mode);
  const badges = badgeCounters(proposals, alerts, production);

  return (
    <nav className="flex w-44 shrink-0 flex-col gap-2 border-r border-neutral-800 pr-1 pt-2">
      {groups.map((g) => (
        <div key={g.key} className="flex flex-col gap-0.5">
          <div className="px-2 text-note text-ghost">{g.label}</div>
          {g.items.map((p) => {
            const active = p.key === props.page;
            const badge = badgeFor(p.key, badges);
            return (
              <button
                key={p.key}
                onClick={() => props.go(p.key)}
                title={p.label}
                className={
                  "relative flex items-center gap-2 rounded px-2 py-1.5 " + T.note + " " +
                  (active
                    ? "bg-neutral-800 text-neutral-100"
                    : "text-faint hover:bg-neutral-900")
                }
              >
                <span className="w-4 shrink-0 text-center text-base leading-none">{p.icon}</span>
                <span>{p.label}</span>
                {badge && (
                  <span
                    className={
                      "ml-auto shrink-0 rounded px-1 leading-4 " +
                      (badge.tone === "error"
                        ? "bg-red-900/80 text-red-300"
                        : "bg-amber-900/80 text-amber-300")
                    }
                  >
                    {badge.text}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      ))}
    </nav>
  );
}
