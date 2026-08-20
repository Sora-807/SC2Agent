/**
 * 调试页（F5）—— 命令流水 + 求值诊断 + 掉项审计 + 原始帧检查器。
 *
 * 为什么这页值得存在：仓库的 commit 史里有一整类 bug 是"op 发了但没生效"
 * （`unit_command_uses_self_do` 静默忽略、挂件订单出现但实体不出现、建造确认超时、换位重试），
 * 为此诞生了 4 个一次性探针脚本。这页把那些信息变成常驻视图。
 *
 * 这里是**唯一允许显示 burnysc2 原生名**的地方（`order.ability_raw` 与 catalog 的
 * `burnysc2_name`）—— 红线 C1 管的是类型身份，不是诊断字段。
 */
import { useMemo, useState } from "react";
import { Card, Empty, fmtTime, sevClass } from "../shell/ui";
import { useFrames } from "../store/frames";
import type { Topic } from "../contract";

const ORIGINS = ["flow", "production", "worker", "user", "agent"] as const;

export function DebugPage() {
  const { ops, production, flow, world, session, alerts, catalog, economy, range, position } =
    useFrames();
  const [originFilter, setOriginFilter] = useState<string>("");
  const [actionFilter, setActionFilter] = useState<string>("");
  const [inspect, setInspect] = useState<Topic>("frame/world");

  const zhOf = useMemo(() => {
    const m = new Map<string, string>();
    for (const e of catalog?.entries ?? []) m.set(e.stable_id, e.display_name_zh);
    return (id: string | null) => (id ? m.get(id) ?? id : "—");
  }, [catalog]);

  const rows = useMemo(() => {
    let list = ops?.ops ?? [];
    if (originFilter) list = list.filter((o) => o.origin === originFilter);
    if (actionFilter) list = list.filter((o) => o.action.includes(actionFilter));
    return [...list].reverse();            // 最新在上
  }, [ops, originFilter, actionFilter]);

  const actions = useMemo(
    () => [...new Set((ops?.ops ?? []).map((o) => o.action))].sort(),
    [ops],
  );

  const strategy = flow?.strategies.at(0) ?? null;

  return (
    <div className="space-y-3">
      <Card title="会话" right={<span className="text-[11px] text-neutral-500">
        游标 {fmtTime(position)} / 范围 {fmtTime(range.from)}–{fmtTime(range.to)}
      </span>}>
        <div className="flex flex-wrap gap-4 text-neutral-300">
          <span>帧源 <b>{session?.frame_source ?? "—"}</b></span>
          <span>状态 <b>{session?.state ?? "—"}</b></span>
          <span>单位 {world?.units.length ?? 0}</span>
          <span>命令流水 {ops?.ops.length ?? 0} 条</span>
          <span>警报 {alerts?.alerts.length ?? 0}</span>
          <span>经济差量 {economy?.emitted_count ?? "—"}</span>
        </div>
      </Card>

      <Card
        title="命令流水"
        right={
          <div className="flex gap-2 text-[11px]">
            <select className="rounded border border-neutral-700 bg-neutral-900 px-1"
                    value={originFilter} onChange={(e) => setOriginFilter(e.target.value)}>
              <option value="">全部来源</option>
              {ORIGINS.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
            <select className="rounded border border-neutral-700 bg-neutral-900 px-1"
                    value={actionFilter} onChange={(e) => setActionFilter(e.target.value)}>
              <option value="">全部动作</option>
              {actions.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
          </div>
        }
      >
        {rows.length === 0 ? (
          <Empty text="本帧窗口没有命令（环形缓冲只保留最近一段）" />
        ) : (
          <div className="max-h-72 overflow-auto">
            <table className="w-full text-left">
              <thead className="sticky top-0 bg-neutral-900 text-neutral-500">
                <tr>
                  <th className="w-12">op</th><th className="w-14">seq</th><th className="w-14">时间</th>
                  <th className="w-20">来源</th><th className="w-32">动作</th>
                  <th className="w-24">单位</th><th>参数</th>
                  <th className="w-20">应用</th><th className="w-24">落地</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((o) => (
                  <tr key={o.op_id + "-" + o.seq} className="border-t border-neutral-900">
                    <td>{o.op_id}</td>
                    <td>{o.seq}</td>
                    <td>{fmtTime(o.at)}</td>
                    <td>
                      <span className="rounded bg-neutral-800 px-1 text-[10px]">{o.origin}</span>
                    </td>
                    <td>{o.action}</td>
                    <td className="text-neutral-400" title={o.unit_tags.join(", ")}>
                      {o.unit_tags.length === 1 ? o.unit_tags[0] : o.unit_tags.length + " 个"}
                    </td>
                    <td className="truncate text-neutral-400" title={JSON.stringify(o.params)}>
                      {JSON.stringify(o.params)}
                    </td>
                    <td>
                      {o.apply === null ? (
                        <span className="text-neutral-600" title="需后端 B9（D6 ApplyResult）">未知</span>
                      ) : o.apply.failed ? (
                        <span className="text-red-400">失败</span>
                      ) : (
                        <span className="text-emerald-400">已接受</span>
                      )}
                    </td>
                    <td>
                      {o.landing === null ? (
                        <span className="text-neutral-600" title="需后端 B9（D7 GameEvent）">未知</span>
                      ) : (
                        <span>{o.landing.kind}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="mt-1 text-[10px] text-neutral-600">
          `origin` 由后端 `RecordingPort` 打标（不给 `Operation` 加字段）。
          "应用/落地"在 B9 之前恒为"未知" —— 显示未知而不是留空，才不会让人以为一切正常。
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <Card title="求值诊断">
          {strategy && strategy.eval_diagnostics.length > 0 ? (
            <table className="w-full text-left">
              <thead className="text-neutral-500">
                <tr><th>step</th><th>类型</th><th>细节</th><th className="w-12">次数</th></tr>
              </thead>
              <tbody>
                {strategy.eval_diagnostics.map((d, i) => (
                  <tr key={i}>
                    <td>{d.step_id}</td><td>{d.kind}</td>
                    <td className="text-neutral-400">{d.detail}</td><td>{d.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty text="没有诊断记录（条件都正常求出了值）" />
          )}
          <div className="mt-1 text-[10px] text-neutral-600">
            引擎在这里记"条件其实没求出来"（比较遇 None 降级为 False 等）——
            不看它，一条永远为假的分支会静默地把策略卡住。
          </div>
        </Card>

        <Card title="掉项审计">
          {production && production.dropped.length > 0 ? (
            <ul className="space-y-1">
              {production.dropped.map((d, i) => (
                <li key={i}>
                  <span className="text-red-400">{d.op}</span> {zhOf(d.stable_id)}
                  <div className="text-neutral-400">{d.reason}</div>
                </li>
              ))}
            </ul>
          ) : <Empty text="没有被丢弃的队列项" />}
          <div className="mt-1 text-[10px] text-neutral-600">
            R7：配置错误降级但**不静默** —— 丢弃必须留下原因。
          </div>
        </Card>
      </div>

      <Card title="警报">
        {alerts && alerts.alerts.length > 0 ? (
          <ul className="space-y-1">
            {alerts.alerts.map((a) => (
              <li key={a.id + a.at}>
                <span className={sevClass(a.severity)}>[{a.severity}]</span>{" "}
                <span className="text-neutral-500">{fmtTime(a.at)}</span> {a.text_zh}
                <span className="ml-1 text-[10px] text-neutral-600">
                  {a.kind} · 来源 {a.source}
                  {a.eta !== null ? " · eta " + a.eta + "s" : ""}
                </span>
              </li>
            ))}
          </ul>
        ) : <Empty text="无警报" />}
      </Card>

      <Card
        title="原始帧检查器"
        right={
          <select className="rounded border border-neutral-700 bg-neutral-900 px-1 text-[11px]"
                  value={inspect} onChange={(e) => setInspect(e.target.value as Topic)}>
            {(["frame/world", "frame/flow", "frame/production", "frame/economy",
               "frame/projection", "frame/ops", "frame/alerts", "frame/session"] as Topic[])
              .map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        }
      >
        <RawInspector topic={inspect} />
        <div className="mt-1 text-[10px] text-neutral-600">
          这里是唯一允许出现 burnysc2 原生名的地方（`ability_raw` 是诊断字段，不是类型身份）。
        </div>
      </Card>
    </div>
  );
}

function RawInspector(props: { topic: Topic }) {
  const s = useFrames();
  const payload =
    props.topic === "frame/world" ? s.world
    : props.topic === "frame/flow" ? s.flow
    : props.topic === "frame/production" ? s.production
    : props.topic === "frame/economy" ? s.economy
    : props.topic === "frame/projection" ? s.projection
    : props.topic === "frame/ops" ? s.ops
    : props.topic === "frame/alerts" ? s.alerts
    : s.session;
  if (!payload) return <Empty text="该 topic 本帧没有数据" />;
  const text = JSON.stringify(payload, null, 1);
  return (
    <pre className="max-h-72 overflow-auto rounded bg-neutral-950 p-2 text-[11px] text-neutral-300">
      {text.length > 40000 ? text.slice(0, 40000) + "\n…（已截断）" : text}
    </pre>
  );
}
