/**
 * 规划页（F9/F13）—— 离线三工作台。
 *
 * 红线（ADR-0022 反例 / R5）：
 * 1. 离线草稿**绝不默认叠加 live** —— 本页不读 live 会话，只读静态面；
 * 2. live（实时驾驶模式）中不出现模块/Strategy 的创建与编辑入口（R5）——
 *    守卫读**模式轴**（F13 修根因 W：旧代码读 sourceKind==="live"，但那个值永远产生不出来）；
 * 3. 所有画布操作转成**结构化草稿**（DraftItem / hunk），人与 agent 同一表示。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { createProposal } from "../api/proposals";
import { MapCanvas } from "../canvas/MapCanvas";
import { defaultLayers } from "../canvas/layers";
import { renderBranches, renderValue } from "../graph/ast";
import { layout } from "../graph/layout";
import {
  applyDraft, loadDraft, mapDraftToHunks, nextMarkName, saveDraft,
  slotOverlaps, slotTl, snapToCellCenter,
  type MapPlanHunk,
} from "../planning/map-draft";
import {
  describeItem, draftCost, draftToHunks, emptyItem, placementOptions,
  type DraftItem,
} from "../planning/queue-draft";
import { PLAN_GATE_REASON } from "../shell/rail";
import { Card, Empty, PAGE_SCROLL } from "../shell/ui";
import { useFrames } from "../store/frames";
import type { CatalogStatic, MapStatic, SchemaStatic, StrategyStatic } from "../contract";
import { T } from "../shell/tokens";

const TABS = [
  ["map", "地图规划"],
  ["production", "生产规划"],
  ["flow", "Flow 装配"],
] as const;

export function PlanningPage(props: { initialTab?: "map" | "production" | "flow" }) {
  const { catalog, map, strategy, schema, api, mode } = useFrames();
  const [tab, setTab] = useState<"map" | "production" | "flow">(
    props.initialTab ?? "production",
  );
  // R5 门控：实时驾驶（live）下 authoring 全部置灰 + 理由（G7 不静默隐藏）
  const gated = mode === "drive";

  return (
    <div className={PAGE_SCROLL + " space-y-3"}>
      <Card title="规划（离线工作台）"
            right={<span className={gated ? "text-note text-red-400" : "text-note text-amber-500"}>
              {gated
                ? PLAN_GATE_REASON + " —— 切回离线编辑或复盘模式后再改"
                : "草稿只在本页；提为提案后才进入审批流（§6）"}
            </span>}>
        <div className="flex gap-2 text-xs">
          {TABS.map(([k, label]) => (
            <button key={k} onClick={() => setTab(k)} disabled={gated}
                    title={gated ? PLAN_GATE_REASON : undefined}
                    className={"rounded border px-2 py-1 "
                      + (gated ? "cursor-not-allowed border-neutral-800 text-ghost" : "")
                      + (!gated && tab === k ? "border-neutral-500" : "")
                      + (!gated && tab !== k ? "border-neutral-800 text-faint" : "")}>
              {label}
            </button>
          ))}
        </div>
      </Card>

      {!gated && tab === "map" && <MapPlanning map={map} />}
      {!gated && tab === "production" && (
        <ProductionPlanning catalog={catalog} map={map} apiOk={api.ok} />
      )}
      {!gated && tab === "flow" && <FlowAssembly graph={strategy} schema={schema} />}
      {gated && (
        <Card title="实时驾驶中不可编辑">
          <Empty text={PLAN_GATE_REASON + " —— 切回「离线编辑」或「复盘」模式后再来"} />
        </Card>
      )}
    </div>
  );
}

function MapPlanning(props: { map: MapStatic | null }) {
  const { map } = props;
  const world = useFrames((s) => s.world);
  const production = useFrames((s) => s.production);
  const catalog = useFrames((s) => s.catalog);
  const economy = useFrames((s) => s.economy);

  // F14 切片 1：结构化草稿（hunk 序列）是真相源，画布吃 applyDraft 的投影结果。
  // 持久化到 localStorage —— 离线画完关页面不丢；B14 落地后同一批 hunk 直接进提案信封。
  const [draft, setDraft] = useState<MapPlanHunk[]>([]);
  const [placeMode, setPlaceMode] = useState<"mark" | "slot" | null>(null);
  const [slotSize, setSlotSize] = useState<2 | 3 | 5>(2);
  const [slotKind, setSlotKind] = useState<"supply" | "production" | "addon">("supply");
  /** 槽位即时校验的短提示（画布上浮层显示，2.6s 后自动消失） */
  const [draftLoaded, setDraftLoaded] = useState(false);
  useEffect(() => {
    setDraft(loadDraft(map));
    setDraftLoaded(true);
    // map 静态面换了 = 换一张图，草稿也换一份（键带 map_name）
  }, [map]);
  useEffect(() => {
    if (draftLoaded) saveDraft(map, draft);
  }, [draft, draftLoaded, map]);

  const proj = useMemo(
    () => (map ? applyDraft(
      map.pos_marks,
      // 槽位基座直接用后端给的 build_point/tl/br（C2：零几何换算）
      map.build_slots.map((s) => ({
        name: s.name, pos: s.build_point, size: s.size, kind: s.kind,
        tl: s.tl, br: s.br,
      })),
      draft,
    ) : { marks: [], slots: [] }),
    [map, draft],
  );
  const marks = proj.marks;
  // "草稿新增"的名单（列表里打标记用）
  const addedNames = useMemo(() => new Set(draft
    .filter((h) => h.kind === "add_mark").map((h) => (h as { name: string }).name)), [draft]);
  const addedSlots = useMemo(() => new Set(draft
    .filter((h) => h.kind === "add_slot").map((h) => (h as { name: string }).name)), [draft]);
  const [submitMsg, setSubmitMsg] = useState<string | null>(null);
  const submitMapPlan = async (): Promise<void> => {
    if (draft.length === 0) { setSubmitMsg("草稿是空的"); return; }
    setSubmitMsg(null);
    try {
      const p = await createProposal({
        kind: "map_plan",
        title_zh: "地图规划草稿（" + draft.length + " 条改动）",
        rationale_zh: "离线地图规划：点位/槽位的增删改（F14 草稿）",
        target: {},
        hunks: mapDraftToHunks(draft),
      });
      setSubmitMsg("已提交提案 " + p.id + "（" + p.status + "），去对话栏审批");
    } catch (err) {
      setSubmitMsg("提交失败：" + (err as Error).message);
    }
  };

  if (!map) return <Card title="地图规划"><Empty text="等 static/map…" /></Card>;

  const place = (pos: [number, number]): void => {
    const snapped = snapToCellCenter(pos);
    if (placeMode === "mark") {
      const name = nextMarkName(marks.map((m) => m.name));
      setDraft((d) => [...d, { kind: "add_mark", name, pos: snapped }]);
      return;
    }
    if (placeMode === "slot") {
      // 即时重叠校验：与"当前投影里所有槽位"（静态 + 草稿）相交就拒（后端 validate 同规则兜底）
      const fp = { tl: slotTl(snapped, slotSize), br: [0, 0] as [number, number] };
      fp.br = [fp.tl[0] + slotSize - 1, fp.tl[1] + slotSize - 1];
      for (const s of proj.slots) {
        if (slotOverlaps(fp, { tl: s.tl, br: s.br })) {
          slotError.current = `与槽位 ${s.name} 重叠 —— 换个位置`;
          setTimeout(() => { slotError.current = null; }, 2600);
          return;
        }
      }
      const name = nextMarkName(proj.slots.map((s) => s.name)).replace("mark_", "slot_");
      setDraft((d) => [...d, { kind: "add_slot", name, pos: snapped, size: slotSize, slotKind }]);
    }
  };
  const slotError = useRef<string | null>(null);

  const rename = (from: string, to: string): void => {
    const clean = to.trim();
    if (!clean || clean === from) return;
    if (marks.some((m) => m.name === clean)) return;   // 查重失败 = 忽略（不打扰）
    setDraft((d) => [...d, { kind: "rename_mark", from, to: clean }]);
  };

  const remove = (name: string): void => {
    setDraft((d) => [...d, { kind: "del_mark", name }]);
  };

  return (
    <div className="flex gap-3">
      <div className="min-w-0 flex-1">
        <div className="relative h-[62vh] min-h-[420px] overflow-hidden rounded border border-neutral-800">
          <MapCanvas
            map={map}
            world={world}
            production={production}
            catalog={catalog}
            economy={economy}
            layers={defaultLayers()}
            smooth={false}
            selection={null}
            onSelect={() => {}}
            marksOverride={marks}
            slotsOverride={proj.slots}
            onBlankClick={placeMode ? place : undefined}
          />
          {placeMode && (
            <div className="pointer-events-none absolute left-2 top-2 rounded border border-amber-700 bg-amber-950/80 px-2 py-1 text-note text-amber-300">
              {placeMode === "mark"
                ? "放置点位：点击地图（吸附格心）· 再点按钮退出"
                : `放置槽位 ${slotSize}×${slotSize}（${slotKind}）：点击地图（吸附格心）· 与既有槽位重叠会拒绝`}
            </div>
          )}
          {slotError.current && (
            <div className="pointer-events-none absolute left-2 top-10 rounded border border-red-700 bg-red-950/85 px-2 py-1 text-note text-red-300">
              {slotError.current}
            </div>
          )}
        </div>
        <div className="mt-1 text-note text-ghost">
          菱形 = 点位标记（U16：与建筑矩形、单位 chip 不同形）。草稿合并显示：新增/改名/删除即时可见。
        </div>
      </div>

      <div className="w-80 shrink-0 space-y-3">
        <Card title="放置工具">
          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={() => setPlaceMode((v) => (v === "mark" ? null : "mark"))}
              className={"rounded border px-2 py-1 text-note " + (placeMode === "mark"
                ? "border-amber-600 bg-amber-950/40 text-amber-300"
                : "border-neutral-700 text-neutral-300")}
            >{placeMode === "mark" ? "退出放置点位" : "＋ 点位"}</button>
            <button
              onClick={() => setPlaceMode((v) => (v === "slot" ? null : "slot"))}
              className={"rounded border px-2 py-1 text-note " + (placeMode === "slot"
                ? "border-amber-600 bg-amber-950/40 text-amber-300"
                : "border-neutral-700 text-neutral-300")}
            >{placeMode === "slot" ? "退出放置槽位" : "＋ 槽位"}</button>
          </div>
          {placeMode === "slot" && (
            <div className="mt-2 flex flex-wrap items-center gap-1 border-t border-neutral-800 pt-2">
              {([2, 3, 5] as const).map((sz) => (
                <button key={sz}
                        onClick={() => setSlotSize(sz)}
                        className={"rounded border px-1.5 py-0.5 text-note " + (slotSize === sz
                          ? "border-sky-600 bg-sky-950/40 text-sky-300"
                          : "border-neutral-700 text-neutral-300")}
                >{sz}×{sz}</button>
              ))}
              {(["supply", "production", "addon"] as const).map((k) => (
                <button key={k}
                        onClick={() => setSlotKind(k)}
                        className={"rounded border px-1.5 py-0.5 text-note " + (slotKind === k
                          ? "border-sky-600 bg-sky-950/40 text-sky-300"
                          : "border-neutral-700 text-neutral-300")}
                >{{ supply: "补给", production: "生产", addon: "挂件" }[k]}</button>
              ))}
              <span className="w-full text-note text-ghost">
                与既有槽位 footprint 重叠会被当场拒绝（后端 validate 同规则兜底）
              </span>
            </div>
          )}
          <div className="mt-2 border-t border-neutral-800 pt-2 text-note text-ghost">
            草稿已存本地（localStorage）。提为提案走审批：接受后写回后端覆盖层，
            **新会话**加载时生效。
          </div>
          <button
            disabled={draft.length === 0}
            title={draft.length === 0 ? "草稿是空的" : "草稿将作为 map_plan 提案提交审批"}
            className="mt-2 w-full rounded border border-emerald-700 bg-emerald-900/40 px-2 py-1 text-note text-emerald-200 disabled:opacity-50"
            onClick={() => void submitMapPlan()}
          >提为提案（map_plan）</button>
          {submitMsg && <div className="mt-1 text-note text-amber-400">{submitMsg}</div>}
        </Card>

        <Card title={"点位（" + marks.length + "）"}>
          <ul className="max-h-64 space-y-1 overflow-auto">
            {marks.map((m) => (
              <li key={m.name} className="flex items-center gap-1 rounded px-1 py-0.5 hover:bg-neutral-900/60">
                <input
                  defaultValue={m.name}
                  onBlur={(e) => rename(m.name, e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                  className={"w-32 rounded border border-transparent bg-transparent px-1 py-0.5 "
                    + "hover:border-neutral-700 focus:border-sky-700 focus:bg-neutral-950 " + T.mono}
                />
                <span className={"ml-auto text-note text-faint " + T.mono}>
                  {m.pos[0].toFixed(1)}, {m.pos[1].toFixed(1)}
                </span>
                <button
                  className="shrink-0 text-red-400/70 hover:text-red-300"
                  title={"删除" + (addedNames.has(m.name) ? "（草稿新增）" : "（草稿覆盖静态点位）")}
                  onClick={() => remove(m.name)}
                >×</button>
              </li>
            ))}
          </ul>
          <div className="mt-1 text-note text-ghost">名字回车/失焦即改（重名忽略）；× 从草稿删除。</div>
        </Card>

        <Card title={"建造槽位（" + proj.slots.length + "）"}>
          <ul className="max-h-56 space-y-1 overflow-auto">
            {proj.slots.map((s) => (
              <li key={s.name} className="flex items-center gap-1 rounded px-1 py-0.5 hover:bg-neutral-900/60">
                <span className="font-medium text-neutral-300">{s.name}</span>
                <span className="text-faint">{s.kind} {s.size}×{s.size}</span>
                <span className={"ml-auto text-note text-ghost " + T.mono}>
                  {s.pos[0].toFixed(1)},{s.pos[1].toFixed(1)}
                </span>
                <button
                  className="shrink-0 text-red-400/70 hover:text-red-300"
                  title={addedSlots.has(s.name) ? "删除（草稿新增）" : "删除（草稿覆盖静态槽位）"}
                  onClick={() => setDraft((d) => [...d, { kind: "del_slot", name: s.name }])}
                >×</button>
              </li>
            ))}
          </ul>
          <div className="mt-1 text-note text-ghost">
            拖动槽位是切片 2b（现在：删 + 放新位）。删除也会以 hunk 形式进提案。
          </div>
        </Card>
      </div>
    </div>
  );
}

function ProductionPlanning(props: {
  catalog: CatalogStatic | null;
  map: MapStatic | null;
  apiOk: boolean;
}) {
  const { catalog, map, apiOk } = props;
  const [items, setItems] = useState<DraftItem[]>([]);
  const [title, setTitle] = useState("生产草稿");
  const [rationale, setRationale] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  const options = useMemo(() => placementOptions(map), [map]);
  const cost = useMemo(() => draftCost(items, catalog), [items, catalog]);
  const buildables = (catalog?.entries ?? []).filter(
    (e: CatalogStatic["entries"][number]) =>
      e.role === "building" || e.role === "combat" || e.role === "worker");

  const update = (id: string, patch: Partial<DraftItem>): void =>
    setItems((list) => list.map((it) => (it.id === id ? { ...it, ...patch } : it)));

  const setPlacement = (id: string, value: string): void => {
    if (!value) { update(id, { placement: null }); return; }
    const [kind, name] = value.split(":");
    const region = name ?? "";
    update(id, {
      placement: kind === "region"
        ? { kind: "in_region", region, index: null }
        : { kind: "exact", mark: region },
    });
  };

  const submit = async (): Promise<void> => {
    if (!apiOk) { setMsg("后端未连接，无法提交提案（可继续本地编辑）"); return; }
    if (items.length === 0) { setMsg("草稿是空的"); return; }
    if (!rationale.trim()) { setMsg("必须写理由（§6 P3：没有理由的提案不可接受）"); return; }
    setMsg(null);
    try {
      const p = await createProposal({
        kind: "production_queue",
        title_zh: title || "生产草稿",
        rationale_zh: rationale,
        target: { queue: "main" },
        hunks: draftToHunks(items),
      });
      setMsg("已提交提案 " + p.id + "（" + p.status + "），去对话栏审批");
    } catch (err) {
      setMsg("提交失败：" + (err as Error).message);
    }
  };

  return (
    <Card title="生产规划" right={<span className="text-note text-faint">
      矿 {cost.minerals} · 气 {cost.vespene} · 供给 {cost.supply}
      {cost.missing.length > 0 && (
        <span className="ml-1 text-amber-400">缺目录项：{cost.missing.join(", ")}</span>
      )}
    </span>}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <button className="rounded border border-neutral-700 px-2 py-1 text-xs"
                onClick={() => setItems((l) => [...l, emptyItem()])}>+ 加一项</button>
        <button className="rounded border border-neutral-700 px-2 py-1 text-xs"
                onClick={() => setItems([])}>清空</button>
        <span className="text-note text-ghost">
          build 必须有 placement；train / assign_workers 不需要
        </span>
      </div>

      {items.length === 0 ? <Empty text="还没有草稿项" /> : (
        <div className="space-y-1">
          {items.map((it, i) => (
            <div key={it.id} className="rounded border border-neutral-800 p-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="w-6 text-ghost">{i}.</span>
                <select value={it.op}
                        onChange={(e) => update(it.id, { op: e.target.value as DraftItem["op"] })}
                        className="rounded border border-neutral-700 bg-neutral-950 px-1 text-xs">
                  <option value="build">build</option>
                  <option value="train">train</option>
                  <option value="assign_workers">assign_workers</option>
                </select>
                {it.op === "assign_workers" ? (
                  <>
                    <select value={it.task ?? "gas"}
                            onChange={(e) => update(it.id, { task: e.target.value as DraftItem["task"] })}
                            className="rounded border border-neutral-700 bg-neutral-950 px-1 text-xs">
                      <option value="mineral">维持采矿</option>
                      <option value="gas">维持采气</option>
                      <option value="idle">维持空闲</option>
                    </select>
                    <input type="number" min={0} value={it.count}
                           onChange={(e) => update(it.id, { count: Number(e.target.value) })}
                           className="w-16 rounded border border-neutral-700 bg-neutral-950 px-1 text-xs" />
                    <span className="text-note text-ghost">目标值（维持 N 个，幂等）</span>
                  </>
                ) : (
                  <>
                    <select value={it.type ?? ""}
                            onChange={(e) => update(it.id, { type: e.target.value || null })}
                            className="rounded border border-neutral-700 bg-neutral-950 px-1 text-xs">
                      <option value="">（选类型）</option>
                      {buildables.map((e) => (
                        <option key={e.stable_id} value={e.stable_id}>
                          {e.display_name_zh}（{e.cost.minerals}/{e.cost.vespene}，{e.build_time}s）
                        </option>
                      ))}
                    </select>
                    <input type="number" min={1} value={it.count}
                           onChange={(e) => update(it.id, { count: Number(e.target.value) })}
                           className="w-16 rounded border border-neutral-700 bg-neutral-950 px-1 text-xs" />
                    <select
                      value={it.placement
                        ? (it.placement.kind === "exact" ? "slot:" + it.placement.mark : "region:" + it.placement.region)
                        : ""}
                      onChange={(e) => setPlacement(it.id, e.target.value)}
                      className="rounded border border-neutral-700 bg-neutral-950 px-1 text-xs">
                      <option value="">（placement）</option>
                      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  </>
                )}
                <button className="ml-auto text-red-400"
                        onClick={() => setItems((l) => l.filter((x) => x.id !== it.id))}>×</button>
              </div>
              <div className="mt-0.5 pl-8 text-note text-ghost">{describeItem(it)}</div>
            </div>
          ))}
        </div>
      )}

      <div className="mt-3 space-y-2 border-t border-neutral-800 pt-2">
        <input className="w-64 rounded border border-neutral-700 bg-neutral-950 px-2 py-1 text-xs"
               value={title} onChange={(e) => setTitle(e.target.value)} placeholder="提案标题" />
        <input className="w-full rounded border border-neutral-700 bg-neutral-950 px-2 py-1 text-xs"
               value={rationale} onChange={(e) => setRationale(e.target.value)}
               placeholder="理由（必填）—— 你自己审批自己的草稿也一样要理由（§6 P3）" />
        <button className="rounded border border-emerald-700 bg-emerald-900/40 px-3 py-1 text-emerald-200"
                onClick={() => void submit()}>提为提案（走审批流）</button>
        {msg && <div className="text-xs text-amber-400">{msg}</div>}
      </div>
    </Card>
  );
}

function FlowAssembly(props: {
  graph: StrategyStatic | null;
  schema: SchemaStatic | null;
}) {
  const { graph, schema } = props;
  const [stepId, setStepId] = useState<string | null>(null);

  if (!graph) return <Card title="Flow 装配"><Empty text="等 static/strategy…" /></Card>;
  const laid = layout(
    graph.steps.map((s: { step_id: string; branches: unknown[] }) => ({
      id: s.step_id, branchCount: Array.isArray(s.branches) ? s.branches.length : 0,
    })),
    graph.edges as { from: string; to: string; kind: string; reason: string }[],
    graph.initial_step,
  );
  // 展示顺序 = 布局顺序（层 → 层内序）：F12 起布局在 graph/layout，这里只借它的排序
  const stepOrder = [...graph.steps].sort(
    (x: { step_id: string }, y: { step_id: string }) =>
      (laid.layer.get(x.step_id) ?? 0) - (laid.layer.get(y.step_id) ?? 0)
      || (laid.order.get(x.step_id) ?? 0) - (laid.order.get(y.step_id) ?? 0),
  );
  const step = graph.steps.find((s: { step_id: string }) => s.step_id === (stepId ?? graph.initial_step))
    ?? graph.steps[0];
  const branches = step ? renderBranches(step.branches, schema) : [];

  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      <Card title={"策略图 · " + graph.id + " v" + graph.version}>
        <div className="space-y-1">
          {stepOrder.map((n: { step_id: string }) => (
            <button key={n.step_id} onClick={() => setStepId(n.step_id)}
                    className={"w-full rounded border p-2 text-left "
                      + (n.step_id === step?.step_id ? "border-neutral-500" : "border-neutral-800")}>
              <span className="font-medium text-neutral-200">{n.step_id}</span>
              {n.step_id === graph.initial_step && <span className="ml-2 text-note text-faint">起点</span>}
              <span className="ml-2 text-note text-ghost">
                {graph.edges
                  .filter((e: { from: string }) => e.from === n.step_id)
                  .map((e: { to: string }) => "→" + e.to)
                  .join(" ")}
              </span>
            </button>
          ))}
        </div>
        <div className="mt-2 text-note text-ghost">
          转移上限 {Object.entries(graph.loop_limits).map(([k, v]) => k + "=" + v).join("、") || "—"} ·
          槽位 {graph.group_slots.map((s: string) => s + "→" + (graph.bindings[s] ?? "?")).join("、")}
        </div>
      </Card>
      <Card title={"分支 AST · " + (step?.step_id ?? "—")}>
        {branches.length === 0 ? <Empty text="没有分支" /> : (
          <ol className="space-y-2">
            {branches.map((b) => (
              <li key={b.index} className="rounded border border-neutral-800 p-2">
                <div className="flex gap-2 text-xs">
                  <span className="text-faint">#{b.index}</span>
                  <span>{b.id ?? "（未命名）"}</span>
                  <span className="text-faint">{b.when === null ? "else" : "when"}</span>
                </div>
                {b.when !== null && <code className="mt-1 block text-sky-300">{b.when}</code>}
                {b.actions.length > 0 && (
                  <ul className="mt-1 text-xs text-neutral-300">
                    {b.actions.map((a, i) => (
                      <li key={i} className={a.forbidden ? "text-ghost" : ""}>
                        → {a.text}
                        {a.forbidden && <span className="ml-1 text-amber-600">（不可用：{a.forbidden}）</span>}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ol>
        )}
      </Card>
      <Card title="声明与别名" className="lg:col-span-2">
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          <div>
            <div className="text-dim">参数声明</div>
            {Object.entries(graph.params).map(([k, v]) => (
              <div key={k} className="text-xs">
                {k} : {String((v as Record<string, unknown>)["type"])}
                {" = "}{JSON.stringify((v as Record<string, unknown>)["default"])}
              </div>
            ))}
          </div>
          <div>
            <div className="text-dim">别名（definitions）</div>
            {Object.entries(graph.definitions).map(([k, v]) => (
              <div key={k} className="text-xs">{k} = <code>{renderValue(v)}</code></div>
            ))}
            {Object.keys(graph.definitions).length === 0 && <Empty text="无" />}
          </div>
        </div>
        <div className="mt-2 border-t border-neutral-800 pt-2 text-note text-ghost">
          AST 编辑（改结构）按决策 U8 留到 next —— 现在先把「看与导出」做扎实；
          导出按钮把 static/strategy 原样存为 JSON。
        </div>
        <button
          className="mt-1 rounded border border-neutral-700 px-2 py-1 text-xs"
          onClick={() => {
            const blob = new Blob([JSON.stringify(graph, null, 2)], { type: "application/json" });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = graph.id + "-v" + graph.version + ".json";
            a.click();
            URL.revokeObjectURL(url);
          }}
        >导出 JSON</button>
      </Card>
    </div>
  );
}