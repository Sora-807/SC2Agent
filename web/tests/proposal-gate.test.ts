/**
 * 审批门（§6 P2/P5）：三种"不能接受"必须给**不同的理由**。
 * 混成一句「不能接受」的话，用户不知道是该催 agent 改、还是该让它重提。
 */
import { describe, expect, it } from "vitest";
import { canReject, reviewGate } from "../src/panels/proposal-gate";
import type { Proposal } from "../src/contract";

const base: Proposal = {
  id: "p1", author: "agent", created_at: 10,
  anchor: { seq: 10, game_time: 10 },
  kind: "production_queue", title_zh: "t", rationale_zh: "r",
  target: {}, hunks: [{ id: "h1", kind: "reorder", text_zh: "x", payload: {} }],
  validation: { ok: true, errors: [] },
  preview: { kind: "projection_pair", current_ref: "a", proposed_ref: "b" },
  status: "待审批", decision: null,
};

describe("reviewGate", () => {
  it("校验通过 + 待审批 + 有改动 → 可接受", () => {
    expect(reviewGate(base)).toEqual({ ok: true });
  });

  it("P2 校验未通过 → 不可接受，理由里带上后端给的错误", () => {
    const g = reviewGate({
      ...base,
      validation: { ok: false, errors: [{ hunk_id: null, text_zh: "delete 下标 9 越界" }] },
    });
    expect(g.ok).toBe(false);
    if (!g.ok) {
      expect(g.why).toBe("invalid");
      expect(g.reason).toContain("越界");
      expect(g.reason).toContain("保留可见");
    }
  });

  it("P5 已失效 → 理由是「让 agent 基于当前状态重提」，而不是「校验失败」", () => {
    const g = reviewGate({ ...base, status: "已失效" });
    expect(g.ok).toBe(false);
    if (!g.ok) {
      expect(g.why).toBe("stale");
      expect(g.reason).toContain("重提");
    }
  });

  it("失效优先于校验：已失效的提案即使校验通过也不能接受", () => {
    const g = reviewGate({ ...base, status: "已失效", validation: { ok: true, errors: [] } });
    expect(g.ok === false && g.why).toBe("stale");
  });

  it("已处理的提案不再可操作", () => {
    for (const status of ["已接受", "部分接受", "已拒绝"] as const) {
      const g = reviewGate({ ...base, status });
      expect(g.ok).toBe(false);
      if (!g.ok) expect(g.why).toBe("settled");
    }
  });

  it("没有改动的提案没什么可接受", () => {
    const g = reviewGate({ ...base, hunks: [] });
    expect(g.ok === false && g.why).toBe("empty");
  });

  it("P3 拒绝必须附理由", () => {
    expect(canReject("")).toBe(false);
    expect(canReject("   ")).toBe(false);
    expect(canReject("这波先开矿")).toBe(true);
  });
});
