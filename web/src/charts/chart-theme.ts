/** 图表读色：轴/网格走 CSS 变量（与 DOM 同一套），系列色固定为粉蓝白主题的
 *  蓝/粉/薰衣草/灰 —— 白底上去饱和，颜色只表达系列身份。 */
export function chartColors(): { axis: string; grid: string; assign: string } {
  const s = getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string): string =>
    s.getPropertyValue(name).trim() || fallback;
  return {
    axis: v("--text-faint", "#5a6069"),
    grid: v("--uplot-grid", "#eceef3"),
    /** assign 动作虚线（canvas 不认 CSS var 字符串，这里展开成色值） */
    assign: v("--accent-yellow-fg", "#96691c"),
  };
}

export function seriesColors(): {
  minerals: string; gas: string; stalled: string; current: string;
  started: string; completed: string;
} {
  return { minerals: "#3e7fae", gas: "#477f66", stalled: "#b05252", current: "#9a86bb",
           started: "#3e7fae", completed: "#477f66" };
}
