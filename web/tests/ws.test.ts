/**
 * WsFrameSource 断线语义（2026-08-21）：握手成功之后的 close = 断线，必须**显式**通知。
 *
 * 之前 onclose 只 notify() 一下 —— 驾驶舱静默冻结在最后一帧，会话条还显示旧状态，
 * 用户被过期的画面误导（ISSUES I3 的确切答案：代码里就是没有断线处理）。
 * 这里锁三条语义：断线要通知 / dispose 不算断线 / 握手失败不算断线。
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { REV } from "../src/contract";
import { WsFrameSource } from "../src/source/ws";

/** 最小可驱动的假 WebSocket：只实现 WsFrameSource 用到的那几个成员 */
class FakeWS {
  static OPEN = 1;
  static instances: FakeWS[] = [];
  readyState = 1;
  sent: string[] = [];
  onmessage: ((ev: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(public url: string) { FakeWS.instances.push(this); }
  close(): void { this.readyState = 3; }
  send(s: string): void { this.sent.push(s); }
}

const hello = (source = "live"): string => JSON.stringify({
  topic: "_hello", rev: REV, source, kind: "live", from: 0, to: 100,
});

afterEach(() => {
  FakeWS.instances.length = 0;
  vi.unstubAllGlobals();
});

async function connected(): Promise<{ src: WsFrameSource; ws: FakeWS }> {
  vi.stubGlobal("WebSocket", FakeWS);
  const src = WsFrameSource.replay("http://127.0.0.1:8770", "live", 0, { live: true });
  const p = src.connect();
  const ws = FakeWS.instances.at(-1)!;
  ws.onmessage!({ data: hello() });
  await p;
  return { src, ws };
}

describe("WsFrameSource 断线语义", () => {
  it("握手成功之后 close → 触发 onDisconnect 且 disconnected()=true", async () => {
    const { src, ws } = await connected();
    let fired = 0;
    src.onDisconnect(() => { fired += 1; });
    ws.onclose!();
    expect(fired).toBe(1);
    expect(src.disconnected()).toBe(true);
  });

  it("dispose() 主动关闭不算断线（否则每次切模式都闪横幅）", async () => {
    const { src, ws } = await connected();
    let fired = 0;
    src.onDisconnect(() => { fired += 1; });
    src.dispose();
    ws.onclose!();          // dispose 里 close 之后浏览器才会回调 onclose
    expect(fired).toBe(0);
    expect(src.disconnected()).toBe(false);
  });

  it("握手失败（_hello 没到就 onerror）→ connect 拒绝，不算断线", async () => {
    vi.stubGlobal("WebSocket", FakeWS);
    const src = WsFrameSource.replay("http://127.0.0.1:8770", "live", 0, { live: true });
    const p = src.connect();
    const ws = FakeWS.instances.at(-1)!;
    let fired = 0;
    src.onDisconnect(() => { fired += 1; });
    ws.onerror!();
    await expect(p).rejects.toThrow();
    expect(fired).toBe(0);
    expect(src.disconnected()).toBe(false);
  });

  it("断线只触发一次；dispose 清理回调", async () => {
    const { src, ws } = await connected();
    let fired = 0;
    const un = src.onDisconnect(() => { fired += 1; });
    ws.onclose!();
    un();
    ws.onclose!();
    expect(fired).toBe(1);
  });
});
