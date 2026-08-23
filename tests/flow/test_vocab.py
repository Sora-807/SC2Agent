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
    assert set(UNIMPLEMENTED_PREDICATE_OPS) == {"has_ready_base", "event_occurred", "user_cancel"}
    assert all(reason for reason in v["forbidden"]["predicates"].values())
    # 二十六轮 T8：timer 族与 locals 转正 —— do_ops/step_keys 禁区清空
    assert set(v["forbidden"]["do_ops"]) == set()
    assert set(v["forbidden"]["step_keys"]) == set()
    # 复合意图（driver 不直接执行）也要进禁止清单，否则 LLM 会写出静默失效的脚本
    assert set(v["forbidden"]["composite_actions"]) == {"assign_workers"}
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


def test_zh_aliases_cover_every_entry():
    """I1：谓词/运算符/动作全部有中文名 —— 新增词忘了配 zh 当场红，
    UI/提示词不会悄悄退回裸标识符（group_count 这类读不懂的名字是 I1 的起因）。"""
    from flow.vocab import ACTION_ZH, OPERATOR_ZH, PREDICATE_ZH

    assert set(PREDICATE_ZH) == set(PREDICATE_SIGNATURES)
    assert set(OPERATOR_ZH) == set(OPERATOR_ARITY)
    # 动作表允许比"可直接发"多（assign_workers 是复合意图不发，但 zh 留着给队列侧用）
    assert set(OP_CATALOG) - set(COMPOSITE_ACTIONS) <= set(ACTION_ZH)
    v = dump_vocabulary()
    for name, spec in v["predicates"].items():
        assert spec["name_zh"] == PREDICATE_ZH[name]
    assert v["predicates"]["group_count"]["name_zh"] == "组内数量"
    assert v["predicates"]["arrived"]["name_zh"] == "已抵达"
    assert v["operators"][">="]["name_zh"] == "≥"
    assert v["operators"]["and"]["name_zh"] == "且"
    assert v["actions"]["attack_move_to"]["name_zh"] == "攻击移动"


def test_prompt_card_mentions_zh_aliases():
    """提示词卡片带中文名（rev 12）：LLM 回复里的条件描述能直接用中文说。"""
    card = render_prompt_card()
    assert "组内数量" in card
    assert "已抵达" in card
    assert "攻击移动" in card
