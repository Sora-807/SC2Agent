/**
 * 左侧图标栏（F13c 重做）—— 分组 + 角标 + 模式门控。
 *
 * - 分组：驾驶 / 规划 / 诊断，带分组标题（功能找得到）；
 * - 角标：待审批 N / 警报 N / 队首阻塞 —— 计数只读帧字段（shell/rail.ts，不本地推断）；
 * - 门控：drive 模式下规划组置灰 + tooltip 引 R5（G7：禁用必带理由，禁止静默隐藏）。
 */
import { useFrames } from "../store/frames";
import { badgeFor, badgeCounters, railGroups } from "./rail";
import type { PageKey } from "./route";
import { C, T } from "./tokens";

export function IconRail(props: { page: PageKey; go: (p: PageKey) => void }) {
  const { mode, proposals, alerts, production } = useFrames();
  const groups = railGroups(mode);
  const badges = badgeCounters(proposals, alerts, production);

  return (
    <nav className="flex w-16 shrink-0 flex-col gap-2 border-r border-neutral-800 pr-1 pt-2">
      {groups.map((g) => (
        <div key={g.key} className="flex flex-col gap-0.5">
          <div className={"px-1 " + C.ghost}>{g.label}</div>
          {g.items.map((p) => {
            const active = p.key === props.page;
            const badge = badgeFor(p.key, badges);
            return (
              <button
                key={p.key}
                onClick={() => !g.gated && props.go(p.key)}
                disabled={g.gated}
                title={g.gated ? g.gateReason ?? "" : p.label}
                className={
                  "relative flex flex-col items-center rounded py-1.5 " + T.note + " " +
                  (active
                    ? "bg-neutral-800 text-neutral-100"
                    : g.gated
                      ? "cursor-not-allowed text-ghost"
                      : "text-faint hover:bg-neutral-900")
                }
              >
                <span className="text-base leading-none">{p.icon}</span>
                <span className="mt-0.5">{p.label}</span>
                {badge && (
                  <span
                    className={
                      "absolute right-0.5 top-0.5 rounded px-1 leading-3 " +
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
