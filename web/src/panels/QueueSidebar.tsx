/**
 * 「文件与试算」面板（2026-08-22 十九轮起住在三区布局的右上卡里）——
 * 规划清单/保存/复制/删除、从模板落地（I12-B3）、放置引用、成本、视野与试算、前瞻警报。
 * 状态在 planning/queue-store（导航不丢）。原来三张独立 Card 改为分节
 * （卡中卡的双层描边去掉，分节用细分隔线）。
 */
import { useEffect, useMemo, useState } from "react";
import { listModules, type ModuleMeta } from "../api/plans";
import { useQueueStore } from "../planning/queue-store";
import { draftCost } from "../planning/queue-draft";
import { useFrames } from "../store/frames";
import { AlertsList } from "./AlertsList";
import { Empty } from "../shell/ui";

function Section(props: { title: string; children: React.ReactNode; first?: boolean }) {
  return (
    <section className={props.first ? "" : "border-t border-l1 pt-2"}>
      <div className="mb-1.5 text-label font-semibold text-strong">{props.title}</div>
      {props.children}
    </section>
  );
}

export function QueueSidebar() {
  const st = useQueueStore();
  const catalog = useFrames((s) => s.catalog);
  // 参考模块（I12-B3）：模板清单只在面板出现时拉一次；后端没起/没有 = 空表不报错
  const [modules, setModules] = useState<ModuleMeta[]>([]);
  const [moduleId, setModuleId] = useState("");
  useEffect(() => {
    void listModules().then((rows) => {
      setModules(rows);
      setModuleId(rows[0]?.id ?? "");
    }).catch(() => setModules([]));
  }, []);

  const cost = useMemo(() => draftCost(st.items, catalog), [st.items, catalog]);
  const spaceRefPlans = useMemo(
    () => (st.refPlans ?? []).filter(
      (p) => p.map_name === (st.plan?.map ?? "LadderMap")
            && p.spawn === (st.plan?.spawn ?? "bl")),
    [st.refPlans, st.plan?.map, st.plan?.spawn]);

  if (!st.plans) return <div className="text-note text-faint">加载规划列表…</div>;

  return (
    <div className="space-y-3">
      <Section first title="规划文件">
        <div className="mb-1 text-note text-faint">
          地图 {st.plan?.map ?? "—"} · 出生点 {st.plan?.spawn ?? "—"}
        </div>
        <select value={st.plan?.id ?? ""}
                onChange={(e) => { void st.open(e.target.value); }}
                className="w-full rounded border border-l2 bg-inset px-1 text-label">
          {st.plans.map((p) => (
            <option key={p.id} value={p.id}>
              {p.title_zh}（{p.id}{p.locked ? "，锁定" : ""}，{p.items} 项）
            </option>
          ))}
        </select>
        {st.plan?.locked ? (
          <div className="mt-1.5 text-note text-[color:var(--warn-fg)]">
            默认规划锁定（参考基准）：要改先复制一份
          </div>
        ) : (
          <button className="btn btn-ok mt-1.5 w-full justify-center"
                  disabled={!st.dirty} title={st.dirty ? undefined : "没有未保存的修改"}
                  onClick={() => void st.save()}>保存{st.dirty ? "（未保存）" : ""}</button>
        )}
        <div className="mt-1.5 flex items-center gap-1.5">
          <input className="min-w-0 flex-1 rounded border border-l2 bg-inset px-2 py-1 text-label"
                 value={st.newName} onChange={(e) => st.patch({ newName: e.target.value })}
                 placeholder="新规划名（中文名）" />
          <button className="btn btn-ghost"
                  disabled={!st.newName.trim()}
                  title="复制当前规划（默认路径：默认规划 → 你的开局）"
                  onClick={() => void st.create(st.plan?.id ?? null)}>复制新建</button>
        </div>
        <button className="btn btn-ghost mt-1.5 w-full justify-center"
                disabled={!st.newName.trim()} onClick={() => void st.create(null)}>空白新建</button>
        {modules.length > 0 && (
          <div className="mt-1.5 flex items-center gap-1.5">
            <select value={moduleId} onChange={(e) => setModuleId(e.target.value)}
                    title="内置战术库（MODULE_REGISTRY）—— 模块是唯一真相源，落地成可编辑的规划文件"
                    className="min-w-0 flex-1 rounded border border-l2 bg-inset px-1 text-label">
              {modules.map((m) => (
                <option key={m.id} value={m.id}>{m.title_zh || m.id}（{m.items} 项）</option>
              ))}
            </select>
            <button className="btn btn-ghost"
                    title="把所选模板的默认参数导出成新规划（名字取上面的新规划名）"
                    onClick={() => void st.createFromModule(moduleId)}>从模板落地</button>
          </div>
        )}
        {!st.plan?.locked && st.plan && (
          <button className="btn btn-danger mt-1.5 w-full justify-center"
                  onClick={() => void st.del()}>删除这个规划</button>
        )}
      </Section>

      <Section title="放置引用">
        <select value={st.refId ?? ""} onChange={(e) => st.patch({ refId: e.target.value || null })}
                title="placement 选项来自这份地图规划：槽位/点位以「规划id/名」限定引用（槽位名=简写标记：D 补给区/R·F·S 生产区/+挂件区，尺寸语义三族通用），预设固定建造点（蓝方主矿气井1…）全局可用"
                className="w-full rounded border border-l2 bg-inset px-1 text-label">
          {spaceRefPlans.length === 0 && <option value="">（该空间无地图规划）</option>}
          {spaceRefPlans.map((p) => (
            <option key={p.id} value={p.id}>{p.title_zh}（{p.slots} 槽）</option>
          ))}
        </select>
        <div className="mt-1.5 text-note text-ghost">
          build 项的 placement 从这里选；train / research / assign_workers 不需要
        </div>
      </Section>

      <Section title="成本与试算"
               >
        <div className="mb-1.5 text-note text-faint">
          矿 {cost.minerals} · 气 {cost.vespene} · 供给 {cost.supply}
        </div>
        {cost.missing.length > 0 && (
          <div className="mb-1.5 text-note text-[color:var(--warn-fg)]">
            缺目录项：{cost.missing.join(", ")}
          </div>
        )}
        <label className="flex items-center gap-1 text-note text-faint">
          视野
          <input type="number" min={60} max={600} step={60} value={st.horizon}
                 onChange={(e) => st.patch({ horizon: Number(e.target.value) })}
                 className="w-16 rounded border border-l2 bg-inset px-1 text-label" />
          秒
        </label>
        <button className="btn btn-ok mt-1.5 w-full justify-center"
                title="标准开局（基地 + 12 SCV）起步，后端真 planner 逐秒干跑 —— 不需要会话"
                disabled={st.busy || st.items.length === 0}
                onClick={() => void st.simulate()}>
          {st.busy ? "试算中…" : "试算"}
        </button>
        {st.msg && <div className="mt-1 text-label text-[color:var(--warn-fg)]">{st.msg}</div>}
      </Section>

      {/* 前瞻警报：与实时风险面板同一数据模型（后端 AlertView）+ 同一渲染组件。
          打开规划即显示（baseSim = 打开时自动跑的对照干跑）；点过试算后以草稿结果为准 */}
      {(() => {
        const sim = st.sim ?? st.baseSim;
        if (!sim || (sim.alerts.length === 0 && sim.skipped.length === 0)) return null;
        const tag = st.sim ? "" : "（当前规划）";
        return (
          <Section title={"前瞻警报（" + sim.alerts.length + "）" + tag}>
            <AlertsList alerts={sim.alerts} empty={<Empty text="没有卡点：规划全程可行" />} />
            {sim.skipped.map((s, i) => (
              <div key={"s" + i} className="text-label text-faint">
                {s.op} 没进投影：{s.reason}
              </div>
            ))}
          </Section>
        );
      })()}
    </div>
  );
}
