"""view.jsonl 直测（N5-d：live 推送/复盘录制/离线夹具共用的帧序列格式）。

同一格式三处消费（决策 U1/U2），读写往返与「坏行带行号抛」是不变量——
此前只被 api/sources 间接罩着，格式层没有自己的锁。
"""
import pytest

from view.jsonl import read_frames, write_frames


FRAMES = [
    {"topic": "static/map", "seq": 0, "game_time": 0.0, "payload": {"slots": []}},
    {"topic": "frame/world", "seq": 1, "game_time": 1.0, "payload": {"units": []}},
    {"topic": "frame/world", "seq": 2, "game_time": 2.0, "payload": {"中文": "值"}},
]


def test_roundtrip(tmp_path):
    path = tmp_path / "sub" / "rec.jsonl"   # 父目录不存在 → 自动建
    n = write_frames(path, FRAMES)
    assert n == 3
    assert list(read_frames(path)) == FRAMES


def test_written_lines_are_one_envelope_per_line(tmp_path):
    """一行一条、紧凑分隔（读侧逐行 json.loads 的前提）。"""
    import json
    path = tmp_path / "r.jsonl"
    write_frames(path, FRAMES)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert [json.loads(l) for l in lines] == FRAMES
    assert '"中文":"值"' in lines[2]           # ensure_ascii=False：中文原样不转义


def test_empty_lines_skipped(tmp_path):
    path = tmp_path / "r.jsonl"
    path.write_text('\n{"a": 1}\n\n{"b": 2}\n\n', encoding="utf-8")
    assert list(read_frames(path)) == [{"a": 1}, {"b": 2}]


@pytest.mark.parametrize("bad", ["{不是json", '{"a": 1'])
def test_bad_line_raises_with_lineno(tmp_path, bad):
    """非法 JSON 直接抛并带行号（不静默跳过：坏帧要能被发现）。
    （`[]` 等合法 JSON 不拦——信封形状校验在消费侧，本层只管 JSON 行。）"""
    path = tmp_path / "r.jsonl"
    path.write_text('{"ok": 1}\n' + bad + '\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"r\.jsonl:2"):
        list(read_frames(path))
