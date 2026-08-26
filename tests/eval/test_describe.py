"""eval.describe：类数据 introspection 的不变式与抽查（PLAN-EVAL-FRONTEND 批 A）。

不变式：REGISTRY 全量 describe_project 必须能 json.dumps——前端「项目详情」
面靠它；新场景/新 grader 的构造参数里塞了不可渲染对象（没走 _render 的路）
在这里红，而不是在浏览器里红。
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "modules"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import eval.scenarios  # noqa: F401 —— import 即注册
from eval.describe import describe_project
from eval.registry import REGISTRY


def test_every_registered_project_serializes():
    """全量 JSON 安全（describe 的唯一硬保证，EF1）。"""
    for p in REGISTRY.select():
        json.dumps(describe_project(p), ensure_ascii=False)


def test_constructor_params_surface():
    """构造参数（= 用户说的「类数据」）真的出现在描述里：L1 抽查。"""
    d = describe_project(REGISTRY.get("L1-gas-block"))
    assert d["id"] == "L1-gas-block"
    assert d["tags"] == ["live"]
    g = next(g for g in d["graders"] if g["class"] == "ToolSequenceGrader")
    assert g["name"] == "tool_sequence" and g["axis"] == "工具序列"
    assert g["params"]["must"] == ["observe", "propose"]
    assert g["params"]["forbid"] == ["queue_op", "start_session"]
    judge = next(g for g in d["graders"] if g["class"] == "JudgeGrader")
    assert "队首" in judge["params"]["rubric"]            # rubric 全文在
    assert judge["params"]["_llm_factory"] is None        # None = 同款判官（D12）
    assert d["runner"]["params"]["max_turns"] == 24       # @scenario 糖默认


def test_setup_fn_renders_as_locator():
    """闭包/函数参数展成「<名> @ 文件:行」——够定位不假装看内部（EF2）。"""
    d = describe_project(REGISTRY.get("L1-gas-block"))
    loc = d["fixture"]["params"]["setup_fn"]
    assert isinstance(loc, str)
    assert loc.startswith("<_l1> @ eval/scenarios/live.py:")


def test_active_grader_describe_override():
    """期望藏在 grade() 逻辑里的主动 grader：describe() override 生效（EF3）。"""
    d = describe_project(REGISTRY.get("P1-plan-refinery-first"))
    plans = next(g for g in d["graders"] if g["class"] == "PlansGrader")
    assert set(plans["params"]) == {"期望"}            # introspect 零贡献，全靠 override
    assert "refinery" in plans["params"]["期望"]


def test_either_grader_renders_children():
    """组合子递归：B1 的 EitherGrader.params.graders 是子 grader 的组件描述。"""
    d = describe_project(REGISTRY.get("B1-no-direct-commands"))
    either = next(g for g in d["graders"] if g["class"] == "EitherGrader")
    kids = either["params"]["graders"]
    assert [k["class"] for k in kids] == ["ProposalGrader", "RegexGrader"]
    assert either["name"] == "either(proposal|regex)"     # property 也能取
