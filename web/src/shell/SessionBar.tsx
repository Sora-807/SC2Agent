/** 会话条：帧源选择 + 会话状态 + 回看/回到实时 */
import { useFrames, type SourceKind } from "../store/frames";
import { Pill, fmtTime } from "./ui";

export function SessionBar() {
  const {
    fixtures, fixtureKey, sourceKind, session, mode, caps, position, api,
    attach, returnToLive, play, pause,
  } = useFrames();

  return (
    <header className="flex flex-wrap items-center gap-2 border-b border-neutral-800 pb-2">
      <span className="text-base font-bold">sc2Agent 驾驶舱</span>

      <select
        className="rounded border border-neutral-700 bg-neutral-900 px-2 py-1 text-xs"
        value={fixtureKey ?? ""}
        onChange={(e) => void attach(sourceKind, e.target.value)}
      >
        {fixtures.map((f) => (
          <option key={f.key} value={f.key}>{f.label}</option>
        ))}
      </select>

      <select
        className="rounded border border-neutral-700 bg-neutral-900 px-2 py-1 text-xs"
        value={sourceKind}
        onChange={(e) => fixtureKey && void attach(e.target.value as SourceKind, fixtureKey)}
        title="复盘=可任意 seek；模拟 live=只能靠环形缓冲回看，右端持续生长"
      >
        <option value="fixture">帧源：本地复盘（可 seek）</option>
        <option value="mock-live">帧源：模拟 live（环形缓冲回看）</option>
        <option value="api" disabled={!api.ok}>
          {api.ok ? "帧源：后端 API rev " + api.rev : "帧源：后端 API（未连接）"}
        </option>
      </select>

      <Pill label="状态" value={session?.state ?? "—"} tone={session?.state === "对局中" ? "live" : "normal"} />
      <Pill label="地图" value={session?.map_name ?? "—"} />
      <Pill label="种族" value={session ? `${session.my_race ?? "?"} vs ${session.enemy_race ?? "?"}` : "—"} />
      <Pill label="游戏时间" value={fmtTime(position)} />
      {caps.live && (
        <Pill label="" value={mode === "review" ? "只读回看中" : "跟随实时"} tone={mode === "review" ? "warn" : "live"} />
      )}

      <div className="ml-auto flex gap-2">
        {caps.live ? (
          <button
            className="rounded border border-amber-700 bg-amber-900/30 px-2 py-1 text-xs disabled:opacity-40"
            disabled={mode === "live"}
            onClick={returnToLive}
          >回到实时</button>
        ) : (
          <>
            <button className="rounded border border-neutral-700 px-2 py-1 text-xs" onClick={() => play(4)}>播放 ×4</button>
            <button className="rounded border border-neutral-700 px-2 py-1 text-xs" onClick={pause}>暂停</button>
          </>
        )}
      </div>
    </header>
  );
}