"""eval.scenarios.planning：planning 域（PLAN §3.11 P1；P2/P3 批 2）。

P1 同时是两个框架能力的示范：
- prepare 预置钩子：写 tmp/plans/*.yaml，fixture 约定式挂载（场景不碰 app 装配）；
- 场景自带主动 grader：PlansGrader 读 world 里的 PlanStore 断言规划内容 ——
  「新检查项靠拼接」的实例（grader 只读世界，D2）。
"""
from __future__ import annotations

from pathlib import Path

from eval.graders import Grade, ToolSequenceGrader
from eval.scenario import scenario

_OPENING_YAML = """\
id: opening
title_zh: 开局（精炼厂在工厂后面—— 卡气）
map: LadderMap
spawn: bl
queue:
- op: train
  type: terran/scv
  count: 2
- op: build
  type: terran/barracks
  count: 1
  placement:
    kind: in_region
    region: home
- op: build
  type: terran/factory
  count: 1
  placement:
    kind: in_region
    region: home
- op: build
  type: terran/refinery
  count: 1
- op: train
  type: terran/marine
  count: 6
"""


class PlansGrader:
    """主动 grader：读 world 的 PlanStore，断言目标规划里 refinery 排在 factory 前。"""

    name = "plans_order"
    axis = "提案"

    def grade(self, result, world=None) -> Grade:
        if world is None or world.get("app") is None:
            return Grade(self.axis, self.name, passed=None, reason_zh="无 world")
        store = world["app"].state.plans
        doc = store.get("opening-v2")
        if doc is None:
            others = [p.get("id") for p in store.list()]
            return Grade(self.axis, self.name, passed=False,
                         reason_zh=f"没有 opening-v2 规划（现有：{others}）")
        queue = [it for it in (doc.get("queue") or [])
                 if it.get("type") in ("terran/refinery", "terran/factory")]
        kinds = [it.get("type") for it in queue]
        ok = kinds == ["terran/refinery", "terran/factory"]
        return Grade(self.axis, self.name, passed=ok,
                     reason_zh=f"顺序 {kinds}（期望 refinery 在 factory 前）")


def _prepare(tmp: Path) -> None:
    plans = tmp / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / "opening.yaml").write_text(_OPENING_YAML, encoding="utf-8")


@scenario("P1-plan-refinery-first", tags=["planning"],
          text="读 plans/opening.yaml，复制成 opening-v2 并把精炼厂挪到工厂前面，"
               "试算对比后报告结果",
          note="planning 域文件流：read→write→simulate_plan→报告；成品精炼厂在前",
          max_turns=30,
          graders=[
              ToolSequenceGrader(must=["read", "write", "simulate_plan"], order=True),
              PlansGrader(),
          ])
def _p1(client) -> None:
    # planning 域不需要对局局面；预置全在 prepare（不启对局也行，但保持同款回路）
    pass
