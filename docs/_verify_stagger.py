"""临时：错位生产区（3×2 栋 3×3 + 右下 2×2 挂件）在步长 1 扫描数据上验证。"""
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


BL_CC = (48.5, 28.5)
TR_CC = (127.5, 119.5)

BL_BUILDINGS = {
    "rax1": ((40, 41), 3), "rax2": ((46, 40), 3), "rax3": ((43, 43), 3),
    "rax4": ((49, 42), 3), "fac1": ((46, 45), 3), "stp1": ((52, 44), 3),
}
BL_ADDONS = {
    "rax1_addon": ((43, 41), 2), "rax2_addon": ((49, 40), 2), "rax3_addon": ((46, 43), 2),
    "rax4_addon": ((52, 42), 2), "fac1_addon": ((49, 45), 2), "stp1_addon": ((55, 44), 2),
}


def pos(tl, size):
    off = size / 2 if size % 2 else (size - 1) / 2
    return (tl[0] + off, tl[1] + off)


def mirror(p, cc):
    return (cc[0] - (p[0] - BL_CC[0]), cc[1] - (p[1] - BL_CC[1]))


for path, cc in [("docs/slot_scan_a.log", BL_CC), ("docs/slot_scan_t1.log", TR_CC)]:
    got_cc, depot, rax = load(path)
    print("=== spawn", got_cc, "===")
    if got_cc is None:
        print("  no data"); continue
    ok = True
    for name, (tl, size) in BL_BUILDINGS.items():
        p = pos(tl, size) if got_cc == BL_CC else mirror(pos(tl, size), got_cc)
        good = p in rax
        ok &= good
        if not good:
            print(f"  FAIL {name} pos={p}")
    for name, (tl, size) in BL_ADDONS.items():
        p = pos(tl, size) if got_cc == BL_CC else mirror(pos(tl, size), got_cc)
        good = p in depot
        ok &= good
        if not good:
            print(f"  FAIL {name} pos={p}")
    print("  ALL BUILDABLE" if ok else "  HAS FAILURES")
