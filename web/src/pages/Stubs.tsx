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
