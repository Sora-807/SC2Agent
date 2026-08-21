/**
 * 规划页（F9/F13/F16）—— 离线三工作台。
 *
 * 红线（ADR-0022 反例 / R5）：
 * 1. 离线草稿**绝不默认叠加 live** —— 本页不读 live 会话，只读静态面；
 * 2. live（实时驾驶模式）中不出现模块/Strategy 的创建与编辑入口（R5）——
 *    守卫读**模式轴**（F13 修根因 W：旧代码读 sourceKind==="live"，但那个值永远产生不出来）；
 * 3. 所有画布操作转成**结构化草稿**（DraftItem / hunk），人与 agent 同一表示。
 *
 * F16：地图 tab 是分栏页（根 h-full 不滚，画布 flex-1）；生产/Flow tab 仍是文档页（内部 PAGE_SCROLL）。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { MapCanvas } from "../canvas/MapCanvas";
import { decodeGrid } from "../canvas/grid";
import { defaultLayers, type LayerState } from "../canvas/layers";
import { LayerPanel } from "../canvas/LayerPanel";
import {
  createMapPlan, getMapPlanPayload, listMapPlans, removeMapPlan, saveMapPlan,
  type MapPlanMeta,
} from "../api/map-plans";
import { ProjectionBoard } from "../charts/ProjectionBoard";
import { AlertsList } from "../panels/AlertsList";
import { renderBranches, renderValue } from "../graph/ast";
import { layout } from "../graph/layout";
import {
  applyDraft, mapDraftToHunks, nextMarkName, previewPlacement,
  snapToCellCenter,
  type MapPlanHunk,
} from "../planning/map-draft";
import {
  describeItem, draftCost, draftFromJson, emptyItem, itemToJson, placementOptions,
  type DraftItem,
} from "../planning/queue-draft";
import {
  createPlan, getPlan, listPlans, removePlan, savePlan, simulatePlan,
  type Plan, type PlanMeta, type PlanSimResult,
} from "../api/plans";
import { PLAN_GATE_REASON } from "../shell/rail";
import { Card, Empty, PAGE_SCROLL } from "../shell/ui";
import { useFrames } from "../store/frames";
import type { CatalogStatic, MapStatic, SchemaStatic, StrategyStatic } from "../contract";
import { T } from "../shell/tokens";

/** 槽位类别的 zh（UI 铬件文案；点位/建筑名等数据 zh 一律来自后端，C4） */
const SLOT_KIND_ZH: Record<string, string> = {
  supply: "补给", production: "生产", addon: "挂件",
};

/** 地图规划 REST payload = static/map 形状 + 固定建造点预留区（矩形，后端单点算） */
export interface ReservedBox {
  tl: [number, number];
  br: [number, number];
  kind: string;
  label_zh: string;
  /** 预设名（蓝方主矿/蓝方二矿/红方主矿气井1…；矿脉无名） */
  name?: string | null;
}
type PlanMapPayload = MapStatic & { reserved?: ReservedBox[] };

/**
 * P1 后本组件不再有自己的导航：地图规划/生产规划/Flow 装配是左侧导航栏的三个入口
 * （route: plan-map/plan-production/plan-flow → initialTab），页内的 tab 行已删
 * （用户拍板：导航只有一处）。tab 直接从 prop 派生，不留本地状态。
 */
export function PlanningPage(props: { initialTab?: "map" | "production" | "flow" }) {
  const { catalog, map, strategy, schema, api, mode } = useFrames();
  const tab = props.initialTab ?? "production";
  // R5 门控：drive 模式下导航已不可达规划页，这里只兜底 hash 直达（G7 不静默）
  const gated = mode === "drive";

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      {gated && (
        <div className="text-note text-red-400">
          {PLAN_GATE_REASON} —— 切回离线编辑或复盘模式后再改
        </div>
      )}

      <div className="min-h-0 flex-1">
        {!gated && tab === "map" && <MapPlanning map={map} apiOk={api.ok} />}
        {!gated && tab === "production" && (
          <div className={PAGE_SCROLL}>
            <ProductionPlanning catalog={catalog} map={map} apiOk={api.ok} />
          </div>
        )}
        {!gated && tab === "flow" && (
          <div className={PAGE_SCROLL}>
            <FlowAssembly graph={strategy} schema={schema} />
          </div>
        )}
        {gated && (
          <Card title="实时驾驶中不可编辑">
            <Empty text={PLAN_GATE_REASON + " —— 切回「离线编辑」或「复盘」模式后再来"} />
          </Card>
        )}
      </div>
    </div>
  );
}

