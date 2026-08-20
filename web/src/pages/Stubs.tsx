/** 未实现页面的占位（诚实标注阶段 + 展示该页已有的帧数据，证明数据到位、只差渲染） */
import { useFrames } from "../store/frames";
import { Card, Empty, Stub } from "../shell/ui";

export function MapPage() {
  const map = useFrames((s) => s.map);
  const world = useFrames((s) => s.world);
  return (
    <Stub
      stage="F2"
      title="地图"
      will={[
        "分层 canvas：区域 / 槽位 / 建筑(含 footprint 与进度) / 单位(按 flow 组) / 资源饱和度",
        "图层开关；视野与菌毯（GridB64 解码到 offscreen ImageData）",
        "位置插值（仅位置，进度绝不插值）+ 选中检查器",
        "摆放调试叠加（意图槽位 / 期望报告位 / 换位重试落点）",
      ]}
    >
      <Card title="该页数据已到位">
        {map ? (
          <ul className="list-inside list-disc text-neutral-300">
            <li>地图 {map.map_name} {map.size[0]}×{map.size[1]}，出生 {map.spawn}</li>
            <li>大区 {map.regions.big.length} 个 / 叶区 {map.regions.leaf.length} 个</li>
            <li>建造槽位 {map.build_slots.length} 个（br/build_point/reported_position 已由后端算好）</li>
            <li>标记点 {map.pos_marks.length} 个 / 资源点 {map.resource_nodes.length} 个</li>
            <li className={map.terrain ? "" : "text-amber-400"}>
              地形 {map.terrain ? "已下发" : "null —— 需后端 B4，届时从纯色底升级为真地形"}
            </li>
            <li>当前帧可见单位 {world?.units.length ?? 0} 个</li>
          </ul>
        ) : <Empty />}
      </Card>
    </Stub>
  );
}

export function FlowPage() {
  const flow = useFrames((s) => s.flow);
  const schema = useFrames((s) => s.schema);
  return (
    <Stub
      stage="F4"
      title="Flow 状态图"
      will={[
        "ELK 布局 + React Flow 渲染 steps/edges（有环图）",
        "当前节点旋转进度；边悬停显示退出原因",
        "转移历史链、transition_count/limit 进度、exit_record 终态卡",
        "点击节点展开内部分支与本帧命中路径",
      ]}
    >
      <Card title="该页数据已到位">
        <ul className="list-inside list-disc text-neutral-300">
          <li>策略实例 {flow?.strategies.length ?? 0} 个（V1 恒 1；帧为列表形状，多实例可长）</li>
          <li>当前 step {flow?.strategies.at(0)?.active_step ?? "—"}，转移历史 {flow?.strategies.at(0)?.transitions.length ?? 0} 条</li>
          <li>
            谓词 {Object.keys(schema?.predicates ?? {}).length} 个
            （其中 value 型 {Object.values(schema?.predicates ?? {}).filter((p) => p.kind === "value").length} 个可放参数位）·
            运算符 {Object.keys(schema?.operators ?? {}).length} 个 ·
            动作原子 {Object.keys(schema?.actions ?? {}).length} 个
          </li>
          <li>
            不可用共 {schema
              ? Object.values(schema.forbidden).reduce((n, ops) => n + Object.keys(ops).length, 0)
              : 0} 项，分 {Object.keys(schema?.forbidden ?? {}).length} 组
            （编辑器置灰并显示后端给的原因；分组名不枚举，后端新增自动流通）
          </li>
          <li>节点形态 {Object.keys(schema?.node_forms ?? {}).length} 种 · 编译规则 {schema?.rules.length ?? 0} 条（编辑器侧栏直接用）</li>
        </ul>
      </Card>
    </Stub>
  );
}

export function PlanningPage() {
  return (
    <Stub
      stage="F9"
      title="规划（离线）"
      will={[
        "地图规划：区域 / 预留区 / 槽位 / 标记点，模板只读 + 复制成草稿",
        "生产规划：自定义起始状态 + 投影工作台",
        "Flow 装配：AST 结构化编辑器（schema 来自 static/schema，非法连线直接禁止）",
        "所有画布操作转成结构化 patch，人与 agent 同一表示",
      ]}
    >
      <Card title="边界提醒">
        <div className="text-neutral-400">
          离线草稿**绝不默认叠加 live**（ADR-0022 反例）；live 中不出现模块/Strategy 的创建与编辑入口（R5）。
        </div>
      </Card>
    </Stub>
  );
}

export function DebugPage() {
  const { world, production, session } = useFrames();
  return (
    <Stub
      stage="F5"
      title="调试"
      will={[
        "命令流水：op_id/seq/action/unit_tags/params/origin → apply → landing（确认/超时/换位重试）",
        "摆放调试叠加层",
        "原始帧检查器（唯一允许显示 burnysc2 名的地方）",
        "掉项审计表",
      ]}
    >
      <Card title="现状">
        <ul className="list-inside list-disc text-neutral-300">
          <li>帧源 {session?.frame_source ?? "—"}</li>
          <li>掉项 {production?.dropped.length ?? 0} 条已在帧里</li>
          <li className="text-amber-400">
            ops 的 apply/landing 在后端 B9（D6 ApplyResult / D7 GameEvent）之前恒 null —— 面板将显示"未知"而非空白
          </li>
          <li>当前帧单位 {world?.units.length ?? 0} 个</li>
        </ul>
      </Card>
    </Stub>
  );
}