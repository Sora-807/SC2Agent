/**
 * 生产页（F3）—— 队列 + 投影 Gantt + 目录 + 经济维持器。
 *
 * 一条诚实性纪律：投影的 `source` 现在只能是 `draft`（planner 吃 ProductionModuleInstance、
 * 运行时吃 QueueItem，两者 authoring 面还没统一），所以标题必须写"参考计划"而不是"当前队列"——
 * 见 plan-backend-view 的缺口记录。骗人的图比没有图更糟。
 */
import { useMemo, useState } from "react";
import { sendCommand, type CommandResult } from "../api/commands";
import { ProjectionChart } from "../charts/ProjectionChart";
import { Card, Empty, fmtTime } from "../shell/ui";
import { useFrames } from "../store/frames";
import type { CatalogStatic, ProjectionFrame, WorldFrame } from "../contract";

/** 命令反馈：409（世界变了）与 400（请求不合法）要让用户看出区别 */
function useCommands() {
  const seq = useFrames((s) => s.seq);
  const sourceKind = useFrames((s) => s.sourceKind);
  const [last, setLast] = useState<CommandResult | null>(null);
  const writable = sourceKind === "api";
  const run = async (cmd: Parameters<typeof sendCommand>[0]): Promise<void> => {
    const res = await sendCommand(cmd, seq);
    setLast(res);
  };
  return { run, last, writable, seq };
}

function CommandBanner(props: { last: CommandResult | null; writable: boolean }) {
  if (!props.writable) {
    return (
      <div className="rounded border border-neutral-800 bg-neutral-900/60 px-2 py-1 text-[11px] text-neutral-500">
        只读：当前帧源是本地夹具。把帧源切到「后端 API」并启动沙盒会话后可以下命令。
      </div>
    );
  }
  if (!props.last) return null;
  if (props.last.ok) {
    return (
      <div className="rounded border border-emerald-800 bg-emerald-950/40 px-2 py-1 text-[11px] text-emerald-300">
        已接受（seq {props.last.accepted_seq}）· 下一 step 生效
      </div>
    );
  }
  const tone = props.last.reason === "stale" ? "amber" : "red";
  return (
    <div className={`rounded border px-2 py-1 text-[11px] ${tone === "amber"
      ? "border-amber-800 bg-amber-950/40 text-amber-300"
      : "border-red-800 bg-red-950/40 text-red-300"}`}>
      {props.last.reason === "stale"
        ? "观察已过期（R8）：" + props.last.message
        : props.last.message}
    </div>
  );
}

