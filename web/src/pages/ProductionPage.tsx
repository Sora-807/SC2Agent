/**
 * 生产页（F3）—— 队列 + 经济 + 投影 Gantt，三区固定占据视口（2026-08-22
 * 用户拍板：各区内部滚动，页面本身不滚；在途建造/目录 tabs 已删）。
 *
 * 一条诚实性纪律：投影的 `source` 现在只能是 `draft`（planner 吃 ProductionModuleInstance、
 * 运行时吃 QueueItem，两者 authoring 面还没统一），所以标题必须写"参考计划"而不是"当前队列"——
 * 见 plan-backend-view 的缺口记录。骗人的图比没有图更糟。
 */
import { useMemo, useState } from "react";
import { sendCommand, type CommandResult } from "../api/commands";
import type { ProjectionFrame } from "../contract";
import { createPlan, savePlan } from "../api/plans";
import { ProjectionBoard } from "../charts/ProjectionBoard";
import { useAccumulatedProjection } from "../charts/use-accumulated";
import { packBars } from "../charts/gantt-data";
import { writeGate } from "../shell/mode";
import { Card, Empty, fmtTime } from "../shell/ui";
import {
  draftFromSessionQueues, itemToJson, placementOptions, type DraftItem,
} from "../planning/queue-draft";
import { QueueTable } from "../planning/QueueTable";
import { useFrames } from "../store/frames";

/**
 * 经济卡（2026-08-22 信息分区 v1，用户拍板）：
 * - 只放**经济本体** —— 采矿/采气工人与满采位（汇总，不再逐节点"矿2/2"那种细节）、
 *   配额（唯一写入口）、建造征用计数；
 * - 删掉：本帧差量（调试信息）、逐节点 chips、征用名单明细 —— 必要性不足；
 * - 固定高度与左侧队列卡对齐（h-340），数据多少不撑动布局。
 * 满采位 = 各节点容量合计（显示层派生，G5：不回灌任何决策路径）。
 */
