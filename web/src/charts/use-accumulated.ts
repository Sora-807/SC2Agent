/**
 * 回放投影的历史累积（F17；二十四轮抽成共享 hook）—— 投影帧只含
 * [based_on, +horizon]，把走过的每秒累积起来，数据像向左流走一样保留。
 *
 * 两个消费方吃同一份语义（二十四轮用户拍板的「生产队列 = 整局操作序列」）：
 * - ProjectionBoard：曲线 + 泳道画整段历史；
 * - 复盘生产页的队列卡：整局操作序列（拖时间轴不再重排，只会随回放推进变长）。
 * 帧源大幅回退（向后拖时间轴 / 换源）→ 历史与新帧不连续，重新累积。
 */
import { useEffect, useRef, useState } from "react";
import type { ProjectionFrame } from "../contract";
import { accumulateInto } from "./gantt-data";

export function useAccumulatedProjection(frame: ProjectionFrame): ProjectionFrame {
  const histRef = useRef<{
    points: Map<number, ProjectionFrame["points"][number]>;
    events: Map<string, ProjectionFrame["events"][number]>;
  }>({ points: new Map(), events: new Map() });
  const lastBasedRef = useRef(Number.NEGATIVE_INFINITY);
  const [merged, setMerged] = useState<ProjectionFrame>(frame);
  useEffect(() => {
    const h = histRef.current;
    if (frame.based_on_game_time < lastBasedRef.current - 5) {
      h.points.clear();
      h.events.clear();
    }
    lastBasedRef.current = frame.based_on_game_time;
    setMerged(accumulateInto(h, frame));
  }, [frame]);
  return merged;
}
