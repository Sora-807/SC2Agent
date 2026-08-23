/**
 * 时间带（2026-08-22 时间轴下沉轮，I11 的落地形状）—— 一行细带放在主列顶部：
 * 复盘的对局记录选择 + 时间轴 + 回放控件（播放/暂停/回到实时）都在这，
 * 顶栏不再有它们的位置。离线（规划）模式整条不渲染 —— 规划不用时间轴。
 *
 * 二十七轮用户拍板：复盘源收敛成**一个下拉 = 对局记录**（时间 · 族 vs 族 · 地图）。
 * 之前的「夹具复盘 / 模拟 live / 后端 API 回放」三个一级选项 + 二级夹具全退役
 * —— 用户分不清它们是什么；夹具仍是规划模式的内部数据源，UI 不再露出。
 *
 * 可见性由 App 控制：复盘常驻；游戏模式只在会话活着时（没会话时主区是 StartCard）。
 */
import { Timeline } from "./Timeline";
import { useFrames } from "../store/frames";
import { fmtTime } from "./ui";
import { T } from "./tokens";

export function TimeStrip() {
  const {
    mode, timeline, range, caps, fixtures, fixtureKey, sourceKind,
    attach, returnToLive, play, pause,
  } = useFrames();
  const recordings = fixtures.filter((f) => f.key.startsWith("rec:"));

  return (
    <div className="flex shrink-0 items-center gap-2 border-b border-l1 bg-panel px-3 py-1">
      {mode === "replay" && (
        <select
          className="shrink-0 rounded border border-l2 bg-panel px-1 py-0.5 text-note"
          value={fixtureKey ?? ""}
          onChange={(e) => void attach("fixture", e.target.value)}
          title="回放过去的对局记录（游戏模式的会话会自动录制；结束后出现在这里）"
        >
          {recordings.length === 0 && (
            <option value="">还没有对局记录 —— 开一局会自动录制</option>
          )}
          {recordings.map((f) => (
            <option key={f.key} value={f.key}>{f.label}</option>
          ))}
        </select>
      )}

      <Timeline />

      <div className="flex shrink-0 items-center gap-1">
        {mode === "replay" && !caps.live && (
          <>
            <button className="btn btn-ghost" onClick={() => play(4)}>播放 ×4</button>
            <button className="btn btn-ghost" onClick={pause}>暂停</button>
          </>
        )}
        {caps.live && (
          <button
            className="btn btn-warn"
            disabled={timeline === "live"}
            onClick={returnToLive}
          >回到实时</button>
        )}
        <span className={"shrink-0 tabular-nums " + T.note + " text-faint"}
              title={!caps.seek && sourceKind !== "live" ? "该帧源不支持 seek" : undefined}>
          {fmtTime(range.from)}–{fmtTime(range.to)}
        </span>
      </div>
    </div>
  );
}