function EconomyCard(props: {
  economy: ReturnType<typeof useFrames.getState>["economy"];
  writable: boolean;
  onQuota: (task: "mineral" | "gas" | "idle", count: number) => void;
}) {
  const e = props.economy;
  const agg = useMemo(() => {
    const m = { workers: 0, cap: 0, nodes: 0 };
    const g = { workers: 0, cap: 0, nodes: 0 };
    for (const n of e?.nodes ?? []) {
      const b = n.kind === "gas" ? g : m;
      b.workers += n.workers;
      b.cap += n.capacity;
      b.nodes += 1;
    }
    return { m, g };
  }, [e]);

  return (
    <Card title="经济" className="h-full min-h-0 overflow-hidden" right={
      <span className="text-note text-faint">
        {e ? `领地 ${e.domain_workers} 人` : ""}
      </span>
    }>
      {e ? (
        <div className="flex h-full min-h-0 flex-col">
          <div className="grid shrink-0 grid-cols-2 gap-2">
            <div>
              <div className="text-note text-faint">采矿 · 满采位 {agg.m.cap}</div>
              <div className="text-metric text-strong">
                {agg.m.workers}<span className="text-note text-faint">/{agg.m.cap}</span>
              </div>
            </div>
            <div>
              <div className="text-note text-faint">
                采气{agg.g.nodes === 0 ? " · 未建气井" : ` · 满采位 ${agg.g.cap}`}
              </div>
              <div className="text-metric text-strong">
                {agg.g.workers}<span className="text-note text-faint">/{agg.g.cap}</span>
              </div>
            </div>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto"><table className="mt-3 w-full text-left">
            <thead className="text-faint">
              <tr><th>任务</th><th>配额</th><th className="text-right">实际</th></tr>
            </thead>
            <tbody>
              {e.tasks.map((t) => (
                <tr key={t.task} className={t.actual < t.target ? "text-[color:var(--warn-fg)]" : ""}>
                  <td>{t.task === "mineral" ? "采矿" : t.task === "gas" ? "采气" : "备用"}</td>
                  <td>
                    {props.writable ? (
                      <input
                        type="number" min={0} max={99}
                        defaultValue={t.quota ?? ""}
                        placeholder="按比例"
                        className="w-14 rounded border border-l2 bg-inset px-1"
                        title="维持 N 个（目标值，幂等）；可达目标受节点容量与人数夹紧"
                        onKeyDown={(ev) => {
                          if (ev.key !== "Enter") return;
                          const v = Number((ev.target as HTMLInputElement).value);
                          if (Number.isFinite(v) && v >= 0) {
                            props.onQuota(t.task as "mineral" | "gas" | "idle", v);
                          }
                        }}
                      />
                    ) : t.quota === null ? (
                      <span className="text-ghost">按比例</span>
                    ) : t.quota}
                  </td>
                  <td className="text-right">
                    {t.actual}<span className="text-note text-ghost">/{t.target}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table></div>

          <div className="mt-auto shrink-0 space-y-1 pt-2 text-note">
            {e.reserved.length > 0 && (
              <div className="text-dim">建造征用 {e.reserved.length} 人（维持器不改派）</div>
            )}
            <div className="text-ghost">配额持久（气矿没建好也挂着）</div>
          </div>
        </div>
      ) : <Empty text="该帧源没有 frame/economy（维持器未接入）" />}
    </Card>
  );
}

/** 命令反馈：409（世界变了）与 400（请求不合法）要让用户看出区别 */
function useCommands() {
  const seq = useFrames((s) => s.seq);
  const sourceKind = useFrames((s) => s.sourceKind);
  const timeline = useFrames((s) => s.timeline);
  const [last, setLast] = useState<CommandResult | null>(null);
  // 门禁收在 shell/mode.ts（可测）：要求 **live 会话源 + 没在回看**。
  // 原来是 `sourceKind === "api"` —— api 是回放源、真正的会话源是 live，
  // 于是 live 下写入控件全被藏起来（主链路走不通），回放下反而放开（命令打到没在看的世界）。
  const gate = writeGate(sourceKind, timeline);
  const run = async (cmd: Parameters<typeof sendCommand>[0]): Promise<void> => {
    const res = await sendCommand(cmd, seq);
    setLast(res);
  };
  return { run, last, writable: gate.writable, gateReason: gate.reason, seq };
}

function CommandBanner(props: { last: CommandResult | null }) {
  // 只读态横幅已退役（十六轮用户拍板：复盘里的「只读」唠叨删去）——
  // 写入控件仍受 writeGate 门控（不可写时直接不渲染），只是不再占一条横幅。
  if (!props.last) return null;
  if (props.last.ok) {
    return (
      <div className="rounded border border-[color:var(--ok-fg)] bg-[color:var(--ok-bg)] px-2 py-1 text-note text-[color:var(--ok-fg)]">
        已接受（seq {props.last.accepted_seq}）· 下一 step 生效
      </div>
    );
  }
  const tone = props.last.reason === "stale" ? "amber" : "red";
  return (
    <div className={`rounded border px-2 py-1 text-note ${tone === "amber"
      ? "border-[color:var(--warn-fg)] bg-[color:var(--warn-bg)] text-[color:var(--warn-fg)]"
      : "border-[color:var(--err-fg)] bg-[color:var(--err-bg)] text-[color:var(--err-fg)]"}`}>
      {props.last.reason === "stale"
        ? "观察已过期（R8）：" + props.last.message
        : props.last.message}
    </div>
  );
}

/** 整局操作序列（二十四轮用户定义的生产队列）：已执行 = 回放累积的开工→完成
 *  配对（拖时间轴不重排）；待执行 = 当前帧队列里还没轮到的项（随回放推进缩短）。
 *  向后大幅拖动时间轴会重置累积（回放数据没有全量历史，只能向前累积）。 */
function WholeOpsList(props: {
  projection: ProjectionFrame;
  production: ReturnType<typeof useFrames.getState>["production"];
  zhOf: (id: string | null) => string;
}) {
  const merged = useAccumulatedProjection(props.projection);
  const ops = useMemo(
    () => packBars(merged).bars.slice().sort((a, b) => a.from - b.from),
    [merged]);
  const pending = useMemo(() => {
    const rows = (props.production?.queues ?? []).flatMap((q) =>
      q.items.map((it) => ({ index: it.index, op: it.op, type: it.stable_id, count: it.count, task: it.task })));
    return rows.sort((a, b) => a.index - b.index);
  }, [props.production]);
  if (ops.length === 0 && pending.length === 0) {
    return <Empty text="还没有操作（回放推进/seek 后累积）" />;
  }
  return (
    <div>
      <table className="w-full text-left">
        <thead className="sticky top-0 z-10 bg-panel text-faint">
          <tr>
            <th className="w-10">#</th><th>操作</th><th className="w-24">开始</th>
            <th className="w-24">完成</th><th className="w-16" />
          </tr>
        </thead>
        <tbody>
          {ops.map((o, i) => (
            <tr key={o.id} className="odd:bg-raised hover:bg-inset">
              <td className="text-ghost">{i}.</td>
              <td className="text-strong">{props.zhOf(o.stableId)}</td>
              <td className="tabular-nums text-dim">{fmtTime(o.from)}</td>
              <td className={o.done ? "tabular-nums text-dim" : "text-[color:var(--warn-fg)]"}
                  title={o.done ? undefined : "开工了还没完成（回放推进后补上）"}>
                {o.done ? fmtTime(o.to) : "进行中"}
              </td>
              <td />
            </tr>
          ))}
          {pending.map((it, i) => (
            <tr key={"p" + it.index} className="odd:bg-raised text-faint hover:bg-inset">
              <td className="text-ghost">{ops.length + i}.</td>
              <td>
                {it.op === "assign_workers"
                  ? `维持${it.task === "gas" ? "采气" : it.task === "mineral" ? "采矿" : "空闲"} ×${it.count}`
                  : props.zhOf(it.type)}{it.count > 1 ? ` ×${it.count}` : ""}
              </td>
              <td className="text-ghost">—</td>
              <td className="text-ghost">待执行</td>
              <td />
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-1 text-note text-ghost">
        上段 = 已执行（回放累积，拖时间轴不重排）；下段灰字 = 此后待执行。
        向后大幅拖时间轴会重置累积（回放没有全量历史，只能向前累积）。
      </div>
    </div>
  );
}

export function ProductionPage() {
  const { production, projection, economy, catalog, map, mode, position, api } = useFrames();
  const cmd = useCommands();

  // ---- 复盘草稿（十八轮：复用规划编辑器） ----
  // 复盘源背后没有会话（writeGate 管着命令面），「编辑」的正确形态是把看到的队列
  // 提取成规划草稿改，改完另存为规划文件 —— 与 live 的命令编辑（cmd.run）互不越界。
  const [draft, setDraft] = useState<DraftItem[] | null>(null);
  const [draftMsg, setDraftMsg] = useState<string | null>(null);
  const [savedPlan, setSavedPlan] = useState<{ id: string; title: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const draftOptions = useMemo(
    () => placementOptions(map, null), [map]);
  const updateDraft = (id: string, patch: Partial<DraftItem>): void =>
    setDraft((l) => (l ?? []).map((it) => (it.id === id ? { ...it, ...patch } : it)));
  const saveAsPlan = async (): Promise<void> => {
    if (!draft || draft.length === 0) { setDraftMsg("草稿是空的"); return; }
    setSaving(true);
    const title = "复盘提取 " + fmtTime(position);
    try {
      const p = await createPlan({ title_zh: title, spawn: "bl" });
      await savePlan(p.id, {
        title_zh: title, map: map?.map_name ?? "LadderMap", spawn: "bl",
        queue: draft.map(itemToJson),
      });
      setSavedPlan({ id: p.id, title });
      setDraftMsg(null);
    } catch (err) {
      setDraftMsg("另存失败：" + (err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const zhOf = useMemo(() => {
    const m = new Map<string, string>();
    for (const e of catalog?.entries ?? []) m.set(e.stable_id, e.display_name_zh);
    return (id: string | null) => (id ? m.get(id) ?? id : "—");
  }, [catalog]);

  return (
    <div className="flex h-full flex-col gap-2 overflow-hidden">
      <CommandBanner last={cmd.last} />
      <div className="grid min-h-0 grid-cols-1 gap-2 xl:grid-cols-3 xl:h-[30%]">
        <Card
          title="生产队列"
          className="flex h-full min-h-0 flex-col overflow-hidden xl:col-span-2"
          right={
            <div className="flex items-center gap-2">
              <span className="text-note text-faint">
                队首门控：队首不可行则整队冻结
              </span>
              {mode === "replay" && api.ok && draft === null
                && production && production.queues.length > 0 && (
                <button className="btn btn-ghost"
                        title="复盘源没有会话可下命令 —— 把看到的队列提取成规划草稿编辑，另存为规划文件"
                        onClick={() => {
                          setDraft(draftFromSessionQueues(production.queues));
                          setSavedPlan(null);
                          setDraftMsg(null);
                        }}>转为规划草稿</button>
              )}
              {draft !== null && (
                <button className="btn btn-ghost"
                        onClick={() => setDraft(null)}>退出编辑</button>
              )}
            </div>
          }
        >
          <div className="min-h-0 flex-1 overflow-y-auto pr-1">
          {draft !== null ? (
            <div>
              <QueueTable items={draft} update={updateDraft} setItems={(fn) => setDraft((l) => fn(l ?? []))} options={draftOptions} />
              <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-l1 pt-2">
                <button className="btn btn-ok" disabled={saving || draft.length === 0}
                        title="草稿存成新的规划文件（在规划模式里继续试算/装配）"
                        onClick={() => void saveAsPlan()}>
                  {saving ? "保存中…" : "另存为规划"}
                </button>
                <button className="btn btn-ghost" onClick={() => setDraft(null)}>放弃</button>
                {savedPlan && (
                  <button className="rounded-lg border-[1.5px] border-accent-blue px-2 py-0.5 text-note font-medium text-strong hover:bg-blue-soft"
                          title="切到规划模式打开这份新规划"
                          onClick={() => {
                            window.location.hash = "/plan-production?plan=" + savedPlan.id;
                            void useFrames.getState().setMode("offline");
                          }}>✎ {savedPlan.title}</button>
                )}
                {draftMsg && <span className="text-note text-[color:var(--warn-fg)]">{draftMsg}</span>}
                <span className="ml-auto text-note text-ghost">
                  复盘只读 —— 编辑发生在草稿上，另存为规划文件
                </span>
              </div>
            </div>
          ) : mode === "replay" && projection ? (
            /* 二十四轮（用户定义）：回放的「生产队列」= 整局操作序列（累积的
               开工→完成 + 此后待执行），拖时间轴不再重排 —— 只移动已执行/待执行
               的分界。时间切片的「此刻剩余队列」仍是下方待执行段与草稿提取的来源 */
            <WholeOpsList projection={projection} production={production} zhOf={zhOf} />
          ) : production && production.queues.length > 0 ? (
            production.queues.map((q) => (
              <div key={q.name} className="mb-3 last:mb-0">
                <div className="mb-1 flex flex-wrap items-baseline gap-2">
                  <span className="font-medium">{q.name}</span>
                  <span
                    className={
                      "rounded px-1.5 text-note " +
                      (q.head_status === "阻塞"
                        ? "bg-[color:var(--warn-bg)] text-[color:var(--warn-fg)]"
                        : q.head_status === "空"
                          ? "bg-raised text-dim"
                          : "bg-[color:var(--ok-bg)] text-[color:var(--ok-fg)]")
                    }
                  >
                    队首 {q.head_status}
                  </span>
                  {q.blocked && (
                    <span className={q.blocked.warned ? "text-[color:var(--err-fg)]" : "text-[color:var(--warn-fg)]"}>
                      已阻塞 {q.blocked.waited.toFixed(0)}s
                      {q.blocked.warned && "（超阈值，后端已升级告警）"}
                      · {q.blocked.reason}
                    </span>
                  )}
                </div>
                {q.items.length === 0 ? (
                  <Empty text="队列为空" />
                ) : (
                  <table className="w-full text-left">
                    <thead className="sticky top-0 z-10 bg-panel text-faint">
                      <tr>
                        <th className="w-8">#</th><th className="w-28">op</th>
                        <th>目标</th><th className="w-12">数量</th>
                        <th className="w-40">放置</th><th className="w-24">状态</th>
                        <th className="w-16" />
                      </tr>
                    </thead>
                    <tbody>
                      {q.items.map((it) => (
                        <tr key={it.index}
                            className={it.status === "队首阻塞" ? "text-[color:var(--warn-fg)]" : ""}>
                          <td>{it.index}</td>
                          <td>{it.op}</td>
                          <td>
                            {it.op === "assign_workers"
                              ? <>维持 {it.task} <span className="text-note text-faint">（目标值语义）</span></>
                              : zhOf(it.stable_id)}
                          </td>
                          <td>{it.count}</td>
                          <td className="text-dim">
                            {it.placement
                              ? it.placement.kind === "exact"
                                ? "槽位 " + it.placement.mark
                                : "区域 " + it.placement.region +
                                  (it.placement.index === null ? "（自动找位）" : " #" + it.placement.index)
                              : "—"}
                          </td>
                          <td>{it.status}</td>
                          <td className="text-right">
                            {cmd.writable && (
                              <>
                                {it.index > 0 && (
                                  <button
                                    className="btn btn-ghost mr-1 px-1"
                                    title="上移一位"
                                    onClick={() => {
                                      const n = q.items.length;
                                      const order = [...Array(n).keys()];
                                      const i = it.index;
                                      [order[i - 1], order[i]] = [order[i]!, order[i - 1]!];
                                      void cmd.run({ kind: "queue", op: "reorder",
                                        body: { name: q.name, order } });
                                    }}
                                  >↑</button>
                                )}
                                <button
                                  className="btn btn-danger px-1"
                                  title="从队列移除"
                                  onClick={() => void cmd.run({ kind: "queue", op: "remove",
                                    body: { name: q.name, index: it.index } })}
                                >×</button>
                              </>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            ))
          ) : <Empty />}

          {production && production.dropped.length > 0 && (
            <div className="mt-2 border-t border-l1 pt-2">
              <div className="text-[color:var(--err-fg)]">掉项审计（R7：降级但不静默）</div>
              <ul className="mt-1 space-y-0.5 text-dim">
                {production.dropped.map((d, i) => (
                  <li key={i}>
                    {d.op} {zhOf(d.stable_id)} —— {d.reason}
                    <span className="ml-1 text-note text-ghost">
                      {d.at === null ? "（后端未记时间）" : fmtTime(d.at)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          </div>
        </Card>

        <EconomyCard economy={economy} writable={cmd.writable}
                     onQuota={(task, count) =>
                       void cmd.run({ kind: "workers", body: { task, count } })} />
      </div>

      <Card
        className="flex min-h-0 flex-1 flex-col overflow-hidden"
        title={projection?.source.kind === "live_queue"
          ? "投影 · 当前队列 " + projection.source.queue_name
          : "投影 · 参考计划"}
        right={
          <span className="text-note">
            {projection?.source.kind === "draft" ? (
              <span className="text-[color:var(--warn-fg)]">
                队列为空，显示的是参考计划 {projection.source.plan_id}
              </span>
            ) : (
              <span className="text-[color:var(--ok-fg)]">基于 seq {projection?.based_on_seq}</span>
            )}
          </span>
        }
      >
        {/* F15：曲线与泳道合进一张卡 —— 共享同一条时间轴（滚轮缩放/中心跟随/点击检查） */}
        {projection
          ? <ProjectionBoard frame={projection} catalog={catalog} zhOf={zhOf} />
          : <Empty />}
        {projection && projection.skipped.length > 0 && (
          <div className="mt-1 text-note text-[color:var(--warn-fg)]">
            有 {projection.skipped.length} 项没进投影：
            {projection.skipped.map((s) => s.op + "（" + s.reason + "）").join("；")}
            <span className="ml-1 text-ghost">—— 曲线比真实队列少算了这部分</span>
          </div>
        )}
      </Card>

    </div>
  );
}
