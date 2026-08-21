/**
 * 会话条（F13 重做）—— 模式是一级控件，数据源退居二级。
 *
 * 三条纪律：
 * 1. 模式轴（U19）：离线编辑 / 实时驾驶 / 复盘，一个控件三个值 —— 模式→合法帧源的
 *    映射在 shell/mode.ts，这里不自算（干掉 SourceKind × fixtureKey 交叉积，根因 V）；
 * 2. 模式的视觉不可忽略：顶部 2px 色带 + 呼吸点/状态文字 —— 不看下拉就知道在线离线；
 * 3. 真机入口就在这里：实时驾驶模式的会话操作区（沙盒 sim / 真机 sc2 / 停止）。
 */
import { useEffect, useState } from "react";
import { fetchSessionInfo, sessionAction, type SessionInfo } from "../api/commands";
import { useFrames, type SourceKind } from "../store/frames";
import { MODE_META, allowedSources, type Mode } from "./mode";
import { Pill, fmtTime } from "./ui";
import { T } from "./tokens";

const MODE_ORDER: Mode[] = ["offline", "drive", "replay"];

const SOURCE_LABEL: Record<SourceKind, string> = {
  fixture: "夹具复盘（可 seek）",
  "mock-live": "模拟 live（环形缓冲回看）",
  api: "后端 API 回放",
  live: "live 会话",
};

export function SessionBar() {
  const {
    mode, timeline, setMode, probe,
    fixtures, fixtureKey, sourceKind, attach,
    session, caps, position, api,
    returnToLive, play, pause,
  } = useFrames();

  // 会话描述轮询（只在 drive 模式）：driver/alive 驱动按钮拦截，防止重复启动 SC2。
  // 后端也有幂等守卫（同 driver 重复 start 不重启），前端拦截是第一道、后端是兜底。
  const [sessInfo, setSessInfo] = useState<SessionInfo | null>(null);
  useEffect(() => {
    if (mode !== "drive" || !api.ok) {
      setSessInfo(null);
      return;
    }
    let stopped = false;
    const poll = async (): Promise<void> => {
      const s = await fetchSessionInfo();
      if (!stopped) setSessInfo(s);
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
  const startSession = async (driver: "sim" | "sc2"): Promise<void> => {
    if (driver === "sc2"
        && !window.confirm("启动真机会话？这会打开一个 SC2 游戏进程，停止会话前别手动关它。")) {
      return;
    }
    if (live && liveDriver !== null && liveDriver !== driver
        && !window.confirm(`当前有${liveDriver === "sc2" ? "真机" : "沙盒"}会话在跑，换会话会先停止它。继续？`)) {
      return;
    }
    await sessionAction("start", { driver });
    await setMode("drive");
    await attach("live", "live");
  };

  const meta = MODE_META[mode];
  const sources = allowedSources(mode, api.ok);
  const reviewing = timeline === "review";

  return (
    <header className="border-b border-neutral-800">
      {/* 模式色带：F13(b) 的「视觉不可忽略」—— 2px 高，全宽 */}
      <div className={"h-0.5 rounded " + meta.band} />

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
                onClick={async () => {
                  if (m !== "drive") {
                    await setMode(m);
                    return;
                  }
                  // drive：先看后端有没有活跃会话 —— 没有就**不 attach**（连了必失败，
                  // 全屏错误屏就是之前「点实时驾驶黑屏」的来源），进模式等用户启动会话
                  const info = await fetchSessionInfo();
                  if (info?.alive) {
                    await setMode("drive");
                    setSessInfo(info);
                    return;
                  }
                  useFrames.setState({ mode: "drive", error: null });
                  setSessInfo(info);
                }}
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
          <select
            className="rounded border border-neutral-700 bg-neutral-900 px-2 py-1 text-xs"
            value={fixtureKey ?? ""}
            onChange={(e) => void attach("fixture", e.target.value)}
          >
            {fixtures.map((f) => (
              <option key={f.key} value={f.key}>{f.label}</option>
            ))}
          </select>
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
            <button
              className="rounded border border-emerald-800 bg-emerald-900/30 px-2 py-1 text-xs disabled:opacity-40"
              disabled={live && liveDriver === "sim"}
              title={live && liveDriver === "sim"
                ? "沙盒会话已在运行（后端也会拒绝重复启动）"
                : "在后端起一个离线沙盒会话（真引擎 + 假世界），起来后可以下命令"}
              onClick={() => void startSession("sim")}
            >{live && liveDriver === "sim" ? "沙盒运行中" : "启动沙盒"}</button>
            <button
              className="rounded border border-red-900 bg-red-900/30 px-2 py-1 text-xs disabled:opacity-40"
              disabled={live && liveDriver === "sc2"}
              title={live && liveDriver === "sc2"
                ? "真机会话已在运行：一个会话 = 一个 SC2 游戏进程，不允许多开（后端也会拒绝）"
                : "连真实 SC2：会启动一个 SC2 游戏进程（结束后记得停止会话）"}
              onClick={() => void startSession("sc2")}
            >{live && liveDriver === "sc2" ? "真机已启动" : "启动真机（SC2）"}</button>
            <button
              className="rounded border border-neutral-700 px-2 py-1 text-xs disabled:opacity-40"
              disabled={!live}
              title="停止会话 = 杀掉整棵子进程树（含 SC2 游戏进程）"
              onClick={async () => {
                await sessionAction("stop");
                setSessInfo(await fetchSessionInfo());
              }}
            >停止会话</button>
            {sessInfo && (
              <span className={T.note + " text-faint"}>
                {sessInfo.label ?? sessInfo.state}
                {sessInfo.error ? ` · ${sessInfo.error}` : ""}
              </span>
            )}
          </div>
        )}

        <Pill label="状态" value={session?.state ?? "—"}
              tone={session?.state === "对局中" ? "live" : "normal"} />
        <Pill label="地图" value={session?.map_name ?? "—"} />
        <Pill label="游戏时间" value={fmtTime(position)} />

        {/* 模式状态语（chrome 的文字半）：一句话说清「现在在哪、能干什么」 */}
        <span className={"flex items-center gap-1.5 " + meta.text}>
          <i className={"inline-block h-2 w-2 rounded-full " + meta.dot} />
          {mode === "replay" || (mode === "drive" && reviewing)
            ? `只读回看 ${fmtTime(position)}`
            : mode === "drive" && !live
              ? "等待会话：点「启动沙盒 / 启动真机」后自动接入"
              : meta.tip}
        </span>

        <div className="ml-auto flex gap-2">
          {caps.live ? (
            <button
              className="rounded border border-amber-700 bg-amber-900/30 px-2 py-1 text-xs disabled:opacity-40"
              disabled={timeline === "live"}
              onClick={returnToLive}
            >回到实时</button>
          ) : (
            <>
              <button className="rounded border border-neutral-700 px-2 py-1 text-xs"
                      onClick={() => play(4)}>播放 ×4</button>
              <button className="rounded border border-neutral-700 px-2 py-1 text-xs"
                      onClick={pause}>暂停</button>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
