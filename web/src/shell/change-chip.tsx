/**
 * 改动按钮（用户 2026-08-22 拍板样式）：蓝框圆角、透明底、黑字，**与回复同宽**
 * 对齐不缺一块；点击跳到后端算好的 hash（不信 LLM 拼链接）。
 *
 * 2026-08-26 从 ChatDock 提取共享：评测 run 详情的「变更」区渲染同一份
 * ChangeRecord 归档（result.json 的 changes 与聊天轮内是同形状）。
 */
import { useFrames } from "../store/frames";
import type { ChatChange } from "../api/agent-chat";

export function ChangeChip({ c }: { c: ChatChange }) {
  return (
    <button
      onClick={() => {
        // 目标页可能不属于当前模式 —— 按目标前缀切模式，否则 App 的
        // 「页面不属于本模式」守卫会把它弹回模式首页（2026-08-24 用户报
        // 复盘页点规划 chip 跳不过去；此前只处理了对局方向这半边）
        if (c.target.startsWith("#/plan-")) {
          if (useFrames.getState().mode !== "offline") {
            void useFrames.getState().setMode("offline");
          }
        } else {
          void useFrames.getState().setMode("drive");
        }
        window.location.hash = c.target.replace(/^#/, "");
      }}
      title={"跳到" + c.target + "（agent 本轮" + (c.action === "open" ? "让你看" : "改") + "的东西）"}
      className="w-full rounded-lg border-[1.5px] border-accent-blue bg-transparent px-2 py-1 text-left text-note font-medium text-strong hover:bg-blue-soft"
    >
      {c.action === "add" ? "＋" : c.action === "open" ? "↗" : "✎"} {c.label}
    </button>
  );
}