export function ProductionPage() {
  const { production, projection, economy, catalog, schema, world } = useFrames();
  const [tab, setTab] = useState<"queue" | "catalog">("queue");
  const cmd = useCommands();

  const zhOf = useMemo(() => {
    const m = new Map<string, string>();
    for (const e of catalog?.entries ?? []) m.set(e.stable_id, e.display_name_zh);
    return (id: string | null) => (id ? m.get(id) ?? id : "—");
  }, [catalog]);

  return (
    <div className="space-y-3">
      <CommandBanner last={cmd.last} writable={cmd.writable} />
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
        <Card
          title="生产队列"
          className="xl:col-span-2"
          right={
            <span className="text-[11px] text-neutral-500">
              队首门控：队首不可行则整队冻结
            </span>
          }
        >
          {production && production.queues.length > 0 ? (
            production.queues.map((q) => (
              <div key={q.name} className="mb-3 last:mb-0">
                <div className="mb-1 flex flex-wrap items-baseline gap-2">
                  <span className="font-medium">{q.name}</span>
                  <span
                    className={
                      "rounded px-1.5 text-[11px] " +
                      (q.head_status === "阻塞"
                        ? "bg-amber-900/60 text-amber-300"
                        : q.head_status === "空"
                          ? "bg-neutral-800 text-neutral-400"
                          : "bg-emerald-900/50 text-emerald-300")
                    }
                  >
                    队首 {q.head_status}
                  </span>
                  {q.blocked && (
                    <span className={q.blocked.warned ? "text-red-400" : "text-amber-400"}>
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
                    <thead className="text-neutral-500">
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
                            className={it.status === "队首阻塞" ? "text-amber-400" : ""}>
                          <td>{it.index}</td>
                          <td>{it.op}</td>
                          <td>
                            {it.op === "assign_workers"
                              ? <>维持 {it.task} <span className="text-[10px] text-neutral-500">（目标值语义）</span></>
                              : zhOf(it.stable_id)}
                          </td>
                          <td>{it.count}</td>
                          <td className="text-neutral-400">
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
                                    className="mr-1 rounded border border-neutral-700 px-1 text-[10px]"
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
                                  className="rounded border border-neutral-700 px-1 text-[10px] text-red-400"
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
            <div className="mt-2 border-t border-neutral-800 pt-2">
              <div className="text-red-400">掉项审计（R7：降级但不静默）</div>
              <ul className="mt-1 space-y-0.5 text-neutral-400">
                {production.dropped.map((d, i) => (
                  <li key={i}>
                    {d.op} {zhOf(d.stable_id)} —— {d.reason}
                    <span className="ml-1 text-[10px] text-neutral-600">
                      {d.at === null ? "（后端未记时间）" : fmtTime(d.at)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Card>

        <Card title="经济维持器" right={
          <span className="text-[11px] text-neutral-500">
            {economy ? `领地 ${economy.domain_workers} 人` : ""}
          </span>
        }>
          {economy ? (
            <>
              <table className="w-full text-left">
                <thead className="text-neutral-500">
                  <tr><th>任务</th><th>配额</th><th>可达</th><th>实际</th></tr>
                </thead>
                <tbody>
                  {economy.tasks.map((t) => (
                    <tr key={t.task} className={t.actual < t.target ? "text-amber-400" : ""}>
                      <td>{t.task === "mineral" ? "采矿" : t.task === "gas" ? "采气" : "备用"}</td>
                      <td>
                        {cmd.writable ? (
                          <input
                            type="number" min={0} max={99}
                            defaultValue={t.quota ?? ""}
                            placeholder="比例"
                            className="w-14 rounded border border-neutral-700 bg-neutral-950 px-1"
                            title="维持 N 个（目标值，幂等）"
                            onKeyDown={(e) => {
                              if (e.key !== "Enter") return;
                              const v = Number((e.target as HTMLInputElement).value);
                              if (Number.isFinite(v) && v >= 0) {
                                void cmd.run({ kind: "workers", body: { task: t.task, count: v } });
                              }
                            }}
                          />
                        ) : t.quota === null ? (
                          <span className="text-neutral-600">按比例</span>
                        ) : t.quota}
                      </td>
                      <td>{t.target}</td>
                      <td>{t.actual}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="mt-1 text-[10px] text-neutral-600">
                配额是<b>持久</b>的（气矿没建好也挂着）；可达目标受节点容量与人数夹紧
              </div>
              <div className="mt-2 border-t border-neutral-800 pt-2">
                <div className="text-neutral-400">
                  本帧差量 {economy.emitted_count} 条
                  {economy.emitted_count === 0 && (
                    <span className="ml-1 text-emerald-400">（稳定态零命令）</span>
                  )}
                </div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {economy.nodes.map((n) => (
                    <span
                      key={n.tag}
                      title={`${n.kind} #${n.tag} ${n.workers}/${n.capacity}`}
                      className={
                        "rounded px-1 text-[10px] " +
                        (n.saturated
                          ? "bg-emerald-900/60 text-emerald-300"
                          : n.workers > 0
                            ? "bg-sky-900/50 text-sky-300"
                            : "bg-neutral-800 text-neutral-500")
                      }
                    >
                      {n.kind === "gas" ? "气" : "矿"}{n.workers}/{n.capacity}
                    </span>
                  ))}
                </div>
                {economy.reserved.length > 0 && (
                  <div className="mt-2 text-neutral-400">
                    建造征用 {economy.reserved.length} 人：
                    {economy.reserved.map((r) => `${r.tag}(${r.owner})`).join("、")}
                    <div className="text-[10px] text-neutral-600">
                      征用期间维持器不会改派它们（否则会毁掉在途建造）
                    </div>
                  </div>
                )}
              </div>
            </>
          ) : <Empty text="该帧源没有 frame/economy（维持器未接入）" />}
        </Card>
      </div>

      <Card
        title="投影 · 参考计划"
        right={
          <span className="text-[11px] text-amber-500">
            {projection?.source.kind === "draft"
              ? "注意：这是参考计划的投影，不是当前队列 —— planner 与运行时的 authoring 面尚未统一"
              : "当前队列"}
          </span>
        }
      >
        {projection ? <ProjectionChart frame={projection} /> : <Empty />}
      </Card>

      <Card title="投影泳道（Gantt）">
        {projection ? <Gantt frame={projection} zhOf={zhOf} /> : <Empty />}
      </Card>

      <div className="flex gap-2 text-xs">
        <button
          className={"rounded border px-2 py-1 " + (tab === "queue" ? "border-neutral-500" : "border-neutral-800 text-neutral-500")}
          onClick={() => setTab("queue")}
        >在途与产线</button>
        <button
          className={"rounded border px-2 py-1 " + (tab === "catalog" ? "border-neutral-500" : "border-neutral-800 text-neutral-500")}
          onClick={() => setTab("catalog")}
        >目录</button>
      </div>

      {tab === "queue" ? (
        <Card title="在途建造">
          {production && production.in_flight.length > 0 ? (
            <table className="w-full text-left">
              <thead className="text-neutral-500">
                <tr><th>队列</th><th>目标</th><th>类型</th><th>建造者</th><th>等待</th><th>重试</th><th>已试槽位</th></tr>
              </thead>
              <tbody>
                {production.in_flight.map((f, i) => (
                  <tr key={i}>
                    <td>{f.queue}</td><td>{zhOf(f.stable_id)}</td><td>{f.kind}</td>
                    <td>{f.builder_tag ?? <span className="text-amber-400">待重试</span>}</td>
                    <td>{f.frames_waited} 帧</td>
                    <td className={f.retries > 0 ? "text-amber-400" : ""}>{f.retries}</td>
                    <td className="text-neutral-400">{f.attempted_slots.join("、") || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <Empty text="没有在途建造" />}
        </Card>
      ) : (
        <CatalogPicker catalog={catalog} zhOf={zhOf} world={world}
                       unsupported={schema?.queue.unsupported_ops ?? {}}
                       writable={cmd.writable}
                       onAdd={(item) => void cmd.run({
                         kind: "queue", op: "append",
                         body: { name: "main", items: [item] },
                       })} />
      )}
    </div>
  );
}

/** 投影事件 → 泳道：每个 started 到对应 completed 画一条 */
function Gantt(props: { frame: ProjectionFrame; zhOf: (id: string | null) => string }) {
  const { frame } = props;
  const t0 = frame.based_on_game_time;
  const span = Math.max(1, frame.horizon);
  const bars: { id: string; label: string; from: number; to: number; done: boolean }[] = [];
  const open = new Map<string, number>();
  for (const e of frame.events) {
    const key = e.stable_id ?? "?";
    if (e.kind === "started") open.set(key + ":" + e.t, e.t);
    if (e.kind === "completed") {
      // 找同类型最早未闭合的 started
      const entry = [...open.entries()].filter(([k]) => k.startsWith(key + ":")).sort()[0];
      const from = entry ? entry[1] : e.t;
      if (entry) open.delete(entry[0]);
      bars.push({ id: key + e.t, label: props.zhOf(e.stable_id), from, to: e.t, done: true });
    }
  }
  for (const [k, from] of open) {
    bars.push({ id: k, label: props.zhOf(k.split(":")[0] ?? null), from, to: t0 + span, done: false });
  }
  const stalls = frame.events.filter((e) => e.kind === "stalled");

  if (bars.length === 0 && stalls.length === 0) return <Empty text="该投影没有开工/完成事件" />;
  return (
    <div className="space-y-1">
      {bars.slice(0, 24).map((b) => (
        <div key={b.id} className="flex items-center gap-2 text-[11px]">
          <span className="w-24 shrink-0 truncate text-neutral-400">{b.label}</span>
          <div className="relative h-3 flex-1 rounded bg-neutral-900">
            <div
              className={"absolute inset-y-0 rounded " + (b.done ? "bg-emerald-700/70" : "bg-amber-700/60")}
              style={{
                left: ((b.from - t0) / span) * 100 + "%",
                width: Math.max(0.6, ((b.to - b.from) / span) * 100) + "%",
              }}
            />
            {stalls.map((s, i) => (
              <div key={i} className="absolute inset-y-0 w-0.5 bg-red-500/70"
                   style={{ left: ((s.t - t0) / span) * 100 + "%" }} />
            ))}
          </div>
          <span className="w-24 shrink-0 text-right text-neutral-600">
            {fmtTime(b.from)} → {b.done ? fmtTime(b.to) : "未完"}
          </span>
        </div>
      ))}
      {stalls.length > 0 && (
        <div className="pt-1 text-[11px] text-red-400">
          卡点：{stalls.map((s) => `${fmtTime(s.t)} ${props.zhOf(s.stable_id)} ${s.reason ?? ""}`).join("；")}
        </div>
      )}
    </div>
  );
}

/** 目录选择器：按前置是否满足置灰（前置来自 catalog，不硬编码） */
function CatalogPicker(props: {
  catalog: CatalogStatic | null;
  zhOf: (id: string | null) => string;
  world: WorldFrame | null;
  unsupported: Record<string, string>;
  writable: boolean;
  onAdd: (item: Record<string, unknown>) => void;
}) {
  const { catalog, world } = props;
  const have = useMemo(() => {
    const s = new Set<string>();
    for (const u of world?.units ?? []) {
      if (u.owner === "self" && u.build_progress >= 1) s.add(u.stable_id);
    }
    return s;
  }, [world]);

  if (!catalog) return <Empty />;
  const groups: [string, string][] = [
    ["building", "建筑"], ["combat", "战斗单位"], ["worker", "工兵"], ["upgrade", "升级"],
  ];
  /** 建筑要 placement：没有 placement 的 build 在编译期就非法（P0 §D2 的边界）。
   *  这里默认丢到 home 区域自动找位；精确槽位留给 F9 的地图规划。 */
  const itemFor = (e: CatalogStatic["entries"][number]): Record<string, unknown> | null => {
    if (e.role === "building") {
      return { op: "build", type: e.stable_id, count: 1,
               placement: { kind: "in_region", region: "home" } };
    }
    if (e.role === "worker" || e.role === "combat") {
      return { op: "train", type: e.stable_id, count: 1 };
    }
    return null;   // upgrade 走 research，而 research 后端还不支持（不给假按钮）
  };
  return (
    <Card title={props.writable ? "目录（点击加入 main 队列）" : "目录（只读：切到后端 API 才能下命令）"}>
      {groups.map(([role, label]) => {
        const rows = catalog.entries.filter((e) => e.role === role);
        if (rows.length === 0) return null;
        return (
          <div key={role} className="mb-2">
            <div className="text-neutral-500">{label}</div>
            <div className="mt-1 flex flex-wrap gap-1">
              {rows.map((e) => {
                const missing = e.prerequisites.filter((p) => !have.has(p));
                const blocked = missing.length > 0;
                const item = itemFor(e);
                const unsupported = item === null
                  ? props.unsupported["research"] ?? "该角色没有对应的队列 op"
                  : null;
                const disabled = blocked || !props.writable || item === null;
                const why = blocked
                  ? "缺前置：" + missing.map((m) => props.zhOf(m)).join("、")
                  : unsupported
                    ? "不可用：" + unsupported
                    : e.produced_by
                      ? "产出建筑 " + props.zhOf(e.produced_by)
                      : "";
                return (
                  <button
                    key={e.stable_id}
                    disabled={disabled}
                    onClick={() => item && props.onAdd(item)}
                    title={why + `｜矿 ${e.cost.minerals} 气 ${e.cost.vespene} 供给 ${e.cost.supply} 时间 ${e.build_time}s`}
                    className={
                      "rounded border px-1.5 py-0.5 text-[11px] " +
                      (disabled
                        ? "border-neutral-800 text-neutral-600"
                        : "border-neutral-700 text-neutral-300 hover:border-neutral-500 hover:text-neutral-100")
                    }
                  >
                    {e.display_name_zh}
                    <span className="ml-1 text-[10px] text-neutral-500">
                      {e.cost.minerals}/{e.cost.vespene}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        );
      })}
      <div className="mt-1 text-[10px] text-neutral-600">
        置灰 = 前置不满足 / 只读 / 后端不支持（前置与原因都来自后端，前端不硬编码）。
        建筑默认丢到 home 区域自动找位 —— 没有 placement 的 build 在编译期就非法。
        不支持的队列 op：{Object.entries(props.unsupported).map(([k, v]) => `${k}（${v}）`).join("；") || "无"}
      </div>
    </Card>
  );
}