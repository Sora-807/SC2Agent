/**
 * 会话条（F13 重做；2026-08-21 实时驾驶整改轮再收敛）—— 模式是一级控件，数据源退居二级。
 *
 * 三条纪律：
 * 1. 模式轴（U19）：离线编辑 / 实时驾驶 / 复盘，一个控件三个值 —— 模式→合法帧源的
 *    映射在 shell/mode.ts，这里不自算（干掉 SourceKind × fixtureKey 交叉积，根因 V）；
 * 2. 模式的视觉不可忽略：顶部 2px 色带 + 呼吸点/状态文字 —— 不看下拉就知道在线离线；
 * 3. 真机入口就在这里：实时驾驶模式的会话操作区（启动真机 / 关闭真机）。
 *
 * 本轮收敛（用户实测反馈）：
 * - 删「启动沙盒」：假世界数据曾把「SC2 没启动」伪装成「连上了但地图空 + 来历不明的
 *   槽位」；sim 驱动保留在后端（测试/agent 走 REST），UI 不再给入口；
 * - 删状态/地图/游戏时间三个 Pill：状态由会话 label 给、游戏时间时间线上有；
 * - 播放×4/暂停只属于复盘（回放语义）；驾驶态只有「回到实时」；
 * - 启动失败必须显形（后端 400 曾被吞成 null = 「点了没反应」）；
 * - 真机启动用两段式确认，不用 window.confirm（嵌入式浏览器可能直接拦掉 = 静默无效）。
 */
import { useEffect, useState } from "react";
import { fetchSessionInfo, sessionAction, type SessionInfo } from "../api/commands";
import { listMapPlans } from "../api/map-plans";
import { useFrames, type SourceKind } from "../store/frames";
import { MODE_META, allowedSources, pickMapPlan, type Mode } from "./mode";
import { fmtTime } from "./ui";
import { T } from "./tokens";

const MODE_ORDER: Mode[] = ["offline", "drive", "replay"];

const SOURCE_LABEL: Record<SourceKind, string> = {
  fixture: "夹具复盘（可 seek）",
  "mock-live": "模拟 live（环形缓冲回看）",
  api: "后端 API 回放",
  live: "live 会话",
};

/** 两段式确认的回落时间（毫秒）：点一次变「确认」，超时自动还原 */
const CONFIRM_RESET_MS = 4000;

