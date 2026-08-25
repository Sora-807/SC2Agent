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


# ---------------- P2：写策略引用 _lib 模板 ----------------

class StrategyGrader:
    """主动 grader：新策略存在且通过编译校验（写时校验过的自然推论）。"""

    name = "strategy_compiles"
    axis = "提案"

    def __init__(self, strategy_id: str, must_steps: int = 2) -> None:
        self.strategy_id = strategy_id
        self.must_steps = must_steps

    def grade(self, result, world=None) -> Grade:
        if world is None or world.get("app") is None:
            return Grade(self.axis, self.name, passed=None, reason_zh="无 world")
        store = world["app"].state.strategies
        doc = store.get(self.strategy_id)
        if doc is None:
            ids = [s.get("id") for s in store.list()]
            return Grade(self.axis, self.name, passed=False,
                         reason_zh=f"没有 {self.strategy_id}（现有：{ids}）")
        steps = len(doc.get("steps") or [])
        ok = steps >= self.must_steps
        return Grade(self.axis, self.name, passed=ok,
                     reason_zh=f"{self.strategy_id} 有 {steps} 步（要求 ≥{self.must_steps}）"
                               + ("；写时校验通过（能进 store 即已编译）" if ok else ""))


@scenario("P2-strategy-from-lib", tags=["planning"],
          text="参考 _lib 模板写一个三步策略（集结→推进→攻击），保存为 rally-push，"
               "装配里确认能用",
          note="planning 域策略写流：read _lib→write strategy→写时编译校验过",
          max_turns=30,
          graders=[
              ToolSequenceGrader(must=["read", "write"], order=True),
              StrategyGrader("rally-push", must_steps=3),
          ])
def _p2(client) -> None:
    pass


# ---------------- P3：改地图规划槽位（几何校验） ----------------

class MapPlanGrader:
    """主动 grader：地图规划被改过且几何校验通过（同 spawn 下槽位两两不重叠）。"""

    name = "mapplan_valid"
    axis = "提案"

    def __init__(self, plan_id: str) -> None:
        self.plan_id = plan_id

    def grade(self, result, world=None) -> Grade:
        import yaml
        if world is None or not (world.get("extras") or {}).get("map_plans_dir"):
            return Grade(self.axis, self.name, passed=None, reason_zh="无 map_plans_dir")
        path = Path(world["extras"]["map_plans_dir"]) / f"{self.plan_id}.yaml"
        if not path.is_file():
            return Grade(self.axis, self.name, passed=False,
                         reason_zh=f"没有地图规划 {self.plan_id}")
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        overlap = []
        total = 0
        for spawn_id, br in (doc.get("spawns") or {}).items():
            slots = br.get("build_slots") or {}
            rects = []
            for name, s in slots.items():
                pos = (s or {}).get("pos") or [0.0, 0.0]
                size = (s or {}).get("size") or 2
                total += 1
                rects.append((name, pos[0] - size / 2, pos[1] - size / 2, size))
            for i, a in enumerate(rects):
                for b in rects[i + 1:]:
                    if (a[1] < b[1] + b[3] and b[1] < a[1] + a[3]
                            and a[2] < b[2] + b[3] and b[2] < a[2] + a[3]):
                        overlap.append((spawn_id, a[0], b[0]))
        ok = not overlap
        return Grade(self.axis, self.name, passed=ok,
                     reason_zh=(f"{total} 个槽位两两不重叠" if ok
                                else f"仍有重叠：{overlap}"))


# 预置一份带重叠的地图规划（D5 与 D4 同位）—— agent 要读懂几何校验报错并挪开
_OVERLAP_MAPPLAN = """\
id: overlap-test
title_zh: 槽位重叠测试（D5 压在 D4 上）
map_name: LadderMap
spawns:
  bl:
    origin: [48.5, 28.5]
    anchor: [48.5, 28.5]
    build_slots:
      D4: {pos: [54.5, 40.5], size: 2, kind: supply}
      D5: {pos: [54.5, 40.5], size: 2, kind: supply}
      B1: {pos: [55.5, 44.5], size: 3, kind: production}
    pos_marks: {}
"""


def _prepare_p3(tmp: Path) -> None:
    d = tmp / "map-plans"
    d.mkdir(parents=True, exist_ok=True)
    (d / "overlap-test.yaml").write_text(_OVERLAP_MAPPLAN, encoding="utf-8")


@scenario("P3-mapplan-fix-overlap", tags=["planning"],
          text="map-plans/overlap-test 里 D5 槽位和 D4 重叠了，把 D5 挪到不重叠的"
               "位置保存（保持供给类、别动其他槽位）",
          note="planning 域地图规划写流：read→edit→写时几何校验过/拒重叠",
          max_turns=30,
          graders=[
              ToolSequenceGrader(must=["read", "edit"], order=True),
              MapPlanGrader("overlap-test"),
          ],
          prepare=_prepare_p3)
def _p3(client) -> None:
    pass
