/**
 * 会话条（F13 重做）—— 模式是一级控件，数据源退居二级。
 *
 * 三条纪律：
 * 1. 模式轴（U19）：离线编辑 / 实时驾驶 / 复盘，一个控件三个值 —— 模式→合法帧源的
 *    映射在 shell/mode.ts，这里不自算（干掉 SourceKind × fixtureKey 交叉积，根因 V）；
 * 2. 模式的视觉不可忽略：顶部 2px 色带 + 呼吸点/状态文字 —— 不看下拉就知道在线离线；
 * 3. 真机入口就在这里：实时驾驶模式的会话操作区（沙盒 sim / 真机 sc2 / 停止）。
 */
import { sessionAction } from "../api/commands";
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
    mode, timeline, setMode,
    fixtures, fixtureKey, sourceKind, attach,
    session, caps, position, api,
    returnToLive, play, pause,
  } = useFrames();

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
          <div className="flex gap-2">
            <button
              className="rounded border border-emerald-800 bg-emerald-900/30 px-2 py-1 text-xs"
              title="在后端起一个离线沙盒会话（真引擎 + 假世界），起来后可以下命令"
              onClick={async () => {
                await sessionAction("start", { driver: "sim" });
                await setMode("drive");
                await attach("live", "live");
              }}
            >启动沙盒</button>
            <button
              className="rounded border border-red-900 bg-red-900/30 px-2 py-1 text-xs"
              title="连真实 SC2：会启动一个 SC2 游戏进程（结束后记得停止会话）"
              onClick={async () => {
                if (!window.confirm("启动真机会话？这会打开一个 SC2 游戏进程，停止会话前别手动关它。")) {
                  return;
                }
                await sessionAction("start", { driver: "sc2" });
                await setMode("drive");
                await attach("live", "live");
              }}
            >启动真机（SC2）</button>
            <button
              className="rounded border border-neutral-700 px-2 py-1 text-xs"
              onClick={async () => {
                await sessionAction("stop");
              }}
            >停止会话</button>
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
