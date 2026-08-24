"""仿真模式倍速配速（driver.sc2_adapter.pace_sleep_seconds 纯函数）。

N 倍速 = 每真实秒推进 N 游戏秒 → 允许墙钟 = game_dt / N；睡掉「允许墙钟 − 实际墙钟」。
"""
from driver.sc2_adapter import pace_sleep_seconds


def test_unlimited_when_no_multiplier():
    assert pace_sleep_seconds(10.0, None, 0.0) == 0.0
    assert pace_sleep_seconds(10.0, 0, 0.0) == 0.0
    assert pace_sleep_seconds(10.0, 1, 0.0) == 0.0   # ≤1 不配速（要慢于全速没有意义）


def test_paces_to_multiplier():
    # 4×：10 游戏秒只许花 2.5 墙秒；已花 1.0 → 还睡 1.5
    assert abs(pace_sleep_seconds(10.0, 4, 1.0) - 1.5) < 1e-9
    # 已超时 → 不睡（不追帧，宁可慢于目标）
    assert pace_sleep_seconds(10.0, 4, 3.0) == 0.0


def test_zero_game_delta_no_sleep():
    assert pace_sleep_seconds(0.0, 8, 0.0) == 0.0
