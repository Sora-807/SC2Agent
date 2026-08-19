"""临时：搜索 4 楼 + 4 挂件错位图案（出生点相对范围，避开补给站 4×4 网格区）。"""
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


PATTERNS = {
    "user_down":      ([(0, 0), (6, -1), (3, 2), (9, 1)],  [(3, 0), (9, -1), (6, 2), (12, 1)]),
    "user_up":        ([(0, 0), (6, 1), (3, 2), (9, 3)],   [(3, 0), (9, 1), (6, 2), (12, 3)]),
    "stair_down":     ([(0, 0), (3, -1), (6, -2), (9, -3)], [(3, 0), (6, -1), (9, -2), (12, -3)]),
    "stair_up":       ([(0, 0), (3, 1), (6, 2), (9, 3)],   [(3, 0), (6, 1), (9, 2), (12, 3)]),
}


def b_pos(t):
    return (t[0] + 1.5, t[1] + 1.5)


def a_pos(t):
    return (t[0] + 0.5, t[1] + 0.5)


def footprint_cells(bx, by, boffs, aoffs):
    cells = set()
    for dx, dy in boffs + aoffs:
        for x in range(bx + dx, bx + dx + 3):
            for y in range(by + dy, by + dy + 3):
                cells.add((x, y))
    return cells


for path, depot_grid, cc_ref in [
    ("docs/slot_scan_a.log", ((40, 47), (32, 39)), (48.5, 28.5)),
    ("docs/slot_scan_t1.log", ((131, 138), (107, 114)), (127.5, 119.5)),
]:
    cc, depot, rax = load(path)
    print("=== spawn", cc, "===")
    for pname, (boffs, aoffs) in PATTERNS.items():
        found = []
        for bx in range(int(cc[0]) - 18, int(cc[0]) + 3):
            for by in range(int(cc[1]) - 18, int(cc[1]) + 26):
                bs = [(bx + dx, by + dy) for dx, dy in boffs]
                as_ = [(bx + dx, by + dy) for dx, dy in aoffs]
                if not (all(b_pos(t) in rax for t in bs) and all(a_pos(t) in depot for t in as_)):
                    continue
                cells = footprint_cells(bx, by, boffs, aoffs)
                (gx0, gx1), (gy0, gy1) = depot_grid
                if cells & {(x, y) for x in range(gx0, gx1 + 1) for y in range(gy0, gy1 + 1)}:
                    continue  # 与补给站网格区重叠
                found.append((bx, by))
        print(f"  {pname:12s} 候选: {found[:8]}{' ...' if len(found) > 8 else ''} 共 {len(found)}")
