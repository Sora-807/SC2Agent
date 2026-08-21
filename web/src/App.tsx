/**
 * 应用外壳（F1）
 *
 * 结构 = 会话条 / 时间线 / 左图标栏 / 主区 / 右对话栏（plan-frontend.md §5）。
 * App 本身不碰帧源实现，只用 store —— 换成 live 时这个文件零改动（决策 U1）。
 */
import { useEffect, useState } from "react";
import { ProposalHost } from "./panels/ProposalHost";
import { ChatDock } from "./shell/ChatDock";
import { IconRail } from "./shell/IconRail";
import { SessionBar } from "./shell/SessionBar";
import { StatusChip } from "./shell/StatusChip";
import { Timeline } from "./shell/Timeline";
import { useRoute, planTabOf } from "./shell/route";
import { homePageOf, railGroups } from "./shell/rail";
import { useFrames } from "./store/frames";
import { Overview } from "./pages/Overview";
import { MapPage } from "./pages/MapPage";
import { DebugPage } from "./pages/DebugPage";
import { FlowPage } from "./pages/FlowPage";
import { PlanningPage } from "./pages/PlanningPage";
import { ProductionPage } from "./pages/ProductionPage";

export function App() {
  const [page, go] = useRoute();
  const mode = useFrames((s) => s.mode);
  const { init, error, loading, fixtures } = useFrames();

  // P1：导航是模式的函数 —— 当前页不属于本模式（切模式 / 旧链接直达）时跳到该模式首页。
  // 驾驶页在离线模式仍有意义吗？没有：离线没有"正在发生的世界"（用户拍板）。
  useEffect(() => {
    const valid = railGroups(mode).some((g) => g.items.some((p) => p.key === page));
    if (!valid) go(homePageOf(mode));
  }, [mode, page, go]);
  // 提案选中态放在外壳：对话栏点开 → 主区显示审批（双投影图在 320px 侧栏里没法看）
  const [openProposal, setOpenProposal] = useState<string | null>(null);
  const proposals = useFrames((s) => s.proposals);
  const reviewing = openProposal
    ? proposals?.proposals.find((p) => p.id === openProposal) ?? null
    : null;

  useEffect(() => {
    void init();
    return () => useFrames.getState().detach();
  }, [init]);

  if (error) {
    return (
      <div className="h-[100dvh] overflow-y-auto p-6 text-red-400">
        <h1 className="text-lg font-bold">帧源出错</h1>
        <pre className="mt-3 whitespace-pre-wrap text-sm">{error}</pre>
        <p className="mt-3 text-dim">
          如果是"夹具清单读不到"，先在 web/ 下跑 <code>pnpm gen:fixtures</code>。
          如果是实时驾驶连不上（后端没在跑 / 契约版本不符），点下面返回离线，
          到实时驾驶模式里点「启动真机」再试。
        </p>
        {/* 全屏错误不能死锁：一键回离线（清 error + 回夹具源） */}
        <button
          className="mt-4 rounded border border-neutral-600 px-3 py-1 text-sm text-neutral-200"
          onClick={() => {
            useFrames.setState({ error: null });
            void useFrames.getState().setMode("offline");
          }}
        >返回离线模式</button>
      </div>
    );
  }

  // 外壳固定一屏（红线 G1/U13）：`h-[100dvh] overflow-hidden` + 一路 `min-h-0`，
  // 滚动权下放给**页面自己的 pane**。原先是 `min-h-screen`（最小高度，会长）+ main
  // `overflow-auto`，于是永远有东西可滚 —— 滚轮缩放时页面跟着动的另一半原因（根因 B）。
  // 用 dvh 不用 vh：带地址栏时 vh 会比可视区大。
  return (
    <div className="flex h-[100dvh] flex-col overflow-hidden p-3 text-sm">
      <div className="shrink-0">
        <SessionBar />
        <Timeline />
      </div>
      <div className="flex min-h-0 flex-1">
        <IconRail page={page} go={go} />
        <main className="relative min-h-0 min-w-0 flex-1 overflow-hidden px-3">
          {loading || fixtures.length === 0 ? (
            <div className="p-6 text-faint">加载帧…</div>
          ) : openProposal ? (
            <ProposalHost id={openProposal} fromFrame={reviewing}
                          onClose={() => setOpenProposal(null)} />
          ) : (
            <>
              {page === "overview" && <Overview />}
              {page === "map" && <MapPage />}
              {page === "production" && <ProductionPage />}
              {page === "flow" && <FlowPage />}
              {/* 规划三入口同组件：rail 键 → 初始 tab（F13c） */}
              {(page === "plan-map" || page === "plan-production" || page === "plan-flow") && (
                <PlanningPage initialTab={planTabOf(page)} />
              )}
              {page === "debug" && <DebugPage />}
            </>
          )}
          <StatusChip />
        </main>
        <ChatDock selected={openProposal} onOpen={setOpenProposal} />
      </div>
    </div>
  );
}