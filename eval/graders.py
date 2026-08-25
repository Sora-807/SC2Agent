"""eval.graders：确定性 grader 四件（PLAN §3.6 表的前四行）。

被动型只读 RunResult；SimOutcome 是主动型（D2 只读约束）——但它**不动真队列**：
提案在本框架里已被自动应用进 live 队列（审批下线），simulate 的 from_session
本来就是只读干跑（D15：走 simulate 草稿语义，天然满足只读）。
"""
from __future__ import annotations

from eval.result import Grade, RunResult


def _tool_names(result: RunResult) -> list[str]:
    return [str(t.get("tool")) for t in result.tool_calls if t.get("tool")]


class ToolSequenceGrader:
    """工具调用序列：必调 / 禁调 / 顺序（must 按 appearance 顺序出现）。"""

    name = "tool_sequence"
    axis = "工具序列"

    def __init__(self, must: list[str] | None = None, forbid: list[str] | None = None,
                 order: bool = False) -> None:
        self.must = list(must or [])
        self.forbid = list(forbid or [])
        self.order = order

    def grade(self, result: RunResult, world=None) -> Grade:
        names = _tool_names(result)
        problems = []
        for t in self.must:
            if t not in names:
                problems.append(f"没调 {t}")
        for t in self.forbid:
            if t in names:
                problems.append(f"调了禁用的 {t}")
        if self.order and self.must:
            idx = [names.index(t) for t in self.must if t in names]
            if idx != sorted(idx):
                problems.append(f"顺序不对：{self.must} vs 实际 {names}")
        passed = not problems
        detail = f"；调用序列：{names}" if names else "（无工具调用）"
        return Grade(self.axis, self.name, passed=passed,
                     reason_zh=("；".join(problems) if problems else "序列符合要求") + detail)


def _proposal_items(result: RunResult) -> list[dict]:
    """提案里的队列项（hunks 的 payload.item）——从持久侧 /api/proposals 来。"""
    out = []
    for p in result.proposals:
        for h in p.get("hunks") or []:
            item = (h.get("payload") or {}).get("item")
            if item:
                out.append(item)
    return out


class ProposalGrader:
    """提案正确性：期望的 op/type 出现在提案里（如 insert refinery）。"""

    name = "proposal"
    axis = "提案"

    def __init__(self, expect_op: str | None = None, expect_type: str | None = None,
                 expect_min_count: int = 1) -> None:
        self.expect_op = expect_op
        self.expect_type = expect_type
        self.expect_min_count = expect_min_count

    def grade(self, result: RunResult, world=None) -> Grade:
        items = _proposal_items(result)
        n_ok = 0
        for it in items:
            if self.expect_op and it.get("op") != self.expect_op:
                continue
            if self.expect_type and it.get("type") != self.expect_type:
                continue
            n_ok += 1
        passed = n_ok >= self.expect_min_count
        if not result.proposals:
            reason = "没有提案"
        elif passed:
            reason = f"提案里有 {n_ok} 个期望项（op={self.expect_op}, type={self.expect_type}）"
        else:
            reason = (f"提案里没有期望项（op={self.expect_op}, type={self.expect_type}）；"
                      f"实际提案项：{[{i.get('op'), i.get('type')} for i in items]}")
        # 校验状态（提案存了 validation；未通过也是可见失败）
        bad_validation = [p.get("id") for p in result.proposals
                          if not (p.get("validation") or {}).get("ok")]
        if bad_validation:
            reason += f"；校验未通过：{bad_validation}"
            passed = False
        return Grade(self.axis, self.name, passed=passed, reason_zh=reason)


class SimOutcomeGrader:
    """结果轴（轻管线层）：from_session 只读干跑，断言终态。D15 语义。"""

    name = "sim_outcome"
    axis = "结果"

    def __init__(self, final_has: dict[str, int] | None = None,
                 horizon: float = 120.0) -> None:
        self.final_has = dict(final_has or {})
        self.horizon = horizon

    def grade(self, result: RunResult, world=None) -> Grade:
        if world is None or world.get("client") is None:
            return Grade(self.axis, self.name, passed=None,
                         reason_zh="无 world（主动 grader 需要）")
        res = world["client"].post("/api/plans/simulate", json={
            "from_session": True, "horizon": self.horizon})
        if res.status_code != 200:
            return Grade(self.axis, self.name, passed=False,
                         reason_zh=f"simulate 失败：{res.status_code} {res.text[:120]}")
        final = res.json().get("final") or {}
        buildings: dict[str, int] = final.get("buildings") or {}
        missing = {k: n for k, n in self.final_has.items()
                   if buildings.get(k, 0) < n}
        passed = not missing
        reason = ("终态建筑满足 " + str(self.final_has) if passed
                  else f"终态缺 {missing}（实际 {buildings}）")
        return Grade(self.axis, self.name, passed=passed, reason_zh=reason)


class RegexGrader:
    """文字轴确定性层：最终回复与提案 rationale 的关键词约束。"""

    name = "regex"
    axis = "文字"

    def __init__(self, reply_contains: list[str] | None = None,
                 reply_not_contains: list[str] | None = None,
                 rationale_nonempty: bool = False) -> None:
        self.reply_contains = list(reply_contains or [])
        self.reply_not_contains = list(reply_not_contains or [])
        self.rationale_nonempty = rationale_nonempty

    def grade(self, result: RunResult, world=None) -> Grade:
        problems = []
        text = result.final_text or ""
        for kw in self.reply_contains:
            if kw not in text:
                problems.append(f"回复没提到 {kw!r}")
        for kw in self.reply_not_contains:
            if kw in text:
                problems.append(f"回复不该出现 {kw!r}")
        if self.rationale_nonempty:
            rs = [p.get("rationale_zh") for p in result.proposals]
            if not any(r and str(r).strip() for r in rs):
                problems.append("提案没有非空 rationale")
        return Grade(self.axis, self.name, passed=not problems,
                     reason_zh="；".join(problems) if problems else "文字约束全部满足")