function MapPlanning(props: { map: MapStatic | null; apiOk: boolean }) {
  const { map, apiOk } = props;
  const catalog = useFrames((s) => s.catalog);

  // P2 切片 1：地图规划是后端文件（默认地图锁定 + 复制新建 + 出生点切换）。
  // 底图 = 所选地图规划的 static/map payload（terrain 叠加夹具地形）；
  // 无后端时回退夹具 map（保底可看，提示保存需要后端）。
  const [mplans, setMplans] = useState<MapPlanMeta[] | null>(null);
  const [selMap, setSelMap] = useState<string>("LadderMap");
  const [spawn, setSpawn] = useState<string>("bl");
  const [selId, setSelId] = useState<string | null>(null);
  const [planPayload, setPlanPayload] = useState<PlanMapPayload | null>(null);
  const [newName, setNewName] = useState("");
  const [planMsg, setPlanMsg] = useState<string | null>(null);
  const selMeta = mplans?.find((p) => p.id === selId) ?? null;
  /** 双下拉模型（用户拍板）：规划 = 某地图 × 某方的一种布局 ——
   * 地图下拉 + 出生点切换 → 规划下拉只列这个空间的布局，随时切换对比 */
  const mapNames = useMemo(
    () => [...new Set((mplans ?? []).map((p) => p.map_name))], [mplans]);
  const spacePlans = useMemo(
    () => (mplans ?? []).filter((p) => p.map_name === selMap && p.spawn === spawn),
    [mplans, selMap, spawn]);

  const refreshList = async (): Promise<MapPlanMeta[]> => {
    const rows = await listMapPlans();
    setMplans(rows);
    return rows;
  };
  const openMapPlan = async (id: string): Promise<void> => {
    setSelId(id);
    setPlanPayload(await getMapPlanPayload(id));
    setDraft([]);   // 换规划 = 换底图，草稿不跨规划携带
    setPlanMsg(null);
  };
  /** 切地图/出生点 = 换一个规划空间：自动落该空间第一个规划 */
  const switchSpace = (m: string, sp: string): void => {
    setSelMap(m);
    setSpawn(sp);
    const first = (mplans ?? []).find((p) => p.map_name === m && p.spawn === sp);
    if (first) void openMapPlan(first.id);
    else { setSelId(null); setPlanPayload(null); setDraft([]); }
  };
  useEffect(() => {
    if (!apiOk) return;
    void refreshList()
      .then((rows) => {
        const first = rows.find((p) => p.map_name === "LadderMap" && p.spawn === "bl")
          ?? rows[0];
        if (first) {
          setSelMap(first.map_name);
          setSpawn(first.spawn);
          return openMapPlan(first.id);
        }
        return undefined;
      })
      .catch((err: Error) => setPlanMsg("加载地图规划失败：" + err.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 挂载时拉一次
  }, [apiOk]);

  const baseMap: PlanMapPayload | null = planPayload
    ? { ...planPayload, terrain: planPayload.terrain ?? map?.terrain ?? null }
    : map;

  // F14 切片 1：结构化草稿（hunk 序列）是真相源，画布吃 applyDraft 的投影结果。
  // P2 后草稿存在内存、保存进地图规划文件（后端是真相源，localStorage 退役）。
  const [draft, setDraft] = useState<MapPlanHunk[]>([]);
  const [placeMode, setPlaceMode] = useState<"mark" | "slot" | null>(null);
  const [slotSize, setSlotSize] = useState<2 | 3 | 5>(2);
  const [slotKind, setSlotKind] = useState<"supply" | "production" | "addon">("supply");
  /** F16：悬停世界坐标（放置预览用）；离开画布置 null */
  const [hoverPos, setHoverPos] = useState<[number, number] | null>(null);
  /** F16：选中的点位/槽位名（地图 ↔ 列表双向高亮）*/
  const [selectedName, setSelectedName] = useState<string | null>(null);

  const proj = useMemo(
    () => (baseMap ? applyDraft(
      baseMap.pos_marks,
      // 槽位基座直接用后端给的 build_point/tl/br（C2：零几何换算）
      baseMap.build_slots.map((s) => ({
        name: s.name, pos: s.build_point, size: s.size, kind: s.kind,
        tl: s.tl, br: s.br,
      })),
      draft,
    ) : { marks: [], slots: [] }),
    [baseMap, draft],
  );
  const marks = proj.marks;
  // "草稿新增"的名单（列表里打标记用）
  const addedNames = useMemo(() => new Set(draft
    .filter((h) => h.kind === "add_mark").map((h) => (h as { name: string }).name)), [draft]);
  const addedSlots = useMemo(() => new Set(draft
    .filter((h) => h.kind === "add_slot").map((h) => (h as { name: string }).name)), [draft]);
  const [submitMsg, setSubmitMsg] = useState<string | null>(null);
  /** P2：离线保存到地图规划文件（不走提案 —— 用户拍板：离线域直改文件） */
  const saveToFile = async (): Promise<void> => {
    if (!selId || draft.length === 0) { setSubmitMsg("草稿是空的"); return; }
    if (selMeta?.locked) { setSubmitMsg("默认地图锁定：先「复制新建」再改"); return; }
    try {
      await saveMapPlan(selId, mapDraftToHunks(draft));
      setDraft([]);
      setSubmitMsg("已保存到地图规划文件（" + selId + "）");
      setPlanPayload(await getMapPlanPayload(selId));
      await refreshList();
    } catch (err) {
      setSubmitMsg("保存被拒：" + (err as Error).message);
    }
  };
  const createMapCopy = async (): Promise<void> => {
    const id = newName.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "-") || undefined;
    try {
      const m = await createMapPlan({ id, title_zh: newName.trim() || "我的地图",
                                      copy_from: selId ?? undefined });
      setNewName("");
      await refreshList();
      await openMapPlan(m.id);
    } catch (err) {
      setPlanMsg("新建失败：" + (err as Error).message);
    }
  };
  const deleteMapPlan = async (): Promise<void> => {
    if (!selId || selMeta?.locked) return;
    try {
      await removeMapPlan(selId);
      const rows = await refreshList();
      const first = rows.find((r) => r.map_name === selMap && r.spawn === spawn) ?? rows[0];
      if (first) await openMapPlan(first.id);
    } catch (err) {
      setPlanMsg("删除失败：" + (err as Error).message);
    }
  };

  if (!baseMap) return <Card title="地图规划"><Empty text="等 static/map…" /></Card>;

  // F16：placeable 查询（合成地形已在夹具里）；地形未下发时 null —— 未知不算非法，如实降级
  const placeableAt = useMemo(() => {
    const t = baseMap.terrain?.placeable;
    if (!t) return null;
    const g = decodeGrid(t);
    return (x: number, y: number): boolean => g.at(Math.floor(x), Math.floor(y)) > 0;
  }, [baseMap]);

  /** 固定建造点预留区（基地/气井/矿脉）：payload 直出，预览/落笔/拖动同用一份
   * （声明必须在 preview memo 之前 —— TDZ 崩溃的教训：引用先于声明 = 黑屏） */
  const reserved = baseMap?.reserved ?? null;

  // F16：放置预览（吸附 + 重叠 + placeable）。hover 算 ghost，点击用**同一结果**落笔 ——
  // 预览与落笔永远一致（不会预览绿、点了却拒）。
  const preview = useMemo(() => {
    if (!placeMode || !hoverPos) return null;
    if (placeMode === "mark") {
      return { kind: "mark" as const, pos: snapToCellCenter(hoverPos), ok: true };
    }
    const pv = previewPlacement({
      mode: "slot", pos: hoverPos, size: slotSize, slots: proj.slots, placeableAt, reserved,
    });
    return { kind: "slot" as const, pos: pv.pos, size: slotSize, ok: pv.ok };
  }, [placeMode, hoverPos, slotSize, proj.slots, placeableAt]);

  /** 规划画布图层：初始强开「可建区」与「矿区」（看得见真实矿脉线/预留区才能摆槽位）；
   * 真持有 state —— 用户可经左下角图层面板自行再关（不常用功能，收进折叠小部件） */
  const [planningLayers, setPlanningLayers] = useState<LayerState>(() =>
    ({ ...defaultLayers(), placeable: true, resources: true }));

  const flashError = (msg: string): void => {
    slotError.current = msg;
    setTimeout(() => { slotError.current = null; }, 2600);
  };

  const reservedReason = (kind: string | null): string =>
    kind === "base" ? "压住基地预留位 —— 主基/分矿的指挥中心要建在这"
      : kind === "geyser" ? "压住气井 —— 精炼厂专属位，别的建筑不可占"
        : "压住矿脉 —— 矿区不可占用";

  const place = (pos: [number, number]): void => {
    if (placeMode === "mark") {
      const snapped = snapToCellCenter(pos);
      const name = nextMarkName(marks.map((m) => m.name));
      setDraft((d) => [...d, { kind: "add_mark", name, pos: snapped }]);
      setSelectedName(name);
      return;
    }
    if (placeMode === "slot") {
      const pv = previewPlacement({
        mode: "slot", pos, size: slotSize, slots: proj.slots, placeableAt, reserved,
      });
      if (!pv.ok) {
        flashError(pv.reason === "overlap"
          ? `与槽位 ${pv.overlaps} 重叠 —— 换个位置`
          : pv.reason === "reserved"
            ? reservedReason(pv.reservedKind)
            : "地形不可建（placeable=0）—— 挪到淡绿可建区");
        return;
      }
      const name = nextMarkName(proj.slots.map((s) => s.name)).replace("mark_", "slot_");
      setDraft((d) => [...d, { kind: "add_slot", name, pos: pv.pos, size: slotSize, slotKind }]);
      setSelectedName(name);
    }
  };
  const slotError = useRef<string | null>(null);

  const rename = (from: string, to: string): void => {
    const clean = to.trim();
    if (!clean || clean === from) return;
    if (marks.some((m) => m.name === clean)) return;   // 查重失败 = 忽略（不打扰）
    setDraft((d) => [...d, { kind: "rename_mark", from, to: clean }]);
    setSelectedName(clean);
  };

  const remove = (name: string): void => {
    setDraft((d) => [...d, { kind: "del_mark", name }]);
  };

  /** 槽位拖动落点：del+add 两条 hunk（与后端"移动"同语义，且不必为 move_slot 再动契约）。
   *  新位置校验与放置同规则（previewPlacement：重叠 + placeable）。 */
  const dropSlot = (name: string, worldPos: [number, number]): void => {
    const cur = proj.slots.find((s) => s.name === name);
    if (!cur) return;
    const others = proj.slots.filter((s) => s.name !== name);
    const pv = previewPlacement({
      mode: "slot", pos: worldPos, size: cur.size, slots: others, placeableAt, reserved,
    });
    if (Math.abs(cur.pos[0] - pv.pos[0]) < 0.01 && Math.abs(cur.pos[1] - pv.pos[1]) < 0.01) {
      return;   // 没动
    }
    if (!pv.ok) {
      flashError(pv.reason === "overlap"
        ? `与槽位 ${pv.overlaps} 重叠 —— 换个位置`
        : pv.reason === "reserved"
          ? reservedReason(pv.reservedKind)
          : "地形不可建（placeable=0）—— 挪到淡绿可建区");
      return;
    }
    setDraft((d) => [...d,
      { kind: "del_slot", name },
      { kind: "add_slot", name, pos: pv.pos, size: cur.size, slotKind: cur.kind },
    ]);
  };

  // F16：选中项变化 → 列表滚到可见（点地图上的槽位/点位，右侧列表跳到对应行）
  const listRef = useRef<HTMLUListElement | null>(null);
  useEffect(() => {
    if (!selectedName || !listRef.current) return;
    const el = listRef.current.querySelector(`[data-name="${CSS.escape(selectedName)}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [selectedName]);

  return (
    <div className="flex h-full min-h-0 gap-3">
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {/* 双下拉模型（用户拍板）：地图 → 出生点 → 规划空间（某图某方的布局集） */}
        <div className="mb-2 flex flex-wrap items-center gap-2">
          {!apiOk ? (
            <span className="text-note text-amber-500">
              地图规划存在后端（runtime/map-plans）—— 先连后端；当前显示夹具底图
            </span>
          ) : mplans ? (
            <>
              <select value={selMap}
                      onChange={(e) => switchSpace(e.target.value, spawn)}
                      className="rounded border border-neutral-700 bg-neutral-950 px-1 text-xs"
                      title="选地图">
                {mapNames.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
              {(["bl", "tr"] as const).map((sp) => (
                <button key={sp}
                        onClick={() => switchSpace(selMap, sp)}
                        className={"rounded border px-2 py-0.5 text-xs "
                          + (spawn === sp
                            ? "border-sky-600 bg-sky-950/40 text-sky-300"
                            : "border-neutral-700 text-neutral-300")}
                        title={sp === "bl" ? "蓝色方出生点（左下）" : "红色方出生点（右上）"}>
                  {sp === "bl" ? "蓝方" : "红方"}
                </button>
              ))}
              <select value={selId ?? ""}
                      onChange={(e) => void openMapPlan(e.target.value)}
                      className="rounded border border-neutral-700 bg-neutral-950 px-1 text-xs"
                      title="这个（地图 × 阵营）空间的布局，随时切换对比">
                {spacePlans.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.title_zh}{p.locked ? "（锁定）" : ""} · {p.slots} 槽位
                  </option>
                ))}
              </select>
              <input className="w-28 rounded border border-neutral-700 bg-neutral-950 px-2 py-1 text-xs"
                     value={newName} onChange={(e) => setNewName(e.target.value)}
                     placeholder="新布局名" />
              <button className="rounded border border-neutral-700 px-2 py-1 text-xs"
                      disabled={!newName.trim()} onClick={() => void createMapCopy()}
                      title="复制当前规划为新布局（空白默认/出厂校准都可复制）">
                复制新建
              </button>
              {selMeta && !selMeta.locked && (
                <button className="rounded border border-neutral-700 px-2 py-1 text-xs text-red-300"
                        onClick={() => void deleteMapPlan()}>删除</button>
              )}
            </>
          ) : (
            <span className="text-note text-faint">加载地图规划…</span>
          )}
          {planMsg && <span className="text-note text-amber-400">{planMsg}</span>}
        </div>

        <div className="relative min-h-0 flex-1 overflow-hidden rounded border border-neutral-800">
          <MapCanvas
            map={baseMap}
            /* 规划视图只画地图规划本身（槽位/点位/地形）—— 不叠加夹具的
               world/economy 帧：夹具是 bl 开局，出生点切到红方时基地/工人
               仍画在蓝方位置，用户会误读为「两个出生点基地一样」（实测踩过）。 */
            world={null}
            production={null}
            economy={null}
            catalog={catalog}
            layers={planningLayers}
            reserved={reserved}
            smooth={false}
            selection={null}
            onSelect={() => {}}
            marksOverride={marks}
            slotsOverride={proj.slots}
            onBlankClick={placeMode ? place : () => setSelectedName(null)}
            draggableSlots={placeMode === null ? proj.slots : null}
            onSlotDrop={dropSlot}
            ghost={preview}
            onHover={setHoverPos}
            onMarkClick={setSelectedName}
            onSlotClick={setSelectedName}
            selectedName={selectedName}
          />
          <LayerPanel layers={planningLayers} onChange={setPlanningLayers}
                      map={baseMap} world={null}
                      extra={(
                        <div className="mt-1 border-t border-neutral-800 pt-1 text-ghost">
                          可建区/矿区初始强制开 —— 编辑要在真实背景上放
                        </div>
                      )} />
          {placeMode && (
            <div className="pointer-events-none absolute bottom-8 left-2 rounded border border-amber-700 bg-amber-950/80 px-2 py-1 text-note text-amber-300">
              {placeMode === "mark"
                ? "放置点位：点击地图（吸附格心）· 再点按钮退出"
                : `放置槽位 ${slotSize}×${slotSize}（${slotKind}）：点击地图（吸附格心）· 悬停看绿/红预览`}
            </div>
          )}
          {slotError.current && (
            <div className="pointer-events-none absolute bottom-16 left-2 rounded border border-red-700 bg-red-950/85 px-2 py-1 text-note text-red-300">
              {slotError.current}
            </div>
          )}
        </div>
        <div className="mt-1 text-note text-ghost">
          菱形 = 点位标记 · 实线框 = 建造槽位（F16：编辑时实线 + 名字）· 淡绿底 = 可建区
          · 点击标记/槽位 ↔ 右侧列表高亮。草稿合并显示：新增/改名/删除即时可见。
        </div>
      </div>

      <div className="flex w-80 shrink-0 flex-col gap-3">
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
                悬停看绿/红预览：绿 = 可放（不重叠 + placeable=1）；重叠/不可建当场拒
              </span>
            </div>
          )}
          <div className="mt-2 border-t border-neutral-800 pt-2 text-note text-ghost">
            草稿在内存，保存写进所选地图规划文件（不走提案）；
            默认地图锁定 —— 复制一份再改。
          </div>
          <button
            disabled={draft.length === 0 || selMeta?.locked || !selId}
            title={selMeta?.locked ? "默认地图锁定：复制新建后再改"
              : draft.length === 0 ? "草稿是空的" : "hunks 应用到该出生点分支（同一套校验）"}
            className="mt-2 w-full rounded border border-emerald-700 bg-emerald-900/40 px-2 py-1 text-note text-emerald-200 disabled:opacity-50"
            onClick={() => void saveToFile()}
          >保存到地图规划文件{selMeta?.locked ? "（默认锁定）" : ""}</button>
          {submitMsg && <div className="mt-1 text-note text-amber-400">{submitMsg}</div>}
        </Card>

        {/* 用户拍板去掉切换选项卡：点位与槽位两组**常显堆叠**，双向高亮直接可见 */}
        <Card title="点位与槽位"
              right={<span className="text-note text-faint">
                点位 {marks.length} · 槽位 {proj.slots.length}
              </span>}>
          <ul ref={listRef} className="h-64 space-y-1 overflow-auto">
            <li className="pt-1 text-note text-ghost">点位（名字回车/失焦即改；× 删除。点击行 ↔ 地图高亮）</li>
            {marks.map((m) => (
              <li key={m.name} data-name={m.name}
                  onClick={() => setSelectedName(m.name)}
                  className={"flex cursor-pointer items-center gap-1 rounded px-1 py-0.5 "
                    + (selectedName === m.name
                      ? "border border-sky-700 bg-sky-950/40"
                      : "border border-transparent hover:bg-neutral-900/60")}>
                <span title="地图上的菱形标记">◆</span>
                <input
                  defaultValue={m.name}
                  onFocus={() => setSelectedName(m.name)}
                  onBlur={(e) => rename(m.name, e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                  className={"w-28 rounded border border-transparent bg-transparent px-1 py-0.5 "
                    + "hover:border-neutral-700 focus:border-sky-700 focus:bg-neutral-950 " + T.mono}
                />
                <span className={"ml-auto text-note text-faint " + T.mono}>
                  {m.pos[0].toFixed(1)}, {m.pos[1].toFixed(1)}
                </span>
                <button
                  className="shrink-0 text-red-400/70 hover:text-red-300"
                  title={"删除" + (addedNames.has(m.name) ? "（草稿新增）" : "（草稿覆盖静态点位）")}
                  onClick={(e) => { e.stopPropagation(); remove(m.name); }}
                >×</button>
              </li>
            ))}
            <li className="pt-2 text-note text-ghost">建造槽位（点击行 ↔ 地图高亮；图上拖动 = 删 + 新位置两条 hunk）</li>
            {proj.slots.map((s) => (
              <li key={s.name} data-name={s.name}
                  onClick={() => setSelectedName(s.name)}
                  title={selectedName === s.name ? "已选中（地图上高亮）" : "点击选中（地图高亮）"}
                  className={"flex cursor-pointer items-center gap-1 rounded px-1 py-0.5 "
                    + (selectedName === s.name
                      ? "border border-sky-700 bg-sky-950/40"
                      : "border border-transparent hover:bg-neutral-900/60")}>
                <span className="font-medium text-neutral-300">{s.name}</span>
                <span className="text-faint">{SLOT_KIND_ZH[s.kind] ?? s.kind} {s.size}×{s.size}</span>
                <span className={"ml-auto text-note text-ghost " + T.mono}>
                  {s.pos[0].toFixed(1)},{s.pos[1].toFixed(1)}
                </span>
                <button
                  className="shrink-0 text-red-400/70 hover:text-red-300"
                  title={addedSlots.has(s.name) ? "删除（草稿新增）" : "删除（草稿覆盖静态槽位）"}
                  onClick={(e) => {
                    e.stopPropagation();
                    setDraft((d) => [...d, { kind: "del_slot", name: s.name }]);
                  }}
                >×</button>
              </li>
            ))}
            {(reserved ?? []).filter((r) => r.name).length > 0 && (
              <li className="pt-2 text-note text-ghost">
                预设固定建造点（地图数据，不可编辑删除 —— 后续规划的放置引用直接用它们）
              </li>
            )}
            {(reserved ?? []).filter((r) => r.name).map((r) => (
              <li key={"preset-" + r.name} title="预设固定建造点：基地/气井脚印，锁定"
                  className="flex cursor-default items-center gap-1 rounded border border-dashed border-neutral-800 px-1 py-0.5 text-faint">
                <span>{r.kind === "base" ? "▣" : "◈"}</span>
                <span className={T.mono}>{r.name}</span>
                <span className="ml-auto text-note text-ghost">{r.kind === "base" ? "基地" : "气井"}</span>
              </li>
            ))}
          </ul>
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
  const [plans, setPlans] = useState<PlanMeta[] | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [title, setTitle] = useState("");
  const [items, setItems] = useState<DraftItem[]>([]);
  const [dirty, setDirty] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [newName, setNewName] = useState("");
  const [horizon, setHorizon] = useState(300);
  /** P0：试算（真 planner 干跑，不需要会话）—— 草稿板 + 已保存对照板，同一组件 */
  const [sim, setSim] = useState<PlanSimResult | null>(null);
  const [baseSim, setBaseSim] = useState<PlanSimResult | null>(null);
  /** 对照板默认收起（用户拍板：平时只看草稿，不占横屏） */
  const [showBase, setShowBase] = useState(false);

  const options = useMemo(() => placementOptions(map), [map]);
  const cost = useMemo(() => draftCost(items, catalog), [items, catalog]);
  const buildables = useMemo(() => (catalog?.entries ?? []).filter(
    (e: CatalogStatic["entries"][number]) =>
      e.role === "building" || e.role === "combat" || e.role === "worker"), [catalog]);
  const researchables = useMemo(
    () => (catalog?.entries ?? []).filter((e) => e.role === "upgrade"), [catalog]);
  const zhOf = useMemo(() => {
    const m = new Map<string, string>();
    for (const e of catalog?.entries ?? []) m.set(e.stable_id, e.display_name_zh);
    return (id: string | null) => (id ? m.get(id) ?? id : "—");
  }, [catalog]);

  const refresh = async (): Promise<PlanMeta[]> => {
    const rows = await listPlans();
    setPlans(rows);
    return rows;
  };

  const open = async (id: string): Promise<void> => {
    const p = await getPlan(id);
    setPlan(p);
    setTitle(p.title_zh);
    setItems(draftFromJson(p.queue));
    setDirty(false);
    setMsg(null);
    setSim(null);
    setBaseSim(p.queue.length > 0
      ? await simulatePlan(p.queue, { horizon, planId: p.id }).catch(() => null)
      : null);
  };

  useEffect(() => {
    if (!apiOk) return;
    void refresh()
      .then((rows) => rows[0] && open(rows[0].id))
      .catch((err: Error) => setMsg("加载规划失败：" + err.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只在 apiOk 变化时拉一次
  }, [apiOk]);

  const update = (id: string, patch: Partial<DraftItem>): void => {
    setItems((list) => list.map((it) => (it.id === id ? { ...it, ...patch } : it)));
    setDirty(true);
  };

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

  const save = async (): Promise<void> => {
    if (!plan || plan.locked) return;
    try {
      const p = await savePlan(plan.id, {
        title_zh: title || plan.title_zh, map: plan.map, spawn: plan.spawn,
        queue: items.map(itemToJson),
      });
      setPlan(p);
      setTitle(p.title_zh);
      setDirty(false);
      setMsg("已保存 " + p.id);
      setBaseSim(p.queue.length > 0
        ? await simulatePlan(p.queue, { horizon, planId: p.id }).catch(() => null)
        : null);
      await refresh();
    } catch (err) {
      setMsg("保存失败：" + (err as Error).message);
    }
  };

  const create = async (copyFrom: string | null): Promise<void> => {
    const id = newName.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "-") || undefined;
    try {
      const p = await createPlan({
        id, title_zh: newName.trim() || "新规划",
        copy_from: copyFrom ?? undefined,
      });
      setNewName("");
      await refresh();
      await open(p.id);
    } catch (err) {
      setMsg("新建失败：" + (err as Error).message);
    }
  };

  const del = async (): Promise<void> => {
    if (!plan || plan.locked) return;
    try {
      await removePlan(plan.id);
      const rows = await refresh();
      if (rows[0]) await open(rows[0].id);
    } catch (err) {
      setMsg("删除失败：" + (err as Error).message);
    }
  };

  /** 试算 = 后端真 planner 从标准开局干跑（A3/C7：前端不本地算；不需要会话） */
  const simulate = async (): Promise<void> => {
    if (items.length === 0) { setSim(null); setMsg("草稿是空的"); return; }
    setBusy(true);
    try {
      setSim(await simulatePlan(items.map(itemToJson),
                                { horizon, planId: plan?.id }));
      setMsg(null);
    } catch (err) {
      setSim(null);
      setMsg("试算失败：" + (err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!apiOk) {
    return <Card title="生产规划">
      <Empty text="规划存在后端（python tools/serve_api.py，一个规划一个 YAML 文件）—— 先连后端" />
    </Card>;
  }
  if (!plans) return <Card title="生产规划"><Empty text="加载规划列表…" /></Card>;

  return (
    <div className="space-y-3">
    <Card title="生产规划" right={<span className="text-note text-faint">
      矿 {cost.minerals} · 气 {cost.vespene} · 供给 {cost.supply}
      {cost.missing.length > 0 && (
        <span className="ml-1 text-amber-400">缺目录项：{cost.missing.join(", ")}</span>
      )}
    </span>}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <select value={plan?.id ?? ""}
                onChange={(e) => { void open(e.target.value); }}
                className="rounded border border-neutral-700 bg-neutral-950 px-1 text-xs">
          {plans.map((p) => (
            <option key={p.id} value={p.id}>
              {p.title_zh}（{p.id}{p.locked ? "，锁定" : ""}，{p.items} 项）
            </option>
          ))}
        </select>
        {plan?.locked ? (
          <span className="text-note text-amber-500">
            默认规划锁定（参考基准）：要改先复制一份
          </span>
        ) : (
          <button className="rounded border border-emerald-700 bg-emerald-900/40 px-2 py-1 text-xs text-emerald-200"
                  disabled={!dirty} title={dirty ? undefined : "没有未保存的修改"}
                  onClick={() => void save()}>保存{dirty ? "（未保存）" : ""}</button>
        )}
        {!plan?.locked && plan && (
          <button className="rounded border border-neutral-700 px-2 py-1 text-xs text-red-300"
                  onClick={() => void del()}>删除</button>
        )}
        <input className="w-40 rounded border border-neutral-700 bg-neutral-950 px-2 py-1 text-xs"
               value={newName} onChange={(e) => setNewName(e.target.value)}
               placeholder="新规划名（中文名）" />
        <button className="rounded border border-neutral-700 px-2 py-1 text-xs"
                disabled={!newName.trim()}
                title="复制当前规划（默认路径：默认规划 → 你的开局）"
                onClick={() => void create(plan?.id ?? null)}>复制新建</button>
        <button className="rounded border border-neutral-700 px-2 py-1 text-xs"
                disabled={!newName.trim()} onClick={() => void create(null)}>空白新建</button>
        <span className="text-note text-faint">
          地图 {plan?.map ?? "—"} · 出生点 {plan?.spawn ?? "—"}
        </span>
      </div>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <button className="rounded border border-neutral-700 px-2 py-1 text-xs"
                onClick={() => { setItems((l) => [...l, emptyItem()]); setDirty(true); }}>+ 加一项</button>
        <button className="rounded border border-neutral-700 px-2 py-1 text-xs"
                onClick={() => { setItems([]); setDirty(true); }}>清空</button>
        <span className="text-note text-ghost">
          build 可选 placement（执行期才必需）；train / research / assign_workers 不需要
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
                  <option value="research">research</option>
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
                      {(it.op === "research" ? researchables : buildables).map((e) => (
                        <option key={e.stable_id} value={e.stable_id}>
                          {e.display_name_zh}（{e.cost.minerals}/{e.cost.vespene}，{e.build_time}s）
                        </option>
                      ))}
                    </select>
                    <input type="number" min={1} value={it.count}
                           onChange={(e) => update(it.id, { count: Number(e.target.value) })}
                           className="w-16 rounded border border-neutral-700 bg-neutral-950 px-1 text-xs" />
                    {it.op === "build" && (
                    <select
                      value={it.placement
                        ? (it.placement.kind === "exact" ? "slot:" + it.placement.mark : "region:" + it.placement.region)
                        : ""}
                      onChange={(e) => setPlacement(it.id, e.target.value)}
                      className="rounded border border-neutral-700 bg-neutral-950 px-1 text-xs">
                      <option value="">（placement）</option>
                      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                    )}
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
               value={title} onChange={(e) => { setTitle(e.target.value); setDirty(true); }}
               placeholder="规划名" />
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-note text-faint">
            视野
            <input type="number" min={60} max={600} step={60} value={horizon}
                   onChange={(e) => setHorizon(Number(e.target.value))}
                   className="ml-1 w-16 rounded border border-neutral-700 bg-neutral-950 px-1 text-xs" />
            秒
          </label>
          <button className="rounded border border-sky-700 bg-sky-900/40 px-3 py-1 text-xs text-sky-200"
                  title="标准开局（基地 + 12 SCV）起步，后端真 planner 逐秒干跑 —— 不需要会话"
                  disabled={busy || items.length === 0}
                  onClick={() => void simulate()}>
            {busy ? "试算中…" : "试算"}
          </button>
          <span className="text-note text-ghost">
            试算 = 标准开局起步的真 planner 干跑；卡点（缺矿/缺气/前置没）就是前瞻警报
          </span>
        </div>
        {msg && <div className="text-xs text-amber-400">{msg}</div>}
      </div>
    </Card>

      {/* 草稿试算占满整宽；「已保存对照」默认收起（用户拍板：平时不对比，省空间） */}
      {sim && (
        <div className="space-y-3">
          {baseSim && (
            <div>
              <button className="rounded border border-neutral-700 px-2 py-1 text-xs text-faint"
                      onClick={() => setShowBase((v) => !v)}>
                {showBase ? "▾ 收起对照 —— 草稿占满整宽" : "▸ 对照已保存规划"}
              </button>
            </div>
          )}
          <div className={showBase && baseSim ? "grid grid-cols-1 gap-3 2xl:grid-cols-2" : ""}>
            <Card title={"草稿试算" + (dirty ? "（未保存）" : "")}>
              <ProjectionBoard frame={sim} catalog={catalog} zhOf={zhOf}
                               initialDomain={{ from: 0, to: Math.max(60, sim.horizon) }} />
            </Card>
            {showBase && baseSim && (
              <Card title={"已保存对照 · " + (plan?.title_zh ?? "")}>
                <ProjectionBoard frame={baseSim} catalog={catalog} zhOf={zhOf}
                                 initialDomain={{ from: 0, to: Math.max(60, baseSim.horizon) }} />
              </Card>
            )}
          </div>
        </div>
      )}

      {/* 前瞻警报：与实时风险面板同一数据模型（后端 AlertView）+ 同一渲染组件 */}
      {sim && (sim.alerts.length > 0 || sim.skipped.length > 0) && (
        <Card title={"前瞻警报（" + sim.alerts.length + "）"}>
          <div className="space-y-1">
            <AlertsList alerts={sim.alerts} empty={<Empty text="没有卡点：规划全程可行" />} />
            {sim.skipped.map((s, i) => (
              <div key={"s" + i} className="text-xs text-faint">
                {s.op} 没进投影：{s.reason}
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
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