/**
 * 提案客户端（F7）—— agent 的产出面在前端这一侧。
 *
 * 关键：**接受/拒绝走后端的同一套校验**（§6 P4：不开 UI 后门）。
 * 前端只负责把三件事显示清楚：
 * 1. 校验不通过的提案**可见但不可接受**（P2）；
 * 2. 已失效的提案不能盲接受（P5）；
 * 3. 拒绝**必须**附理由，理由会回流给 agent（P3）。
 */
import { API_BASE } from "../store/frames";
import type { ProjectionFrame, Proposal } from "../contract";

export interface ProjectionPair {
  proposal_id: string;
  current: ProjectionFrame;
  proposed: ProjectionFrame;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(new URL(path, API_BASE).toString(), init);
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

export const listProposals = (): Promise<Proposal[]> => call("/api/proposals");

export const fetchPreview = (id: string, horizon = 120): Promise<ProjectionPair> =>
  call(`/api/proposals/${id}/preview?horizon=${horizon}`);

export const acceptProposal = (id: string, hunkIds?: string[], comment?: string): Promise<Proposal> =>
  call(`/api/proposals/${id}/accept`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ hunk_ids: hunkIds ?? null, comment_zh: comment ?? null }),
  });

export const rejectProposal = (id: string, comment: string): Promise<Proposal> =>
  call(`/api/proposals/${id}/reject`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ comment_zh: comment }),
  });

/** 让 agent 推一条提案（B10 之前先给人手动造样本用；agent 接上后走同一个入口） */
export const createProposal = (body: Record<string, unknown>): Promise<Proposal> =>
  call("/api/proposals", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
