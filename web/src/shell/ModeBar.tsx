/**
 * 模式条（2026-08-22 十五轮：无边缘缝的外壳）—— 全宽顶栏，贴视口顶边，
 * 下方侧栏直接接在它的底边上（border-chrome 浅边分割，不再留缝）。
 *
 * 形状（用户十四轮拍板、十五轮修正）：【指示灯】游戏 | 复盘 | 规划，三个框，
 * 不加多余文字。蓝外围上的一枚白药丸分段控件；指示灯：绿+呼吸 = SC2 会话运行中、
 * 灰 = 没在跑、红 = 帧流断开。右缘只放**异常态**控件（平时不渲染任何东西）。
 */
import { MODE_META, MODE_ORDER } from "./mode";
import { useSessionStore } from "./session-store";
import { useFrames } from "../store/frames";
import { T } from "./tokens";

/** 指示灯三态（纯展示语义；绿=运行/灰=未运行/红=断流） */
type Light = "run" | "idle" | "down";

const LIGHT_STYLE: Record<Light, { cls: string; title: string }> = {
  run: { cls: "bg-[color:var(--ok-fg)] animate-pulse", title: "SC2 会话运行中" },
  idle: { cls: "bg-[color:var(--border-on-base)]", title: "没有运行中的会话" },
  down: { cls: "bg-[color:var(--err-fg)] animate-pulse", title: "帧流已断开" },
};

export function ModeBar() {
  const { mode, setMode, api, probe, disconnected, reconnect } = useFrames();
  const { info, opErr, stop, changeGameSpeed } = useSessionStore();
  const live = info?.alive === true;
  const hasSession = Boolean(info?.driver);
  // 仿真会话运行中：顶栏给一个即时变速（快进倍数）—— 不重启，改了就生效
  const fastRunning = live && info?.mode === "fast";

  const light: Light = disconnected ? "down" : live ? "run" : "idle";
  const ls = LIGHT_STYLE[light];

  return (
    <header className="flex shrink-0 items-center gap-2 border-b border-chrome bg-panel px-3 py-1.5">
      <i className={"h-2.5 w-2.5 shrink-0 rounded-full " + ls.cls} title={ls.title} />
      {/* 一级控件：白药丸三段（drive 在后端不在时仍可见但置灰带理由，G7） */}
      <div className="flex overflow-hidden rounded-lg border border-chrome bg-panel shadow-sm">
        {MODE_ORDER.map((m) => {
          const active = m === mode;
          const disabled = m === "drive" && !api.ok;
          return (
            <button
              key={m}
              disabled={disabled}
              title={disabled
                ? "游戏模式需要后端 API：先启动 python tools/serve_api.py"
                : MODE_META[m].tip}
              onClick={() => void setMode(m)}
              className={
                "px-3 py-1.5 " + T.label + " " +
                (active
                  ? "bg-select font-semibold text-strong"
                  : disabled
                    ? "cursor-not-allowed text-ghost"
                    : "text-dim hover:bg-raised hover:text-strong")
              }
            >
              {MODE_META[m].label}
            </button>
          );
        })}
      </div>

      {/* 右缘：只有异常态才出现（常态零元素） */}
      <div className="ml-auto flex items-center gap-2">
        {!api.ok && (
          <button
            className="btn btn-warn"
            title="游戏与后端会话都需要 python tools/serve_api.py 在跑；启动后点这里重连"
            onClick={probe}
          >后端未连接 · 重试</button>
        )}
        {api.ok && disconnected && (
          <button
            className="btn btn-danger"
            title="画面停留在断开前，继续操作会基于过期状态 —— 显式重连（不做自动重连）"
            onClick={() => void reconnect()}
          >帧流已断开 · 重连</button>
        )}
        {mode === "drive" && fastRunning && (
          <div className="flex overflow-hidden rounded-lg border border-chrome bg-panel"
               title="仿真模式快进倍数（即时生效，不重启）">
            {[["2", 2], ["4", 4], ["8", 8], ["最快", 0]].map(([label, v]) => (
              <button
                key={label}
                onClick={() => void changeGameSpeed(v as number)}
                className={"px-1.5 py-0.5 " + T.note + " "
                  + ((info?.speed ?? 0) === v
                     ? "bg-select font-semibold text-strong" : "text-dim hover:bg-raised")}
              >{label as string}{(label as string) !== "最快" ? "×" : ""}</button>
            ))}
          </div>
        )}
        {mode === "drive" && hasSession && (
          <button
            className="btn btn-ghost"
            title={info?.driver === "sc2"
              ? "结束会话 = 杀掉整棵子进程树（含 SC2 游戏进程）；对已崩溃的会话同样有效（清孤儿）"
              : "停止会话"}
            onClick={() => void stop()}
          >{live ? "结束会话" : "清理会话"}</button>
        )}
        {mode === "drive" && opErr && (
          <span
            className={"max-w-[420px] truncate rounded border border-[color:var(--err-fg)] bg-panel px-2 py-1 " + T.note + " text-[color:var(--err-fg)]"}
            title={opErr}
          >失败：{opErr}</span>
        )}
      </div>
    </header>
  );
}
