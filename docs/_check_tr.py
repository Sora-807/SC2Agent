"""临时：TR 侧 user_down 图案在 (135,107) 附近是否成立 + 输出镜像布局坐标。"""
import re


def load(path):
    depot, rax, cc = set(), set(), None
    for line in open(path, encoding="utf-8", errors="replace"):
        if "spawn CC=" in line:
            nums = re.findall(r"[\d.]+", line.split("CC=")[1])
            cc = (float(nums[0]), float(nums[1]))
        m = re.match(r"BUILDABLE (\w+) world=\(([\d.]+),([\d.]+)\)", line)
        if m:
            (depot if m.group(1) == "depot" else rax).add((float(m.group(2)), float(m.group(3))))
    return cc, depot, rax


cc, depot, rax = load("docs/slot_scan_t1.log")
print("TR CC =", cc)

B_OFFS = [(0, 0), (3, 2), (6, -1), (9, 1)]
A_OFFS = [(3, 0), (6, 2), (9, -1), (12, 1)]


def b_pos(t):
    return (t[0] + 1.5, t[1] + 1.5)


def a_pos(t):
    return (t[0] + 0.5, t[1] + 0.5)


for bx, by in [(135, 107), (134, 108), (136, 107), (135, 108)]:
    bs = [(bx + dx, by + dy) for dx, dy in B_OFFS]
    as_ = [(bx + dx, by + dy) for dx, dy in A_OFFS]
    ok_b = all(b_pos(t) in rax for t in bs)
    ok_a = all(a_pos(t) in depot for t in as_)
    print(f"B1 tl ({bx},{by}): buildings OK={ok_b} addons OK={ok_a}")
    if ok_b and ok_a:
        for i, t in enumerate(bs):
            print(f"  rax{i+1} tl={t} pos={b_pos(t)}")
        for i, t in enumerate(as_):
            print(f"  rax{i+1}_addon tl={t} pos={a_pos(t)}")
