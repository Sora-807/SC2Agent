"""view.fmt 直测（N5-d：mmss 从 alerts/observe 双份合一后无直测——补上）。"""
import pytest

from view.fmt import mmss


@pytest.mark.parametrize(("t", "want"), [
    (0.0, "00:00"),
    (9.4, "00:09"),        # round 半秒向下
    (9.6, "00:10"),        # 进位
    (61.0, "01:01"),
    (600.0, "10:00"),
    (3675.0, "61:15"),     # 超一小时不折日——MM:SS 连续分钟（复盘时间轴语义）
    (-3.0, "00:00"),       # 负值钳 0（浮点噪声防御）
])
def test_mmss(t, want):
    assert mmss(t) == want
