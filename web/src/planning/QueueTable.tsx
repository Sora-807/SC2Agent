/**
 * 队列编辑表（2026-08-22 十八轮抽取共享）—— 规划生产页与复盘生产页的同一个编辑器：
 * 规划页编辑规划文件草稿（queue-store），复盘页编辑「从会话队列提取的草稿」
 * （draftFromSessionQueues，转存为新规划文件）。数据面归调用方，这里只有编辑交互：
 * 行间悬浮 ＋ 插入、行尾 ↑↓ 换序、× 删除、op/目标/数量/放置 的就地修改。
 *
 * 密集列表纪律（用户十六轮拍板）：控件无框化（hover/focus 才显底）+ 斑马纹做行节奏。
 */
import { useMemo } from "react";
import type { CatalogStatic } from "../contract";
import { emptyItem, type DraftItem } from "./queue-draft";
import { useFrames } from "../store/frames";

/** 行间插入位：两行之间的一条极窄缝隙，hover 时浮出「＋ 在此插入」——
 *  表格化排版的插入交互（用户拍板 2026-08-22：复用复盘页布局 + 行间 + 号）。 */
function RowWithInsert(props: {
  index: number;
  onInsert: () => void;
  children: React.ReactNode;
}) {
  return (
    <>
      {props.index > 0 && (
        <tr className="insert-row group/ins">
          <td colSpan={6} className="p-0">
            <button
              className="flex h-0 w-full items-center justify-center overflow-hidden rounded text-note text-faint transition-all duration-150 group-hover/ins:h-6 hover:bg-inset hover:text-strong"
              title={"在第 " + props.index + " 项前插入"}
              onClick={props.onInsert}
            >＋ 在此插入</button>
          </td>
        </tr>
      )}
      {props.children}
    </>
  );
}

export function QueueTable(props: {
  items: DraftItem[];
  update: (id: string, patch: Partial<DraftItem>) => void;
  setItems: (fn: (l: DraftItem[]) => DraftItem[]) => void;
  /** 放置引用的选项（规划页来自所选地图规划；复盘页来自帧源的 map 静态面） */
  options: { value: string; label: string }[];
}) {
  const catalog = useFrames((s) => s.catalog);
  const { items, update, setItems } = props;

  const buildables = useMemo(() => (catalog?.entries ?? []).filter(
    (e: CatalogStatic["entries"][number]) =>
      e.role === "building" || e.role === "combat" || e.role === "worker"), [catalog]);
  const researchables = useMemo(
    () => (catalog?.entries ?? []).filter((e) => e.role === "upgrade"), [catalog]);

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

  /** select 回显：exact mark 可能落在 slot:（限定/普通槽位）或 mark:（预设名）两组
   *  选项里，取存在的那组 —— 否则预设名会被回显成空。 */
  const placementValue = (it: DraftItem): string => {
    if (!it.placement) return "";
    if (it.placement.kind !== "exact") return "region:" + it.placement.region;
    const m = it.placement.mark;
    return props.options.some((o) => o.value === "mark:" + m) ? "mark:" + m : "slot:" + m;
  };

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <button className="btn btn-ghost"
                onClick={() => setItems((l) => [...l, emptyItem()])}>+ 加一项</button>
        <button className="btn btn-ghost"
                onClick={() => setItems(() => [])}>清空</button>
      </div>

      {items.length === 0 ? (
        <div className="text-ghost">还没有草稿项（行间悬浮 + 可插入）</div>
      ) : (
        <table className="w-full text-left">
          <thead className="sticky top-0 z-10 bg-panel text-faint">
            <tr>
              <th className="w-8">#</th><th className="w-24">op</th>
              <th>目标</th><th className="w-20">数量</th>
              <th className="w-44">放置</th><th className="w-24" />
            </tr>
          </thead>
          <tbody>
            {items.map((it, i) => (
              <RowWithInsert key={it.id} index={i} onInsert={() => {
                setItems((l) => {
                  const next = [...l];
                  next.splice(i, 0, emptyItem());
                  return next;
                });
              }}>
                <tr className="group/row odd:bg-raised hover:bg-inset">
                  <td className="text-ghost">{i}.</td>
                  <td>
                    <select value={it.op}
                            onChange={(e) => update(it.id, { op: e.target.value as DraftItem["op"] })}
                            className="rounded px-1 text-label hover:bg-inset focus:bg-inset">
                      <option value="build">build</option>
                      <option value="train">train</option>
                      <option value="research">research</option>
                      <option value="assign_workers">assign_workers</option>
                    </select>
                  </td>
                  <td>
                    {it.op === "assign_workers" ? (
                      <span className="flex items-center gap-1">
                        <select value={it.task ?? "gas"}
                                onChange={(e) => update(it.id, { task: e.target.value as DraftItem["task"] })}
                                className="rounded px-1 text-label hover:bg-inset focus:bg-inset">
                          <option value="mineral">维持采矿</option>
                          <option value="gas">维持采气</option>
                          <option value="idle">维持空闲</option>
                        </select>
                        <span className="text-note text-ghost">目标值（幂等）</span>
                      </span>
                    ) : (
                      <select value={it.type ?? ""}
                              onChange={(e) => update(it.id, { type: e.target.value || null })}
                              className="w-full rounded px-1 text-label hover:bg-inset focus:bg-inset">
                        <option value="">（选类型）</option>
                        {(it.op === "research" ? researchables : buildables).map((e) => (
                          <option key={e.stable_id} value={e.stable_id}>
                            {e.display_name_zh}（{e.cost.minerals}/{e.cost.vespene}，{e.build_time}s）
                          </option>
                        ))}
                      </select>
                    )}
                  </td>
                  <td>
                    <input type="number" min={it.op === "assign_workers" ? 0 : 1} value={it.count}
                           onChange={(e) => update(it.id, { count: Number(e.target.value) })}
                           className="w-16 rounded px-1 text-label hover:bg-inset focus:bg-inset" />
                  </td>
                  <td>
                    {it.op === "build" ? (
                      <select
                        value={placementValue(it)}
                        onChange={(e) => setPlacement(it.id, e.target.value)}
                        className="w-full rounded px-1 text-label hover:bg-inset focus:bg-inset">
                        <option value="">（placement）</option>
                        {props.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                      </select>
                    ) : <span className="text-ghost">—</span>}
                  </td>
                  <td className="whitespace-nowrap text-right">
                    <button
                      className="mr-0.5 rounded border border-l2 px-1 text-note opacity-0 transition-opacity group-hover/row:opacity-100"
                      disabled={i === 0} title="上移"
                      onClick={() => setItems((l) => {
                        const next = [...l];
                        [next[i - 1], next[i]] = [next[i]!, next[i - 1]!];
                        return next;
                      })}>↑</button>
                    <button
                      className="mr-0.5 rounded border border-l2 px-1 text-note opacity-0 transition-opacity group-hover/row:opacity-100"
                      disabled={i === items.length - 1} title="下移"
                      onClick={() => setItems((l) => {
                        const next = [...l];
                        [next[i + 1], next[i]] = [next[i]!, next[i + 1]!];
                        return next;
                      })}>↓</button>
                    <button
                      className="rounded border border-l2 px-1 text-[color:var(--err-fg)] opacity-60 transition-opacity hover:opacity-100"
                      title="删除"
                      onClick={() => setItems((l) => l.filter((x) => x.id !== it.id))}>×</button>
                  </td>
                </tr>
              </RowWithInsert>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
