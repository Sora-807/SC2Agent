"""flow.vocab：词表导出（A1）—— 提示词与校验器同源的漂移守卫。"""
import json

from game.operation import COMPOSITE_ACTIONS, OP_CATALOG
from flow.predicates import OPERATOR_ARITY, PREDICATE_SIGNATURES, UNIMPLEMENTED_PREDICATE_OPS
from flow.vocab import dump_vocabulary, render_prompt_card


def test_vocabulary_covers_authoritative_tables():
    """漂移守卫：导出的词表必须与签名表/动作目录逐键一致（提示词不可能落后于校验器）。"""
    v = dump_vocabulary()
    assert set(v["predicates"]) == set(PREDICATE_SIGNATURES)
    assert set(v["operators"]) == set(OPERATOR_ARITY)
    # 可用动作 = OP_CATALOG 减去复合意图（后者 driver 不直接执行，见 forbidden.composite_actions）
    assert set(v["actions"]) == set(OP_CATALOG) - set(COMPOSITE_ACTIONS)
    assert v["predicates"]["arrived"]["params"] == [
        {"name": "group", "required": True},
        {"name": "target", "required": True},
        {"name": "radius", "required": True},
    ]
    assert v["predicates"]["group_count"]["params"][1] == {"name": "type", "required": False}
    assert v["predicates"]["point_toward"]["kind"] == "value"


def test_forbidden_ops_carry_reasons():
    """未实现的谓词/do 操作必须带原因进提示词（"禁止使用 + 为什么"比只列白名单有效）。"""
    v = dump_vocabulary()
    assert set(v["forbidden"]["predicates"]) == set(UNIMPLEMENTED_PREDICATE_OPS)
    assert all(reason for reason in v["forbidden"]["predicates"].values())
    assert set(v["forbidden"]["do_ops"]) == {"start_timer", "stop_timer", "set_local"}
    # 复合意图（driver 不直接执行）与未实现的 step 声明键也要进禁止清单，否则 LLM 会写出静默失效的脚本
    assert set(v["forbidden"]["composite_actions"]) == {"assign_workers"}
    assert set(v["forbidden"]["step_keys"]) == {"locals"}
    assert "assign_workers" not in v["actions"]  # 不出现在可用动作里


def test_vocabulary_is_json_serializable():
    json.dumps(dump_vocabulary(), ensure_ascii=False)  # 不抛 = 可直接进提示词/接口


def test_prompt_card_mentions_every_predicate_and_action():
    card = render_prompt_card()
    for name in PREDICATE_SIGNATURES:
        assert name in card
    for name in OP_CATALOG:
        assert name in card
    assert "terran/marine" in card  # stable id 词汇写进提示词
    assert "on/off/yes/no" in card  # YAML bool 陷阱写进提示词
