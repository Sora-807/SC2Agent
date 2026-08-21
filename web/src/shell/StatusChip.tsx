/**
 * 状态浮层（F10）—— 取代原先每页常驻的页脚。
 *
 * 原页脚硬编码「契约 ViewFrame v0.1（rev=1）」，而两侧 REV 早已是 9（根因 Y）：
 * 在驾驶舱的黄金位置常驻一条**错误信息**，还占掉一行高度。
 * 这里改成右下角一枚小徽标 + 点开的浮层，rev 读 `contract` 的**真值**并与后端比对 ——
 * 版本不符是最值得当场看见的事（架构不变量 A1）。
 */
import { useState } from "react";
import { REV } from "../contract";
import { useFrames } from "../store/frames";

export function StatusChip() {
  const [open, setOpen] = useState(false);
  const api = useFrames((s) => s.api);
  const sourceKind = useFrames((s) => s.sourceKind);
  const seq = useFrames((s) => s.seq);

  // 后端在线且 rev 与前端不一致 = 契约漂移，必须显眼（不静默）
  const drift = api.ok && api.rev !== undefined && api.rev !== REV;

  return (
    <div className="pointer-events-none absolute bottom-2 right-2 z-20 flex flex-col items-end gap-1">
      {open && (
        <div className="pointer-events-auto w-80 rounded border border-neutral-700 bg-neutral-950/95 p-2 text-[11px] leading-relaxed text-neutral-400 shadow-lg">
          <div className="mb-1 font-medium text-neutral-200">契约与来源</div>
          <ul className="space-y-0.5">
            <li>
              前端契约 rev <span className="text-neutral-200">{REV}</span>
              {api.ok ? (
                <>
                  {" · "}后端 rev{" "}
                  <span className={drift ? "text-red-400" : "text-neutral-200"}>{api.rev ?? "—"}</span>
                </>
              ) : (
                <span className="ml-1 text-neutral-600">（后端未连接）</span>
              )}
            </li>
            <li>帧源 <span className="text-neutral-200">{sourceKind}</span> · 当前 seq {seq}</li>
            <li className="border-t border-neutral-800 pt-1">
              唯一真相源 <code>docs/plan-frontend.md</code> §2；面板只读帧字段，
              未做任何本地派生（红线 C7）
            </li>
          </ul>
          {drift && (
            <div className="mt-1 rounded border border-red-800 bg-red-950/40 p-1 text-red-300">
              契约版本不符：前端 {REV} vs 后端 {api.rev}。改契约要 REV+1 且两侧同步（红线 C8）。
            </div>
          )}
        </div>
      )}
      <button
        className={
          "pointer-events-auto rounded border px-1.5 py-0.5 text-[10px] tabular-nums " +
          (drift
            ? "border-red-700 bg-red-950/60 text-red-300"
            : "border-neutral-800 bg-neutral-900/80 text-neutral-500 hover:text-neutral-300")
        }
        title="契约版本与帧源"
        onClick={() => setOpen((v) => !v)}
      >
        {drift ? "rev 不符" : "rev " + REV}
      </button>
    </div>
  );
}
