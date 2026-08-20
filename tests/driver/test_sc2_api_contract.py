"""driver ↔ burnysc2 API 契约测试：translator 调用的每个 burnysc2 方法真实存在且签名兼容。

不需要启动 SC2——直接 inspect 已安装的 sc2 包。作用：
- 升级 burnysc2 版本时，方法被删/改签名会当场红，而不是真机运行时才炸；
- 保证 "操作集对应到游戏上" 这条承诺在 CI 里持续被验证。
"""
import inspect

import pytest
from sc2.bot_ai import BotAI
from sc2.unit import Unit

from driver.sc2_adapter import TRANSLATORS, UNIMPLEMENTED_ACTIONS
from game import OP_CATALOG

# action -> (调用的 burnysc2 Unit 方法名, 必需位置参数个数)
# 与 sc2_adapter 各 _t_* 实现一一对应；改翻译实现时同步改这里（契约双面锁）。
EXPECTED_UNIT_METHODS: dict[str, tuple[str, int]] = {
    "move_to": ("move", 1),
    "attack_move_to": ("attack", 1),
    "hold_position": ("hold_position", 0),
    "stop": ("stop", 0),
    "follow": ("move", 1),          # V1 近似：move(target_unit)
    "patrol": ("patrol", 1),
    "focus_fire": ("attack", 1),
    "siege": ("__call__", 1),        # 通用能力路径：siege 无具名 Unit 方法（走 __call__(AbilityId)，同挂件 BUILD_REACTOR）
    "unsiege": ("__call__", 1),     # 同上（UNSIEGE_UNSIEGE）
    "build": ("build", 1),          # build(unit, position=None)：必需 1（unit）
    "train": ("train", 1),
    "research": ("research", 1),
    "load": ("smart", 1),           # V1 近似：smart(target_unit)
    "gather": ("gather", 1),
    "build_gas": ("build", 1),        # build(unit, position)：气矿时 position=气井 Unit
}


def _required_positional(sig: inspect.Signature) -> list[inspect.Parameter]:
    """除 self 外的必需位置参数（getattr(Unit, m) 拿到的是未绑定函数，含 self）。"""
    return [
        p for p in list(sig.parameters.values())[1:]
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        and p.default is inspect.Parameter.empty
    ]


@pytest.mark.parametrize("method", sorted({m for m, _ in EXPECTED_UNIT_METHODS.values()}))
def test_unit_method_exists(method: str):
    assert callable(getattr(Unit, method, None)), f"sc2.Unit 缺方法 {method}"


@pytest.mark.parametrize("method,required", sorted(set(EXPECTED_UNIT_METHODS.values())))
def test_unit_method_signature(method: str, required: int):
    sig = inspect.signature(getattr(Unit, method))
    got = len(_required_positional(sig))
    assert got == required, f"sc2.Unit.{method} 必需位置参数 {got} != 契约 {required}"


def test_contract_table_matches_translators():
    """契约表与 TRANSLATORS 双面一致：改了翻译实现必须改契约表。"""
    assert set(EXPECTED_UNIT_METHODS) == set(TRANSLATORS), \
        f"契约表 {set(EXPECTED_UNIT_METHODS) ^ set(TRANSLATORS)} 与 TRANSLATORS 不一致"


def test_bot_ai_has_do():
    """translator 产出的 UnitCommand 经 BotAI.do() 下发——这个通道必须存在。"""
    assert callable(getattr(BotAI, "do", None)), "BotAI 缺 do()（命令下发通道）"


def test_train_build_research_param_types():
    """train/build 的 unit 参数是 UnitTypeId、research 的 upgrade 是 UpgradeId。

    真机实测教训：translator 把字符串直接传入时 burnysc2 静默失败
    （str 没有 .value）——这条契约锁死参数类型，不让它再发生。
    """
    expected = {"train": "UnitTypeId", "build": "UnitTypeId", "research": "UpgradeId"}
    for method, want in expected.items():
        params = list(inspect.signature(getattr(Unit, method)).parameters.values())
        ann = str(params[1].annotation)  # 第二个位置参数（第一个是 self）
        assert want in ann, f"sc2.Unit.{method} 第 2 参数注解 {ann!r} 不含 {want}"


def test_catalog_partitioned_between_translators_and_unimplemented():
    """OP_CATALOG 权威源：每个 action ∈ TRANSLATORS ∪ UNIMPLEMENTED_ACTIONS，且互斥。"""
    implemented = set(TRANSLATORS)
    unimplemented = set(UNIMPLEMENTED_ACTIONS)
    assert implemented & unimplemented == set()
    assert implemented | unimplemented == set(OP_CATALOG)

