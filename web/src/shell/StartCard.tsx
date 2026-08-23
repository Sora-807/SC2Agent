/**
 * 会话启动卡（2026-08-22 顶栏极简轮）—— 「启动真机」从顶栏退役后的新家：
 * 游戏模式 + 没有活会话时占据主区（没有会话就没有可看的驾驶面，这里就是游戏模式的首页）。
 *
 * 会话状态/地图规划清单/两段式确认都在 session-store（与 ModeBar 共享同一份轮询）。
 */
import { useSessionStore } from "./session-store";
import { useFrames } from "../store/frames";
import { Card } from "./ui";
import { T } from "./tokens";

export function StartCard() {
  const { api, probe } = useFrames();
  const { info, mapPlans, mapPlanId, setMapPlanId,
          strategies, strategyId, setStrategyId, confirming, start } = useSessionStore();

  if (!api.ok) {
    return (
      <div className="flex h-full items-center justify-center">
        <Card title="游戏" className="w-[420px]">
          <div className={"mb-2 " + T.body + " text-dim"}>
            游戏模式需要后端 API（python tools/serve_api.py）。启动后点重连。
          </div>
          <button className="btn btn-warn" onClick={probe}>重试连接</button>
        </Card>
      </div>
    );
  }

  const stateLine = [info?.label ?? info?.state, info?.error]
    .filter(Boolean).join(" · ");

  return (
    <div className="flex h-full items-center justify-center">
      <Card title="启动游戏" className="w-[420px]">
        <div className={"mb-3 " + T.note + " text-faint"}>
          一个会话 = 一个 SC2 游戏进程（不允许多开，后端也会拒绝）。
          启动后自动接入驾驶画面；从「启动」到首帧约需 1-2 分钟。
        </div>

        {mapPlans && mapPlans.length > 0 && (
          <label className="mb-3 flex items-center gap-2">
            <span className={T.label + " text-dim"}>地图规划</span>
            <select
              value={mapPlanId ?? ""}
              onChange={(e) => setMapPlanId(e.target.value)}
              className="min-w-0 flex-1 rounded border border-l2 bg-inset px-2 py-1 text-label"
              title="会话装配加载这份地图规划（槽位/点位）"
            >
              {mapPlans.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.title_zh}{p.locked ? "（默认）" : ""}
                </option>
              ))}
            </select>
          </label>
        )}

        {strategies && strategies.length > 0 && (
          <label className="mb-3 flex items-center gap-2">
            <span className={T.label + " text-dim"}>策略</span>
            <select
              value={strategyId ?? ""}
              onChange={(e) => setStrategyId(e.target.value)}
              className="min-w-0 flex-1 rounded border border-l2 bg-inset px-2 py-1 text-label"
              title="会话启动时装配这份策略文件（策略在会话间不热改；agent 写的策略也在这里选）"
            >
              <option value="">内置默认（集结推进）</option>
              {strategies.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.title_zh}{s.locked ? "（内置）" : ""}
                </option>
              ))}
            </select>
          </label>
        )}

        <button
          className={
            "w-full justify-center rounded border px-2 py-1.5 " + T.label + " " +
            (confirming
              ? "border-[color:var(--warn-fg)] bg-[color:var(--warn-bg)] font-medium text-[color:var(--warn-fg)]"
              : "border-[color:var(--err-fg)] bg-[color:var(--err-bg)] text-[color:var(--err-fg)]")
          }
          title={confirming
            ? "再点一次才会启动（4 秒内不点自动还原）"
            : "连真实 SC2：会启动一个 SC2 游戏进程（结束后用顶栏「结束会话」收尾）"}
          onClick={() => void start()}
        >
          {confirming ? "再点一次 · 确认启动 SC2" : "启动真机（SC2）"}
        </button>

        {stateLine && (
          <div className={"mt-2 truncate " + T.note + " text-faint"} title={stateLine}>
            会话：{stateLine}
          </div>
        )}
      </Card>
    </div>
  );
}