export function SessionBar() {
  const {
    mode, timeline, setMode, probe,
    fixtures, fixtureKey, sourceKind, attach,
    caps, position, api,
    returnToLive, play, pause,
    disconnected, reconnect,
  } = useFrames();

  // 会话描述轮询（只在 drive 模式）：driver/alive 驱动按钮拦截，防止重复启动 SC2。
  // 后端也有幂等守卫（同 driver 重复 start 不重启），前端拦截是第一道、后端是兜底。
  const [sessInfo, setSessInfo] = useState<SessionInfo | null>(null);
  /** 会话装配用的地图规划（进入游戏加载哪一份；P2 切片 1） */
  const [mapPlans, setMapPlans] = useState<{ id: string; title_zh: string; locked: boolean }[] | null>(null);
  /** null = 清单还没到；到达后经 pickMapPlan 兜底（旧默认值 "default" 已随预设改名
   *  退役，发不存在的 id 会被后端 400 —— 曾是「点启动真机没反应」的根因） */
  const [mapPlanId, setMapPlanId] = useState<string | null>(null);
  /** 启动/停止失败的原因（后端 detail 原文；会话活过来时自动清） */
  const [opErr, setOpErr] = useState<string | null>(null);
  /** 真机两段式确认：第一点变「确认启动」，再点才真启动 */
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (mode !== "drive" || !api.ok) return;
    void listMapPlans()
      .then((rows) => {
        const list = rows.map((r) => ({ id: r.id, title_zh: r.title_zh, locked: r.locked }));
        setMapPlans(list);
        setMapPlanId((cur) => pickMapPlan(list, cur));
      })
      .catch(() => setMapPlans(null));
  }, [mode, api.ok]);

  useEffect(() => {
    if (mode !== "drive" || !api.ok) {
      setSessInfo(null);
      return;
    }
    let stopped = false;
    const poll = async (): Promise<void> => {
      const s = await fetchSessionInfo();
      if (stopped) return;
      setSessInfo(s);
      if (s?.alive) setOpErr(null);      // 会话活着 = 上一次失败已经翻篇
    };
    void poll();
    const t = setInterval(() => void poll(), 2000);
    return () => {
      stopped = true;
      clearInterval(t);
    };
  }, [mode, api.ok]);

  const live = sessInfo?.alive === true;
  const liveDriver = sessInfo?.driver ?? null;
  const startSession = async (driver: "sc2"): Promise<void> => {
    setOpErr(null);
    if (!confirming) {
      setConfirming(true);
      window.setTimeout(() => setConfirming(false), CONFIRM_RESET_MS);
      return;
    }
    setConfirming(false);
    // 发送前兜底（pickMapPlan）：规划 id 必须在清单里，不在就不带（后端用出厂模板）
    const planId = mapPlans ? pickMapPlan(mapPlans, mapPlanId) ?? undefined : undefined;
    const r = await sessionAction("start", { driver, mapPlan: planId });
    if (!r.ok) {
      setOpErr(r.detail);
      setSessInfo(await fetchSessionInfo());
      return;
    }
    setSessInfo(await fetchSessionInfo());
    // WS 在无会话时也保持连接（后端合成「未连接」帧），会话起来后同一连接自动接上
    await setMode("drive");
    if (useFrames.getState().sourceKind !== "live") await attach("live", "live");
  };
  const stopSession = async (): Promise<void> => {
    setOpErr(null);
    setConfirming(false);
    const r = await sessionAction("stop");
    if (!r.ok) setOpErr(r.detail);
    setSessInfo(await fetchSessionInfo());
  };

  const meta = MODE_META[mode];
  const sources = allowedSources(mode, api.ok);
  const reviewing = timeline === "review";

  return (
    <header className="border-b border-neutral-800">
      {/* 模式色带：F13(b) 的「视觉不可忽略」—— 2px 高，全宽 */}
      <div className={"h-0.5 rounded " + meta.band} />

      {/* WS 断线横幅（2026-08-21）：之前零处理 = 驾驶舱静默冻结在最后一帧，
          用户被过期的画面误导。显眼横幅 + 手动重连（不做自动重连，语义显式）。 */}
      {disconnected && (
        <div className="mt-1 flex items-center gap-2 rounded border border-red-800 bg-red-950/50 px-2 py-1">
          <span className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-red-400" />
          <span className={"text-red-300 " + T.label}>
            帧流已断开（后端可能已停止或崩溃）· 画面停留在断开前，继续操作会基于过期状态
          </span>
          <button
            className="ml-auto shrink-0 rounded border border-red-700 px-2 py-0.5 text-red-200 hover:bg-red-900/40"
            onClick={() => void reconnect()}
          >重连</button>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 pb-2 pt-1">
        <span className="text-base font-bold">sc2Agent 驾驶舱</span>

        {/* 一级控件：模式轴（三值；drive 在后端不在时仍可见但置灰带理由，G7） */}
        <div className="flex overflow-hidden rounded border border-neutral-700">
          {MODE_ORDER.map((m) => {
            const active = m === mode;
            const disabled = m === "drive" && !api.ok;
            return (
              <button
                key={m}
                disabled={disabled}
                title={disabled
                  ? "实时驾驶需要后端 API：先启动 python tools/serve_api.py"
                  : MODE_META[m].tip}
                onClick={() => void setMode(m)}
                className={
                  "px-2.5 py-1 " + T.label + " " +
                  (active
                    ? "bg-neutral-700 text-neutral-100"
                    : disabled
                      ? "cursor-not-allowed text-ghost"
                      : "text-dim hover:bg-neutral-800")
                }
              >
                {MODE_META[m].label}
              </button>
            );
          })}
        </div>

        {/* 后端未连接：可见的重连入口（G7：不静默藏起来；用户可能后启动 serve_api） */}
        {!api.ok && (
          <button
            className="rounded border border-amber-800 bg-amber-950/40 px-2 py-1 text-xs"
            title="实时驾驶与后端会话都需要 python tools/serve_api.py 在跑；启动后点这里重连"
            onClick={probe}
          >后端未连接 · 重试</button>
        )}

        {/* 二级控件：随模式变化的数据源 / 会话操作 */}
        {mode === "offline" && (
          <>
            <span className="text-note text-ghost" title="离线规划的静态面（catalog/地图/地形）来自这个夹具；规划本身存在后端文件">背景数据</span>
            <select
              className="rounded border border-neutral-700 bg-neutral-900 px-2 py-1 text-xs"
              value={fixtureKey ?? ""}
              onChange={(e) => void attach("fixture", e.target.value)}
            >
              {fixtures.map((f) => (
                <option key={f.key} value={f.key}>{f.label}</option>
              ))}
            </select>
          </>
        )}

        {mode === "replay" && (
          <>
            <select
              className="rounded border border-neutral-700 bg-neutral-900 px-2 py-1 text-xs"
              value={sources.includes(sourceKind) ? sourceKind : sources[0] ?? "fixture"}
              onChange={(e) =>
                fixtureKey && void attach(e.target.value as SourceKind, fixtureKey)}
              title="复盘源可任意 seek；模拟 live 只能靠环形缓冲回看"
            >
              {sources.map((k) => (
                <option key={k} value={k}>{SOURCE_LABEL[k]}</option>
              ))}
            </select>
            {sourceKind !== "api" && (
              <select
                className="rounded border border-neutral-700 bg-neutral-900 px-2 py-1 text-xs"
                value={fixtureKey ?? ""}
                onChange={(e) => void attach(sourceKind, e.target.value)}
              >
                {fixtures.map((f) => (
                  <option key={f.key} value={f.key}>{f.label}</option>
                ))}
              </select>
            )}
          </>
        )}

        {mode === "drive" && (
          <div className="flex items-center gap-2">
            {mapPlans && mapPlans.length > 0 && (
              <select
                value={mapPlanId ?? ""}
                onChange={(e) => setMapPlanId(e.target.value)}
                disabled={live}
                title={live ? "会话已启动（规划在启动时装配）" : "会话装配加载这份地图规划（槽位/点位）"}
                className="rounded border border-neutral-700 bg-neutral-900 px-2 py-1 text-xs disabled:opacity-50">
                {mapPlans.map((p) => (
                  <option key={p.id} value={p.id}>
                    地图 · {p.title_zh}{p.locked ? "（默认）" : ""}
                  </option>
                ))}
              </select>
            )}
            <button
              className={
                "rounded border px-2 py-1 text-xs disabled:opacity-40 " +
                (confirming
                  ? "border-amber-600 bg-amber-900/40 text-amber-200"
                  : "border-red-900 bg-red-900/30")}
              disabled={live && liveDriver === "sc2"}
              title={live && liveDriver === "sc2"
                ? "真机会话已在运行：一个会话 = 一个 SC2 游戏进程，不允许多开（后端也会拒绝）"
                : confirming
                  ? "再点一次才会启动（4 秒内不点自动还原）"
                  : "连真实 SC2：会启动一个 SC2 游戏进程（结束后用「关闭真机」收尾）"}
              onClick={() => void startSession("sc2")}
            >{live && liveDriver === "sc2"
                ? "真机运行中"
                : confirming ? "再点一次 · 确认启动 SC2" : "启动真机（SC2）"}</button>
            {/* 关闭按钮：会话存在即可点（不限 alive）—— 子进程死了 SC2 可能还挂着，
                这时恰恰是最需要点它的时候（树杀兜底）；stop 对死会话幂等无害 */}
            {sessInfo && liveDriver && (
              <button
                className="rounded border border-neutral-700 px-2 py-1 text-xs"
                title={liveDriver === "sc2"
                  ? "关闭真机 = 杀掉整棵子进程树（含 SC2 游戏进程）；对已崩溃的会话同样有效（清孤儿）"
                  : "停止沙盒会话"}
                onClick={() => void stopSession()}
              >{liveDriver === "sc2" ? "关闭真机" : "停止沙盒"}</button>
            )}
            {sessInfo && (
              <span className={T.note + " max-w-[360px] truncate text-faint"}
                    title={[sessInfo.label ?? sessInfo.state, sessInfo.error].filter(Boolean).join(" · ")}>
                {sessInfo.label ?? sessInfo.state}
                {sessInfo.error ? ` · ${sessInfo.error}` : ""}
              </span>
            )}
            {opErr && (
              <span
                className={"max-w-[420px] truncate rounded border border-red-800 bg-red-950/50 px-2 py-1 " + T.note + " text-red-300"}
                title={opErr}
              >失败：{opErr}</span>
            )}
          </div>
        )}

        {/* 模式状态语（chrome 的文字半）：一句话说清「现在在哪、能干什么」 */}
        <span className={"flex items-center gap-1.5 " + meta.text}>
          <i className={"inline-block h-2 w-2 rounded-full " + meta.dot} />
          {mode === "replay" || (mode === "drive" && reviewing)
            ? `只读回看 ${fmtTime(position)}`
            : mode === "drive" && !live
              ? "等待会话：点「启动真机」后自动接入"
              : meta.tip}
        </span>

        <div className="ml-auto flex gap-2">
          {mode === "replay" && !caps.live && (
            <>
              <button className="rounded border border-neutral-700 px-2 py-1 text-xs"
                      onClick={() => play(4)}>播放 ×4</button>
              <button className="rounded border border-neutral-700 px-2 py-1 text-xs"
                      onClick={pause}>暂停</button>
            </>
          )}
          {caps.live && (
            <button
              className="rounded border border-amber-700 bg-amber-900/30 px-2 py-1 text-xs disabled:opacity-40"
              disabled={timeline === "live"}
              onClick={returnToLive}
            >回到实时</button>
          )}
        </div>
      </div>
    </header>
  );
}
