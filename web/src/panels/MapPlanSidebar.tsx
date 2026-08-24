/**
 * 地图规划抽屉（2026-08-22 左抽屉轮）—— 原地图页的顶栏工具行 + 右侧两卡收进左侧：
 * 规划空间（地图×出生点×布局）/ 放置工具 / 点位与槽位列表。主区只剩画布。
 * 状态全在 planning/map-plan-store（导航/收起不丢）。
 */
import { useEffect, useMemo, useRef } from "react";
import { useBaseMap, useMapPlanStore, useMapProj } from "../planning/map-plan-store";
import { Card, Empty } from "../shell/ui";
import { T } from "../shell/tokens";

/** 槽位类别的 zh（UI 铬件文案；点位/建筑名等数据 zh 一律来自后端，C4） */
const SLOT_KIND_ZH: Record<string, string> = {
  supply: "补给", production: "生产", addon: "挂件",
};

export function MapPlanSidebar() {
  const st = useMapPlanStore();
  const baseMap = useBaseMap();
  const proj = useMapProj();
  const marks = proj.marks;
  const selMeta = st.mplans?.find((p) => p.id === st.selId) ?? null;

  // 批 2（双分支）：蓝红是**同一规划的分支视图**（不再按出生点过滤规划清单）——
  // 地图下拉选空间；规划下拉列该地图全部布局（dual 一份两看，旧单分支各归各）
  const mapNames = useMemo(
    () => [...new Set((st.mplans ?? []).map((p) => p.map_name))], [st.mplans]);
  const spacePlans = useMemo(
    () => (st.mplans ?? []).filter((p) => p.map_name === st.selMap),
    [st.mplans, st.selMap]);

  // "草稿新增"的名单（列表里打标记用）
  const addedNames = useMemo(() => new Set(st.draft
    .filter((h) => h.kind === "add_mark").map((h) => (h as { name: string }).name)), [st.draft]);
  const addedSlots = useMemo(() => new Set(st.draft
    .filter((h) => h.kind === "add_slot").map((h) => (h as { name: string }).name)), [st.draft]);

  // 选中项变化 → 列表滚到可见（点地图上的槽位/点位，抽屉列表跳到对应行）
  const listRef = useRef<HTMLUListElement | null>(null);
  useEffect(() => {
    if (!st.selectedName || !listRef.current) return;
    const el = listRef.current.querySelector(`[data-name="${CSS.escape(st.selectedName)}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [st.selectedName]);

  const rename = (from: string, to: string): void => {
    const clean = to.trim();
    if (!clean || clean === from) return;
    if (marks.some((m) => m.name === clean)) return;   // 查重失败 = 忽略（不打扰）
    st.pushHunks({ kind: "rename_mark", from, to: clean });
    st.patch({ selectedName: clean });
  };

  return (
    <>
      <Card title="规划空间">
        <div className="flex flex-wrap items-center gap-1.5">
          <select value={st.selMap}
                  onChange={(e) => st.switchSpace(e.target.value, st.spawn)}
                  className="min-w-0 flex-1 rounded border border-l2 bg-inset px-1 text-label"
                  title="选地图">
            {mapNames.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
          {(["bl", "tr"] as const).map((sp) => (
            <button key={sp}
                    onClick={() => st.switchSpace(st.selMap, sp)}
                    className={"rounded border px-2 py-0.5 text-label "
                      + (st.spawn === sp
                        ? "border-accent-blue bg-blue-soft font-medium text-strong"
                        : "border-l2 text-dim")}
                    title={sp === "bl" ? "蓝色方分支（左下）—— 同一规划的另一侧" : "红色方分支（右上）—— 同一规划的另一侧"}>
              {sp === "bl" ? "蓝方" : "红方"}
            </button>
          ))}
        </div>
        <select value={st.selId ?? ""}
                onChange={(e) => void st.openMapPlan(e.target.value)}
                className="mt-1.5 w-full rounded border border-l2 bg-inset px-1 text-label"
                title="这个（地图 × 阵营）空间的布局，随时切换对比">
          {spacePlans.length === 0 && <option value="">（该空间没有布局）</option>}
          {spacePlans.map((p) => (
            <option key={p.id} value={p.id}>
              {p.title_zh}{p.locked ? "（锁定）" : ""} · {p.slots} 槽位
            </option>
          ))}
        </select>
        <div className="mt-1.5 flex items-center gap-1.5">
          <input className="min-w-0 flex-1 rounded border border-l2 bg-inset px-2 py-1 text-label"
                 value={st.newName} onChange={(e) => st.patch({ newName: e.target.value })}
                 placeholder="新布局名" />
          <button className="btn btn-ghost"
                  disabled={!st.newName.trim()} onClick={() => void st.createMapCopy()}
                  title="复制当前规划为新布局（空白默认/出厂校准都可复制）">
            复制新建
          </button>
        </div>
        {selMeta && !selMeta.locked && (
          <button className="btn btn-danger mt-1.5 w-full justify-center"
                  onClick={() => void st.deleteMapPlan()}>删除这个布局</button>
        )}
        {st.planMsg && <div className="mt-1 text-note text-[color:var(--warn-fg)]">{st.planMsg}</div>}
      </Card>

      <Card title="放置工具">
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => st.patch({ placeMode: st.placeMode === "mark" ? null : "mark" })}
            className={"btn " + (st.placeMode === "mark" ? "btn-warn" : "btn-ghost")}
          >{st.placeMode === "mark" ? "退出放置点位" : "＋ 点位"}</button>
          <button
            onClick={() => st.patch({ placeMode: st.placeMode === "slot" ? null : "slot" })}
            className={"btn " + (st.placeMode === "slot" ? "btn-warn" : "btn-ghost")}
          >{st.placeMode === "slot" ? "退出放置槽位" : "＋ 槽位"}</button>
        </div>
        {st.placeMode === "slot" && (
          <div className="mt-2 flex flex-wrap items-center gap-1 border-t border-l1 pt-2">
            {([2, 3, 5] as const).map((sz) => (
              <button key={sz}
                      onClick={() => st.patch({ slotSize: sz })}
                      className={"rounded border px-1.5 py-0.5 text-note " + (st.slotSize === sz
                        ? "border-accent-blue bg-blue-soft font-medium text-strong"
                        : "border-l2 text-dim")}
              >{sz}×{sz}</button>
            ))}
            {(["supply", "production", "addon"] as const).map((k) => (
              <button key={k}
                      onClick={() => st.patch({ slotKind: k })}
                      className={"rounded border px-1.5 py-0.5 text-note " + (st.slotKind === k
                        ? "border-accent-blue bg-blue-soft font-medium text-strong"
                        : "border-l2 text-dim")}
              >{SLOT_KIND_ZH[k]}</button>
            ))}
            <span className="w-full text-note text-ghost">
              悬停看绿/红预览：绿 = 可放（不重叠 + placeable=1）；重叠/不可建当场拒
            </span>
          </div>
        )}
        <div className="mt-2 border-t border-l1 pt-2 text-note text-ghost">
          草稿在内存，保存写进所选地图规划文件（不走提案）；
          默认地图锁定 —— 复制一份再改。
        </div>
        <button
          disabled={st.draft.length === 0 || selMeta?.locked || !st.selId}
          title={selMeta?.locked ? "默认地图锁定：复制新建后再改"
            : st.draft.length === 0 ? "草稿是空的" : "hunks 应用到该出生点分支（同一套校验）"}
          className="btn btn-ok mt-2 w-full justify-center"
          onClick={() => void st.saveToFile()}
        >保存到地图规划文件{selMeta?.locked ? "（默认锁定）" : ""}</button>
        {st.submitMsg && <div className="mt-1 text-note text-[color:var(--warn-fg)]">{st.submitMsg}</div>}
      </Card>

      {/* 用户拍板去掉切换选项卡：点位与槽位两组**常显堆叠**，双向高亮直接可见 */}
      <Card title="点位与槽位" help="槽位名是简写标记（rev 14）：D=2×2 补给类建造区（人族补给站/神族水晶塔）、R/F/S=3×3 生产类建造区（字母是人族助记，本质是生产区）、+ = 2×2 挂件/附属区。标记与种族无关——本质是「尺寸+类别」的建造区；括号里的中文名（补给站1）只是展示别名。"
            right={<span className="text-note text-faint">
              点位 {marks.length} · 槽位 {proj.slots.length}
            </span>}>
        {!baseMap ? <Empty text="等 static/map…" /> : (
          <ul ref={listRef} className="space-y-1">
            <li className="pt-1 text-note text-ghost">点位（名字回车/失焦即改；× 删除。点击行 ↔ 地图高亮）</li>
            {marks.map((m) => (
              <li key={m.name} data-name={m.name}
                  onClick={() => st.patch({ selectedName: m.name })}
                  className={"flex cursor-pointer items-center gap-1 rounded px-1 py-0.5 "
                    + (st.selectedName === m.name
                      ? "border border-l2 bg-active"
                      : "border border-transparent hover:bg-raised")}>
                <span title="地图上的菱形标记">◆</span>
                <input
                  defaultValue={m.name}
                  onFocus={() => st.patch({ selectedName: m.name })}
                  onBlur={(e) => rename(m.name, e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                  className={"w-24 rounded border border-transparent bg-transparent px-1 py-0.5 "
                    + "hover:border-l2 focus:border-accent-blue focus:bg-inset " + T.mono}
                />
                <span className={"ml-auto text-note text-faint " + T.mono}>
                  {m.pos[0].toFixed(1)},{m.pos[1].toFixed(1)}
                </span>
                <button
                  className="shrink-0 text-[color:var(--err-fg)] hover:opacity-70"
                  title={"删除" + (addedNames.has(m.name) ? "（草稿新增）" : "（草稿覆盖静态点位）")}
                  onClick={(e) => {
                    e.stopPropagation();
                    st.pushHunks({ kind: "del_mark", name: m.name });
                  }}
                >×</button>
              </li>
            ))}
            <li className="pt-2 text-note text-ghost">建造槽位（点击行 ↔ 地图高亮；图上拖动 = 删 + 新位置两条 hunk）</li>
            {proj.slots.map((s) => (
              <li key={s.name} data-name={s.name}
                  onClick={() => st.patch({ selectedName: s.name })}
                  title={st.selectedName === s.name ? "已选中（地图上高亮）" : "点击选中（地图高亮）"}
                  className={"flex cursor-pointer items-center gap-1 rounded px-1 py-0.5 "
                    + (st.selectedName === s.name
                      ? "border border-l2 bg-active"
                      : "border border-transparent hover:bg-raised")}>
                <span className="font-medium text-dim" title={`标记名 ${s.name}`}>{s.aliasZh || s.name}</span>
                <span className="text-faint">{s.aliasZh ? `${s.name} · ` : ""}{SLOT_KIND_ZH[s.kind] ?? s.kind} {s.size}×{s.size}</span>
                <span className={"ml-auto text-note text-ghost " + T.mono}>
                  {s.pos[0].toFixed(1)},{s.pos[1].toFixed(1)}
                </span>
                <button
                  className="shrink-0 text-[color:var(--err-fg)] hover:opacity-70"
                  title={addedSlots.has(s.name) ? "删除（草稿新增）" : "删除（草稿覆盖静态槽位）"}
                  onClick={(e) => {
                    e.stopPropagation();
                    st.pushHunks({ kind: "del_slot", name: s.name });
                  }}
                >×</button>
              </li>
            ))}
            {(baseMap.reserved ?? []).filter((r) => r.name).length > 0 && (
              <li className="pt-2 text-note text-ghost">
                预设固定建造点（地图数据，不可编辑删除 —— 后续规划的放置引用直接用它们）
              </li>
            )}
            {(baseMap.reserved ?? []).filter((r) => r.name).map((r) => (
              <li key={"preset-" + r.name} title="预设固定建造点：基地/气井脚印，锁定"
                  className="flex cursor-default items-center gap-1 rounded border border-dashed border-l1 px-1 py-0.5 text-faint">
                <span>{r.kind === "base" ? "▣" : "◈"}</span>
                <span className={T.mono}>{r.name}</span>
                <span className="ml-auto text-note text-ghost">{r.kind === "base" ? "基地" : "气井"}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </>
  );
}
