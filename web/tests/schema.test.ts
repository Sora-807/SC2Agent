/**
 * static/schema 的不变式测试（数据现在由后端 `tools/make_fixtures.py` 产出）
 *
 * 与后端词表的**逐字同源**已由 Python 侧 `tests/view/test_statics.py` 保证
 * （`schema_static()` 直接比对 `dump_vocabulary()`）。这里守的是前端这一侧：
 * zod 接得住真形状，且几个"抄错就会造出编译不过的方块"的关键点没有退化。
 *
 * rev 1 的教训：手抄词表把 follow/research/use_ability 的参数、point_toward 的 origin 全抄错了。
 */
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { parseEnvelopeLine, type SchemaStatic } from "../src/contract";

const FIX_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..", "public", "fixtures");

function schemaOf(fixture: string): SchemaStatic {
  const text = readFileSync(resolve(FIX_DIR, fixture), "utf8");
  for (const line of text.split("\n")) {
    if (line.trim() === "") continue;
    const env = parseEnvelopeLine(line);
    if (env.topic === "static/schema") return env.payload;
  }
  throw new Error("夹具里没有 static/schema");
}

describe("static/schema 不变式", () => {
  const schema = schemaOf("opening.jsonl");

  it("谓词表非空，且区分 value / bool（编辑器据此决定能否放进参数位）", () => {
    const names = Object.keys(schema.predicates);
    expect(names.length).toBeGreaterThanOrEqual(15);
    expect(schema.predicates["group_center"]?.kind).toBe("value");
    expect(schema.predicates["region_center"]?.kind).toBe("value");
    expect(schema.predicates["point_toward"]?.kind).toBe("value");
    expect(schema.predicates["group_count"]?.kind).toBe("bool");
  });

  it("point_toward 的第一个参数是 origin（rev 1 抄成 from 过）", () => {
    expect(schema.predicates["point_toward"]?.params.map((p) => p.name)).toEqual([
      "origin", "toward", "dist",
    ]);
  });

  it("rev 1 抄错的几个动作签名锁死", () => {
    expect(schema.actions["follow"]?.params.map((p) => p.name)).toEqual(["target_unit"]);
    expect(schema.actions["research"]?.params.map((p) => p.name)).toEqual(["type"]);
    expect(schema.actions["use_ability"]?.params.map((p) => p.name)).toEqual(["ability"]);
    expect(schema.actions["hold_position"]?.params).toEqual([]);
  });

  it("运算符 arity 表：and/or 不限、not 恰好一个", () => {
    expect(schema.operators["and"]).toEqual({ min_args: 2, max_args: null });
    expect(schema.operators["or"]).toEqual({ min_args: 2, max_args: null });
    expect(schema.operators["not"]).toEqual({ min_args: 1, max_args: 1 });
    expect(schema.operators[">="]).toEqual({ min_args: 2, max_args: 2 });
  });

  it("forbidden 是开放分组表，后端新增分组会流通过来（rev 5）", () => {
    const groups = Object.keys(schema.forbidden);
    expect(groups).toEqual(expect.arrayContaining(["predicates", "spatial_tools", "do_ops"]));
    expect(groups.length).toBeGreaterThanOrEqual(5);
    for (const [group, ops] of Object.entries(schema.forbidden)) {
      for (const [op, reason] of Object.entries(ops)) {
        expect(reason.length, group + "." + op + " 缺原因（不静默）").toBeGreaterThan(0);
      }
    }
  });

  it("复合意图不在可直接发的 actions 里，但在 forbidden 里带原因", () => {
    // assign_workers 要按单位扇出成 gather/stop（ADR-0030 D1），driver 不直接执行
    expect(schema.actions["assign_workers"]).toBeUndefined();
    expect(schema.forbidden["composite_actions"]?.["assign_workers"]).toBeTruthy();
    expect(schema.queue.ops).toContain("assign_workers"); // 生产队列侧仍支持
  });

  it("声明白名单 / 节点形态 / 编译规则都下发了（编辑器侧栏直接用）", () => {
    expect(schema.declarations.param_types).toEqual(
      expect.arrayContaining(["int", "float", "point", "bool", "str"]),
    );
    expect(schema.declarations.loop_limit_keys).toContain("max_step_transitions");
    expect(Object.keys(schema.node_forms).length).toBeGreaterThanOrEqual(6);
    expect(schema.rules.length).toBeGreaterThanOrEqual(4);
  });

  it("不支持的队列 op 带原因", () => {
    expect(Object.keys(schema.queue.unsupported_ops).length).toBeGreaterThan(0);
    for (const [op, reason] of Object.entries(schema.queue.unsupported_ops)) {
      expect(reason.length, op + " 缺原因").toBeGreaterThan(0);
    }
  });

  it("三份夹具的 schema 完全一致", () => {
    expect(schemaOf("blocked.jsonl")).toEqual(schema);
    expect(schemaOf("leapfrog.jsonl")).toEqual(schema);
  });
});
