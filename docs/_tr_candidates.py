"""临时：列出 TR user_down 全部候选（避开补给站网格区）。"""
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
B_OFFS = [(0, 0), (3, 2), (6, -1), (9, 1)]
A_OFFS = [(3, 0), (6, 2), (9, -1), (12, 1)]
DEPOT_GRID = {(x, y) for x in range(131, 139) for y in range(107, 115)}


def b_pos(t):
    return (t[0] + 1.5, t[1] + 1.5)


def a_pos(t):
    return (t[0] + 0.5, t[1] + 0.5)


found = []
for bx in range(int(cc[0]) - 18, int(cc[0]) + 3):
    for by in range(int(cc[1]) - 18, int(cc[1]) + 26):
        bs = [(bx + dx, by + dy) for dx, dy in B_OFFS]
        as_ = [(bx + dx, by + dy) for dx, dy in A_OFFS]
        if not (all(b_pos(t) in rax for t in bs) and all(a_pos(t) in depot for t in as_)):
            continue
        cells = set()
        for dx, dy in B_OFFS + A_OFFS:
            for x in range(bx + dx, bx + dx + 3):
                for y in range(by + dy, by + dy + 3):
                    cells.add((x, y))
        if cells & DEPOT_GRID:
            continue
        found.append((bx, by))
print("TR user_down 候选:", found)
