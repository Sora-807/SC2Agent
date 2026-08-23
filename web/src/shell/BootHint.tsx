/**
 * 真机首帧等待横幅（I6）—— 判定逻辑在 shell/mode.ts 的 bootHint（纯函数，可测），
 * 这里只负责订阅 store 与展示。数据（static/map）到达后自动消失。
 *
 * 之前地图页只有一句「等待 static/map…」，真机 1-2 分钟的空窗被实测误读为
 * 「地图没同步」—— 提示本身不是状态，是把后端真实节奏（首帧慢）告诉用户。
 */
import { useFrames } from "../store/frames";
import { bootHint } from "./mode";
import { T } from "./tokens";

export function BootHint(props: { className?: string }) {
  const mode = useFrames((s) => s.mode);
  const sourceKind = useFrames((s) => s.sourceKind);
  const sessionState = useFrames((s) => s.session?.state ?? null);
  const mapArrived = useFrames((s) => s.map != null);
  const hint = bootHint(mode, sourceKind, sessionState, mapArrived);
  if (!hint) return null;
  return (
    <div
      className={
        "flex items-center gap-2 rounded border border-accent-blue bg-blue-soft px-2 py-1 " +
        (props.className ?? "")
      }
    >
      <span className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-[color:var(--accent-blue-fg)]" />
      <span className={"text-blue-fg " + T.label}>{hint}</span>
    </div>
  );
}
