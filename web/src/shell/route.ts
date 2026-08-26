/** 极简 hash 路由（不引 react-router：页面不多，URL 可分享即够） */
import { useEffect, useState } from "react";

/**
 * 页面清单（F13c 分组）：
 * - drive 驾驶：概览 / 地图 / 生产 / Flow（看现在）
 * - plan  规划：地图规划 / 生产规划 / Flow 装配（authoring —— drive 模式下按 R5 门控）
 * - diag  诊断：调试（帧检查暂并入调试页，不单造页面）
 * `initialTab`：规划三入口共用 PlanningPage，各自落到对应 tab。
 */
export const PAGES = [
  { key: "overview", label: "概览", icon: "▤", group: "drive" },
  { key: "map", label: "地图", icon: "◫", group: "drive" },
  { key: "production", label: "生产", icon: "▦", group: "drive" },
  { key: "flow", label: "Flow", icon: "◈", group: "drive" },
  { key: "plan-map", label: "地图规划", icon: "✥", group: "plan" },
  { key: "plan-production", label: "生产规划", icon: "✎", group: "plan" },
  { key: "plan-flow", label: "Flow 装配", icon: "❖", group: "plan" },
  { key: "eval", label: "评测", icon: "✓", group: "diag" },
  { key: "debug", label: "调试", icon: "⚙", group: "diag" },
] as const;

export type PageKey = (typeof PAGES)[number]["key"];
export type PageGroup = "drive" | "plan" | "diag";

export const PAGE_GROUP_LABEL: Record<PageGroup, string> = {
  drive: "驾驶",
  plan: "规划",
  diag: "诊断",
};

/** 规划入口 → PlanningPage 的初始 tab（三入口同组件，见 App.tsx） */
export function planTabOf(key: PageKey): "map" | "production" | "flow" {
  if (key === "plan-map") return "map";
  if (key === "plan-flow") return "flow";
  return "production";
}

const isPage = (v: string): v is PageKey => PAGES.some((p) => p.key === v);

/** hash 里的路由信息：页 + 查询参数（如 `#/plan-production?plan=agent-m1`）。 */
export interface Route {
  page: PageKey;
  params: URLSearchParams;
}

/** 纯解析（可测）：`plan-production?plan=x` → {page, params}。旧 #/planning 兼容。 */
export function parseRoute(raw: string): Route {
  const [path = "", query = ""] = raw.split("?");
  // 旧链接 #/planning 落到生产规划（F9 时代的单入口）
  const page: PageKey = path === "planning" ? "plan-production"
    : isPage(path) ? path : "overview";
  return { page, params: new URLSearchParams(query) };
}

function current(): Route {
  return parseRoute(window.location.hash.replace(/^#\/?/, ""));
}

export function useRoute(): [PageKey, (p: PageKey) => void, URLSearchParams] {
  const [route, setRoute] = useState<Route>(current);
  useEffect(() => {
    const onHash = (): void => setRoute(current());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const go = (p: PageKey): void => {
    window.location.hash = "#/" + p;
  };
  return [route.page, go, route.params];
}
