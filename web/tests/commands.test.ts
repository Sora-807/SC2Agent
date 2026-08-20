/**
 * 命令客户端（B6）：HTTP 状态码 → 用户能看懂的区别。
 *
 * 409 与 400 必须区分：
 * - 409 = **世界变了**（观察过期，R8）→ 重取最新帧再试；
 * - 400 = 请求本身不合法 → 原因由后端给，前端不编。
 * 混成一句"失败了"会让人以为是自己点错了。
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { sendCommand } from "../src/api/commands";

const origFetch = globalThis.fetch;

function mockFetch(status: number, body: unknown) {
  globalThis.fetch = vi.fn(async () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    })) as unknown as typeof fetch;
}

afterEach(() => {
  globalThis.fetch = origFetch;
});

describe("sendCommand", () => {
  it("成功时回报 accepted_seq（前端据此显示「下一 step 生效」）", async () => {
    mockFetch(200, { ok: true, accepted_seq: 42, detail: { queue: "main", items: 2 } });
    const res = await sendCommand({ kind: "workers", body: { task: "gas", count: 3 } }, 42);
    expect(res.ok).toBe(true);
    if (res.ok) expect(res.accepted_seq).toBe(42);
  });

  it("409 + current_seq → stale（观察过期，R8），并带上当前 seq", async () => {
    mockFetch(409, {
      detail: { reason: "命令基于过期观察：based_on_seq=3，当前 seq=13", based_on_seq: 3, current_seq: 13 },
    });
    const res = await sendCommand({ kind: "workers", body: { task: "gas", count: 3 } }, 3);
    expect(res.ok).toBe(false);
    if (!res.ok) {
      expect(res.reason).toBe("stale");
      expect(res.current_seq).toBe(13);
      expect(res.message).toContain("过期观察");
    }
  });

  it("409 但没有 current_seq → offline（没有运行中的会话）", async () => {
    mockFetch(409, { detail: "没有运行中的会话（先 POST /api/session/start）" });
    const res = await sendCommand({ kind: "workers", body: { task: "gas", count: 1 } }, 1);
    expect(res.ok).toBe(false);
    if (!res.ok) expect(res.reason).toBe("offline");
  });

  it("400 → invalid，原因原样来自后端（前端不编文案）", async () => {
    mockFetch(400, { detail: "research 暂不支持：upgrade 数据目录（cost/time/前置/科技挂件）待建" });
    const res = await sendCommand(
      { kind: "queue", op: "append", body: { name: "main", items: [] } }, 5);
    expect(res.ok).toBe(false);
    if (!res.ok) {
      expect(res.reason).toBe("invalid");
      expect(res.message).toContain("upgrade 数据目录");
    }
  });

  it("连不上后端 → network（而不是静默失败）", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("ECONNREFUSED");
    }) as unknown as typeof fetch;
    const res = await sendCommand({ kind: "workers", body: { task: "idle", count: 0 } }, 0);
    expect(res.ok).toBe(false);
    if (!res.ok) expect(res.reason).toBe("network");
  });

  it("请求体一定带 based_on_seq（R8 的门在后端，但前端不能忘了填）", async () => {
    const spy = vi.fn(async () =>
      new Response(JSON.stringify({ ok: true, accepted_seq: 7, detail: {} }), { status: 200 }));
    globalThis.fetch = spy as unknown as typeof fetch;
    await sendCommand({ kind: "queue", op: "clear", body: { name: "main" } }, 7);
    const body = JSON.parse(String((spy.mock.calls[0]![1] as RequestInit).body));
    expect(body.based_on_seq).toBe(7);
    expect(body.name).toBe("main");
  });
});
