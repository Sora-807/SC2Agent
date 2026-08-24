"""serve_api 端口守卫（2026-08-24）：上一代后端自动树杀、陌生占用不误杀。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT / "modules", ROOT / "tools", ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from serve_api import listening_on, looks_like_our_backend


def test_listening_on_matches_port_and_wildcards():
    conns = [
        ("127.0.0.1", 8770, "LISTEN", 1, "python", "tools/serve_api.py"),   # 命中
        ("127.0.0.1", 8770, "ESTABLISHED", 2, "x", "y"),                    # 不是监听
        ("127.0.0.1", 8771, "LISTEN", 3, "x", "y"),                         # 端口不同
        ("192.168.1.5", 8770, "LISTEN", 4, "x", "y"),                       # 绑在别的地址
        ("0.0.0.0", 8770, "LISTEN", 5, "python", "uvicorn app"),            # 通配监听也算冲突
    ]
    hit = listening_on(conns, "127.0.0.1", 8770)
    assert [p for p, _, _ in hit] == [1, 5]
    # host 自身是通配时，任何地址的监听都冲突
    assert [p for p, _, _ in listening_on(conns, "0.0.0.0", 8770)] == [1, 4, 5]


def test_looks_like_our_backend():
    assert looks_like_our_backend("C:/py/python.exe tools/serve_api.py --port 8770")
    assert looks_like_our_backend("uvicorn api.app:app --port 8770")
    assert not looks_like_our_backend("C:/Game/sc2_x64.exe")
    assert not looks_like_our_backend("")
