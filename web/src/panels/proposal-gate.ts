/**
 * 审批门（§6 P2/P5）—— 从组件里抽出来的纯判定，便于单测。
 *
 * 三种"不能接受"必须给**不同的理由**，否则用户不知道该怎么办：
 * - 校验未通过 → 让 agent 改（内容仍然可见，P2）；
 * - 已失效     → 让 agent 基于当前状态重提（P5）；
 * - 已处理     → 什么都不用做。
 */
import type { Proposal } from "../contract";

export type Gate =
  | { ok: true }
  | { ok: false; why: "invalid" | "stale" | "settled" | "empty"; reason: string };

const SETTLED = new Set(["已接受", "部分接受", "已拒绝"]);

export function reviewGate(p: Proposal): Gate {
  if (SETTLED.has(p.status)) {
    return { ok: false, why: "settled", reason: `提案已处理（${p.status}）` };
  }
  if (p.status === "已失效") {
    return {
      ok: false, why: "stale",
      reason: "提案基于的世界已经不在了 —— 请让 agent 基于当前状态重提",
    };
  }
  if (p.validation?.ok !== true) {
    const errs = (p.validation?.errors ?? []).map((e) => e.text_zh).join("；");
    return {
      ok: false, why: "invalid",
      reason: "校验未通过，不可接受（内容保留可见，便于诊断与让 agent 学）" + (errs ? "：" + errs : ""),
    };
  }
  if (p.hunks.length === 0) {
    return { ok: false, why: "empty", reason: "这条提案没有任何改动" };
  }
  return { ok: true };
}

/** 拒绝必须附理由（§6 P3：理由回流给 agent，否则它会重复推同一个提案） */
export const canReject = (comment: string): boolean => comment.trim().length > 0;
