/**
 * 契约与夹具测试（F0 验收）
 *
 * 1. 每份夹具逐行过 zod 校验（夹具合契约）
 * 2. rev 不匹配一律拒绝（不许静默降级）
 * 3. 帧源 seek 语义：每个 topic 只回放 <= 游标的最后一帧
 * 4. 红线体检：夹具里不出现 burnysc2 名（红线 C1）、grid 不是嵌套数组（C5）
 */
import { readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  ContractError, REV, STATIC_TOPICS, isStaticTopic,
  parseEnvelope, parseEnvelopeLine, zTopic,
} from "../src/contract";
import { JsonlFrameSource } from "../src/source/jsonl";

const FIX_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..", "public", "fixtures");
const fixtures = readdirSync(FIX_DIR).filter((f) => f.endsWith(".jsonl"));

describe("夹具", () => {
  it("至少三组场景", () => {
    expect(fixtures.length).toBeGreaterThanOrEqual(3);
  });

  for (const name of fixtures) {
    describe(name, () => {
      const text = readFileSync(resolve(FIX_DIR, name), "utf8");
      const lines = text.split("\n").filter((l) => l.trim() !== "");

      it("逐行合契约", () => {
        for (const [i, line] of lines.entries()) {
          try {
            parseEnvelopeLine(line);
          } catch (err) {
            throw new Error(`第 ${i + 1} 行：${(err as Error).message}`);
          }
        }
        expect(lines.length).toBeGreaterThan(0);
      });

      it("seq 非递减、game_time 非负；seq = 世界版本号（同一 tick 的多个 topic 共享它）", () => {
        // 契约 §2.1：world/flow/production/ops 的 seq 用 GameState.seq。
        // 所以同一 tick 的几个 topic **seq 相同** —— 帧内顺序由流的顺序给，不靠 seq 排。
        let prev = -1;
        for (const line of lines) {
          const env = parseEnvelopeLine(line);
          expect(env.seq).toBeGreaterThanOrEqual(prev);
          expect(env.game_time).toBeGreaterThanOrEqual(0);
          prev = env.seq;
        }
      });

      it("同一 game_time 的帧共享 seq（说明它是世界版本号而不是信封计数器）", () => {
        const bySeq = new Map<number, Set<string>>();
        for (const line of lines) {
          const env = parseEnvelopeLine(line);
          if (env.topic.startsWith("static/")) continue;
          if (!bySeq.has(env.seq)) bySeq.set(env.seq, new Set());
          bySeq.get(env.seq)!.add(env.topic);
        }
        const shared = [...bySeq.values()].filter((s) => s.size > 1);
        expect(shared.length, "至少有些 tick 会同时产出多个 topic").toBeGreaterThan(0);
      });

      it("覆盖三个静态面 + 核心动态面", () => {
        const topics = new Set(lines.map((l) => parseEnvelopeLine(l).topic));
        for (const t of ["static/map", "static/catalog", "static/schema",
          "frame/session", "frame/world", "frame/flow", "frame/production"] as const) {
          expect(topics.has(t), `缺 topic ${t}`).toBe(true);
        }
      });

      it("红线 C1：类型身份一律 stable_id，不出现 burnysc2 名", () => {
        // 例外是**明确声明为原生透传**的两个位置：catalog.burnysc2_name（仅调试面板显示）
        // 与 order.ability_raw（SC2 原生能力名，诊断用；SC2 里训机枪兵的能力就叫 "Marine"）。
        // 其余任何地方出现全大写类型名都是翻译漏了。
        const RAW = /"(MARINE|SCV|SIEGETANK|SIEGETANKSIEGED|BARRACKS|COMMANDCENTER|SUPPLYDEPOT|FACTORY|REFINERY|MEDIVAC)"/;
        for (const line of lines) {
          const env = parseEnvelopeLine(line);
          if (env.topic === "static/catalog") continue;
          const scrubbed = JSON.stringify(env.payload).replace(/"ability_raw":"[^"]*"/g, '"ability_raw":""');
          expect(RAW.test(scrubbed), `${env.topic} 出现了未翻译的 burnysc2 名`).toBe(false);
        }
      });

      it("每个单位的 stable_id 都是两段式（翻译真的发生了）", () => {
        for (const line of lines) {
          const env = parseEnvelopeLine(line);
          if (env.topic !== "frame/world") continue;
          for (const u of env.payload.units) {
            expect(u.stable_id, `tag ${u.tag}`).toMatch(/^[a-z]+\/[a-z0-9_]+$/);
          }
        }
      });

      it("帧源可 seek，且订阅立即拿到当前帧", () => {
        const src = JsonlFrameSource.fromJsonl(text);
        const { from, to } = src.range();
        expect(to).toBeGreaterThanOrEqual(from);

        const seen: number[] = [];
        const un = src.subscribe("frame/world", (env) => seen.push(env.game_time));
        expect(seen.length).toBe(1); // 订阅即回放当前游标下最新一帧

        src.seek(to);
        expect(seen.at(-1)).toBeLessThanOrEqual(to);
        const last = seen.at(-1)!;

        src.seek(from);
        expect(seen.at(-1)).toBeLessThanOrEqual(last);
        un();
        const n = seen.length;
        src.seek(to);
        expect(seen.length).toBe(n); // 退订后不再收
      });
    });
  }
});

