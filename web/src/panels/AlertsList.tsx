/**
 * 警报列表（复用组件）—— 概览「风险」面板与规划「前瞻警报」共用同一渲染。
 *
 * 两处数据是同一模型（后端 AlertView：severity / text_zh / eta，zh 文案后端拼，C4）；
 * 这里只渲染 —— 判定与文案都在后端（红线：前端不自己根据数字写告警）。
 * 规划警报的 kind（plan_stalled）不在帧契约的闭集里，所以 props 用结构化类型，
 * 不绑 contract 的 zod 枚举。
 */
import type { ReactNode } from "react";

export interface AlertItem {
  id: string;
  kind: string;
  severity: "info" | "warn" | "error";
  at: number;
  eta: number | null;
  text_zh: string;
}

const SEV_CLASS: Record<AlertItem["severity"], string> = {
  error: "text-[color:var(--err-fg)]",
  warn: "text-[color:var(--warn-fg)]",
  info: "text-blue-fg",
};

export function AlertsList(props: { alerts: AlertItem[]; empty?: ReactNode }) {
  if (props.alerts.length === 0) return <>{props.empty ?? null}</>;
  return (
    <ul className="space-y-1">
      {props.alerts.map((a) => (
        <li key={a.id + a.at}>
          <span className={SEV_CLASS[a.severity]}>●</span> {a.text_zh}
          {a.eta !== null && a.eta > 0 && (
            <span className="ml-1 text-note text-faint">（约 {a.eta}s 时）</span>
          )}
        </li>
      ))}
    </ul>
  );
}
