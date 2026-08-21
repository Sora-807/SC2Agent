/**
 * frame/economy（rev 6）：维持器状态必须在帧里可解释。
 *
 * 关键不变式：`quota` 与 `target` 是**两个不同的数** —— 只看 target 会以为
 * "精炼厂没建好就设的气配额"又蒸发了，恰好与 issues P9 的修复相反。
 */
import { readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { parseEnvelopeLine, type EconomyFrame } from "../src/contract";

const FIX_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..", "public", "fixtures");
const fixtures = readdirSync(FIX_DIR).filter((f) => f.endsWith(".jsonl"));

function economyFrames(file: string): EconomyFrame[] {
  const out: EconomyFrame[] = [];
  for (const line of readFileSync(resolve(FIX_DIR, file), "utf8").split("\n")) {
    if (line.trim() === "") continue;
    const env = parseEnvelopeLine(line);
    if (env.topic === "frame/economy") out.push(env.payload);
  }
  return out;
}

function worldUnitTags(file: string): Set<number> {
  const tags = new Set<number>();
  for (const line of readFileSync(resolve(FIX_DIR, file), "utf8").split("\n")) {
    if (line.trim() === "") continue;
    const env = parseEnvelopeLine(line);
    if (env.topic !== "frame/world") continue;
    for (const u of env.payload.units) tags.add(u.tag);
  }
  return tags;
}

describe("frame/economy", () => {
  it("每份夹具都带经济帧（维持器已接入）", () => {
    for (const f of fixtures) {
      expect(economyFrames(f).length, f).toBeGreaterThan(0);
    }
  });

  it("三个任务齐全，且 actual 不超过领地人数", () => {
    for (const f of economyFrames("opening.jsonl")) {
      expect(f.tasks.map((t) => t.task).sort()).toEqual(["gas", "idle", "mineral"]);
      const total = f.tasks.reduce((n, t) => n + t.actual, 0);
      expect(total).toBeLessThanOrEqual(f.domain_workers);
    }
  });

  it("节点容量由后端给（矿 2 / 气 3），前端不硬编码", () => {
    const nodes = economyFrames("opening.jsonl").flatMap((f) => f.nodes);
    expect(nodes.length).toBeGreaterThan(0);
    for (const n of nodes) {
      expect(n.capacity).toBe(n.kind === "gas" ? 3 : 2);
      expect(n.saturated).toBe(n.workers >= n.capacity);
      expect(n.workers).toBeLessThanOrEqual(n.capacity);
    }
  });

  it("气配额持久：开局场景设过 gas=3，配额从设定那刻起一直在", () => {
    const frames = economyFrames("opening.jsonl");
    const withQuota = frames.filter((f) => (f.tasks.find((t) => t.task === "gas")?.quota ?? null) !== null);
    expect(withQuota.length, "设过配额之后每一帧都该带着它").toBeGreaterThan(10);
    expect(withQuota[0]!.tasks.find((t) => t.task === "gas")!.quota).toBe(3);
    // 而且最终真的补满了（精炼厂建好后维持器自动收敛）
    const last = frames.at(-1)!.tasks.find((t) => t.task === "gas")!;
    expect(last.actual).toBe(3);
  });

  it("稳定态零命令：多数帧的 emitted_count 为 0（ADR-0030 验收 6）", () => {
    const frames = economyFrames("leapfrog.jsonl");
    const zero = frames.filter((f) => f.emitted_count === 0).length;
    expect(zero / frames.length).toBeGreaterThan(0.7);
  });

  it("B12：base_tag 非空时必须是本夹具里真实存在的单位 tag（可直接与 frame/world join）", () => {
    for (const file of fixtures) {
      const unitTags = worldUnitTags(file);
      for (const f of economyFrames(file)) {
        for (const n of f.nodes) {
          if (n.base_tag !== null) {
            expect(unitTags.has(n.base_tag), `${file}: base_tag ${n.base_tag} 不在 frame/world 里`).toBe(true);
          }
        }
      }
    }
  });

  it("B12：有主基的夹具里矿节点都能归到基地（有 CC 的帧必有非空 base_tag）", () => {
    // opening 夹具全程有指挥中心（开周就有）——节点归属不应该是 null
    for (const f of economyFrames("opening.jsonl")) {
      if (f.nodes.length === 0) continue;
      expect(f.nodes.some((n) => n.base_tag !== null), "有 CC 却全部节点无归属").toBe(true);
    }
  });
});