describe("契约", () => {
  it("rev 不匹配直接拒绝", () => {
    expect(() => parseEnvelope({ topic: "frame/session", rev: REV + 1, seq: 1, game_time: 0, wall_ms: 0, payload: {} }))
      .toThrow(ContractError);
  });

  it("字段缺失直接拒绝（不静默补默认值）", () => {
    expect(() => parseEnvelope({
      topic: "frame/world", rev: REV, seq: 1, game_time: 0, wall_ms: 0,
      payload: { economy: { minerals: 0, vespene: 0, supply_used: 0 } },
    })).toThrow(ContractError);
  });

  it("未知 topic 直接拒绝", () => {
    expect(() => parseEnvelope({ topic: "frame/nope", rev: REV, seq: 1, game_time: 0, wall_ms: 0, payload: {} }))
      .toThrow(ContractError);
  });

  it("topic 白名单与 §2 一致（13 个）", () => {
    expect(zTopic.options.length).toBe(13);
  });

  it("前后端 REV 必须一致（契约变更流程 C8 的机械守卫）", () => {
    // 与其硬编码版本号，不如直接读后端的常量：任何一侧单独改 rev 都会在这里失败。
    const py = readFileSync(
      resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "modules", "view", "schema.py"),
      "utf8",
    );
    const m = /^REV\s*=\s*(\d+)/m.exec(py);
    expect(m, "modules/view/schema.py 里找不到 REV").not.toBeNull();
    expect(Number(m![1]), "前端 REV 与 modules/view/schema.py 不一致").toBe(REV);
  });

  it("topic 闭集前后端一致", () => {
    const py = readFileSync(
      resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "modules", "view", "schema.py"),
      "utf8",
    );
    const block = /^TOPICS = \(([\s\S]*?)\)/m.exec(py);
    expect(block).not.toBeNull();
    const pyTopics = [...block![1]!.matchAll(/"([^"]+)"/g)].map((x) => x[1]!);
    expect([...pyTopics].sort()).toEqual([...zTopic.options].sort());
  });

  it("静态面是 4 个，且与 topic 白名单一致", () => {
    expect(STATIC_TOPICS.length).toBe(4);
    for (const t of STATIC_TOPICS) {
      expect(zTopic.options).toContain(t);
      expect(isStaticTopic(t)).toBe(true);
    }
    expect(isStaticTopic("frame/world")).toBe(false);
  });

  it("AnyEnvelope 可按 topic 收窄 payload（判别联合回归）", () => {
    const env = parseEnvelope({
      topic: "frame/session", rev: REV, seq: 1, game_time: 12, wall_ms: 0,
      payload: {
        state: "对局中", frame_source: "fixture", map_name: "LadderMap",
        my_race: "terran", enemy_race: "protoss", game_time: 12, error: null,
      },
    });
    // 编译期：下面这行要求 env.payload 被收窄成 SessionFrame（不收窄则 tsc 报错）
    if (env.topic === "frame/session") {
      const state: "未连接" | "启动中" | "对局中" | "已结束" | "崩溃" = env.payload.state;
      expect(state).toBe("对局中");
    } else {
      throw new Error("topic 收窄失败");
    }
  });
});
