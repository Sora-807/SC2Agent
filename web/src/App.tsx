/**
 * 应用外壳（F1；2026-08-22 十五轮：无边缘缝 + 右侧固定工作面板）
 *
 * 结构（全部贴边、区块间只留浅色分割线，不再有圆角卡片间的缝）：
 *   顶栏（全宽，贴视口顶）→ 下排 [左图标栏（贴左边，直接接在顶栏下）|
 *   主列（时间带 + 页面，蓝底白卡）| 对话区（四周留缝，独立浮起）]
 * App 本身不碰帧源实现，只用 store —— 换成 live 时这个文件零改动（决策 U1）。
 */
import { useEffect } from "react";
import { ChatDock } from "./shell/ChatDock";
import { ModeBar } from "./shell/ModeBar";
import { SideRail } from "./shell/SideRail";
import { StartCard } from "./shell/StartCard";
import { TimeStrip } from "./shell/TimeStrip";
import { useSessionStore } from "./shell/session-store";
import { useRoute, planTabOf } from "./shell/route";
import { homePageOf, railGroups } from "./shell/rail";
import { useFrames } from "./store/frames";
import { Overview } from "./pages/Overview";
import { MapPage } from "./pages/MapPage";
import { DebugPage } from "./pages/DebugPage";
import { FlowPage } from "./pages/FlowPage";
import { PlanningPage } from "./pages/PlanningPage";
import { ProductionPage } from "./pages/ProductionPage";
import { EvalPage } from "./pages/EvalPage";

export function App() {
  const [page, go, params] = useRoute();
  const mode = useFrames((s) => s.mode);
  const apiOk = useFrames((s) => s.api.ok);
  const { init, error, loading, fixtures } = useFrames();
  const live = useSessionStore((s) => s.info?.alive === true);

  // P1：导航是模式的函数 —— 当前页不属于本模式（切模式 / 旧链接直达）时跳到该模式首页。
  // 驾驶页在规划模式仍有意义吗？没有：离线没有"正在发生的世界"（用户拍板）。
  useEffect(() => {
    const valid = railGroups(mode).some((g) => g.items.some((p) => p.key === page));
    if (!valid) go(homePageOf(mode));
  }, [mode, page, go]);

  // 会话面轮询（游戏模式 && 后端在线）：ModeBar 指示灯与 StartCard 共享这一份
  useEffect(() => {
    useSessionStore.getState().setWatch(mode === "drive" && apiOk);
    return () => useSessionStore.getState().setWatch(false);
  }, [mode, apiOk]);

  useEffect(() => {
    void init();
    return () => useFrames.getState().detach();
  }, [init]);

  if (error) {
    return (
      <div className="h-[100dvh] overflow-y-auto bg-base p-6 text-[color:var(--err-fg)]">
        <h1 className="text-lg font-bold">帧源出错</h1>
        <pre className="mt-3 whitespace-pre-wrap text-body">{error}</pre>
        <p className="mt-3 text-[color:var(--on-base-text)]">
          如果是"夹具清单读不到"，先在 web/ 下跑 <code>pnpm gen:fixtures</code>。
          如果是游戏模式连不上（后端没在跑 / 契约版本不符），点下面返回规划，
          到游戏模式里点「启动真机」再试。
        </p>
        {/* 全屏错误不能死锁：一键回规划（清 error + 回夹具源） */}
        <button
          className="btn btn-ghost px-3 text-strong"
          onClick={() => {
            useFrames.setState({ error: null });
            void useFrames.getState().setMode("offline");
          }}
        >返回规划模式</button>
      </div>
    );
  }

  // 外壳固定一屏（红线 G1/U13）：`h-[100dvh] overflow-hidden` + 一路 `min-h-0`，
  // 滚动权下放给**页面自己的 pane**。用 dvh 不用 vh：带地址栏时 vh 会比可视区大。
  // 游戏模式没活会话时主区是 StartCard（没有可看的驾驶面，启动卡就是首页）。
  const driveIdle = mode === "drive" && !live;
  return (
    <div className="flex h-[100dvh] flex-col overflow-hidden text-body">
      <ModeBar />
      <div className="flex min-h-0 flex-1">
        <SideRail page={page} go={go} />
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          {mode !== "offline" && !driveIdle && <TimeStrip />}
          <main className="relative min-h-0 min-w-0 flex-1 overflow-hidden p-2">
            {loading || fixtures.length === 0 ? (
              <div className="p-3 text-[color:var(--on-base-text)]">加载帧…</div>
            ) : driveIdle ? (
              <StartCard />
            ) : (
              <>
                {page === "overview" && <Overview />}
                {page === "map" && <MapPage />}
                {page === "production" && <ProductionPage />}
                {page === "flow" && <FlowPage />}
                {/* 规划三入口同组件：rail 键 → 初始 tab（F13c）。查询参数是深链选中
                    （chat 的改动 chip 跳进来带 ?plan=/?map=）—— initOnce 幂等消费 */}
                {(page === "plan-map" || page === "plan-production" || page === "plan-flow") && (
                  <PlanningPage key={page + params.toString()}
                                initialTab={planTabOf(page)}
                                initialPlanId={params.get("plan")}
                                initialMapPlanId={params.get("map")} />
                )}
                {page === "eval" && <EvalPage projectId={params.get("project")}
                                               runDir={params.get("run")} />}
                {page === "debug" && <DebugPage />}
              </>
            )}
          </main>
        </div>
        {/* 对话区（十七轮终形）：右/上/下贴边（与顶栏对齐，不留缝），只有左缘
            留 8px 蓝缝（来自 main 的 p-2 右缘）与工作卡隔开 */}
        <div className="flex shrink-0 flex-col">
          <ChatDock />
        </div>
      </div>
    </div>
  );
}
