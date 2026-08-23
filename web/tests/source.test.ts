/**
 * 帧源语义测试（F1 验收）
 *
 * 这里锁死三件以后很容易被改坏的事：
 *  1. seek 的快照语义：每个 topic 拿到的都是 "<= 游标的最后一帧"；
 *  2. 只读回看：回看期间 live **继续在后台累积**，但不污染画面（ADR-0023 反例）；
 *  3. 时间线标记只来自帧字段（前端不编文案）。
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { zTopic, type AnyEnvelope, type Topic } from "../src/contract";
import { JsonlFrameSource, extractMarkers, parseJsonl } from "../src/source/jsonl";
import { MockLiveFrameSource } from "../src/source/mock-live";
import { ReviewableSource } from "../src/source/reviewable";

const FIX_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..", "public", "fixtures");
const readFixture = (name: string): string => readFileSync(resolve(FIX_DIR, name), "utf8");

describe("JsonlFrameSource", () => {
  const text = readFixture("opening.jsonl");

  it("seek 后每个 topic 都停在 <= 游标的最后一帧（快照语义）", () => {
    const all = parseJsonl(text);
    const src = JsonlFrameSource.fromJsonl(text);
    const got = new Map<Topic, AnyEnvelope>();
    for (const topic of zTopic.options) {
      src.subscribe(topic, (e) => got.set(topic, e as AnyEnvelope));
    }

    for (const t of [0, 7, 23.5, 41, 60]) {
      src.seek(t);
      for (const topic of zTopic.options) {
        const expected = all
          .filter((e) => e.topic === topic && e.game_time <= t + 1e-9)
          .at(-1);
        const actual = got.get(topic);
        if (!expected) continue;
        expect(actual, topic + " @" + t).toBeDefined();
        expect(actual!.seq, topic + " @" + t).toBe(expected.seq);
      }
    }
    src.dispose();
  });

  it("seek 越界被夹回范围内", () => {
    const src = JsonlFrameSource.fromJsonl(text);
    const { from, to } = src.range();
    src.seek(-999);
    expect(src.position()).toBe(from);
    src.seek(1e9);
    expect(src.position()).toBe(to);
    src.dispose();
  });

  it("退订后不再收帧", () => {
    const src = JsonlFrameSource.fromJsonl(text);
    let n = 0;
    const un = src.subscribe("frame/world", () => { n += 1; });
    const afterSubscribe = n;
    un();
    src.seek(src.range().to);
    expect(n).toBe(afterSubscribe);
    src.dispose();
  });
});

describe("时间线标记", () => {
  it("警报/转移/提案都来自帧字段，按时间排序且去重", () => {
    const envs = parseJsonl(readFixture("leapfrog.jsonl"));
    const ms = extractMarkers(envs);
    expect(ms.length).toBeGreaterThan(0);

    for (let i = 1; i < ms.length; i += 1) {
      expect(ms[i]!.t).toBeGreaterThanOrEqual(ms[i - 1]!.t);
    }
    // 转移在每帧的 transitions 里反复出现 → 必须去重
    const trans = ms.filter((m) => m.kind === "transition");
    expect(new Set(trans.map((m) => m.text + m.t)).size).toBe(trans.length);
    // 文案取自帧的 reason（此夹具的第一条转移原因是 READY）
    expect(trans.some((m) => m.text.includes("READY"))).toBe(true);
  });

  it("阻塞夹具的警报文案来自后端 text_zh", () => {
    const ms = extractMarkers(parseJsonl(readFixture("blocked.jsonl")));
    const alerts = ms.filter((m) => m.kind === "alert");
    expect(alerts.length).toBeGreaterThan(0);
    expect(alerts.some((m) => m.text.includes("队首阻塞"))).toBe(true);
    // 二十一轮语义：缺矿/缺气是顺序执行的等待（info），不是红色阻塞。
    // 本夹具的阻塞原因是「高能瓦斯不足」→ 恒 info；结构性阻塞（前置/供给/放置）才会升 error。
    expect(alerts.filter((m) => m.text.includes("队首阻塞"))
      .every((m) => m.severity === "info")).toBe(true);
  });
});

describe("ReviewableSource（环形缓冲 + 只读回看）", () => {
  const mk = (retain = 900) => {
    const live = MockLiveFrameSource.fromJsonl(readFixture("opening.jsonl"), 1, 1000);
    const rev = new ReviewableSource(live, retain);
    return { live, rev };
  };

  it("live 源本身不支持 seek，套一层后支持", () => {
    const { live, rev } = mk();
    expect(live.caps.seek).toBe(false);
    expect(rev.caps.seek).toBe(true);
    expect(rev.caps.live).toBe(true);
    rev.dispose();
  });

  it("live 模式下新帧直达订阅者", () => {
    const { live, rev } = mk();
    const seen: number[] = [];
    rev.subscribe("frame/world", (e) => seen.push(e.game_time));
    live.advance(10);
    expect(seen.length).toBeGreaterThan(0);
    expect(seen.at(-1)).toBe(10);
    expect(rev.mode()).toBe("live");
    rev.dispose();
  });

  it("回看期间 live 继续累积，但画面停在历史那一刻", () => {
    const { live, rev } = mk();
    live.advance(30);
    const seen: number[] = [];
    rev.subscribe("frame/world", (e) => seen.push(e.game_time));

    rev.seek(10);
    expect(rev.mode()).toBe("review");
    expect(seen.at(-1)).toBe(10);
    const frozen = seen.length;
    const toBefore = rev.range().to;

    live.advance(20); // live 在后台继续跑

    expect(seen.length, "回看期间不应再推帧给订阅者").toBe(frozen);
    expect(seen.at(-1), "画面仍停在 10s").toBe(10);
    expect(rev.range().to, "但缓冲右端要继续生长（禁止停止采集）").toBeGreaterThan(toBefore);
    expect(rev.position()).toBe(10);
    rev.dispose();
  });

  it("回到实时后跳到最新帧", () => {
    const { live, rev } = mk();
    live.advance(30);
    const seen: number[] = [];
    rev.subscribe("frame/world", (e) => seen.push(e.game_time));
    rev.seek(10);
    live.advance(20);

    rev.returnToLive();
    expect(rev.mode()).toBe("live");
    expect(seen.at(-1)).toBe(rev.range().to);

    live.advance(5); // 恢复跟随
    expect(seen.at(-1)).toBe(rev.range().to);
    rev.dispose();
  });

  it("拖到最右端等价于回到实时", () => {
    const { live, rev } = mk();
    live.advance(30);
    rev.seek(10);
    expect(rev.mode()).toBe("review");
    rev.seek(rev.range().to);
    expect(rev.mode()).toBe("live");
    rev.dispose();
  });

  it("环形缓冲淘汰后，seek 到窗口之前被夹到窗口起点", () => {
    const { live, rev } = mk(5); // 每 topic 只留 5 帧
    live.advance(40);
    const { from } = rev.range();
    expect(from).toBeGreaterThan(0); // 早期动态帧已被淘汰
    rev.seek(0);
    expect(rev.position()).toBe(from);
    rev.dispose();
  });

  it("没有 topic 丢过帧时，窗口就是流的起点（起始较晚的 topic 不收紧窗口）", () => {
    const { live, rev } = mk(10_000); // 大到不会淘汰
    live.advance(60);
    const { from, to } = rev.range();
    // opening 夹具的动态帧从 t=0 起，proposals 首次出现在 t=45 —— 后者不得把 from 推到 45
    expect(from).toBe(0);
    expect(to).toBeGreaterThanOrEqual(45);
    rev.seek(20);
    expect(rev.position()).toBe(20);
    rev.dispose();
  });

  it("静态面不把可 seek 窗口钉在 t=0（回归：静态帧永不淘汰）", () => {
    const { live, rev } = mk(5);
    live.advance(40);
    // 静态面仍拿得到（每局只发一次，任何游标下都有效）
    let gotMap = false;
    rev.subscribe("static/map", () => { gotMap = true; });
    expect(gotMap).toBe(true);
    // 但它不得把窗口左端拉回 0
    expect(rev.range().from).toBeGreaterThan(0);
    rev.dispose();
  });
});
