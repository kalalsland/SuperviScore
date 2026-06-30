# -*- coding: utf-8 -*-
"""
串行执行所有学校的导师抓取任务。

用法（在 main/ 目录下）:
    python scripts/run_all.py            # 跑所有未完成的学校
    python scripts/run_all.py --list     # 仅打印状态列表，不执行
    python scripts/run_all.py sjtu_cs    # 单独强制执行某所学校（忽略已完成标记）

断点续跑：若某学校输出目录下已有 推荐名单*.csv，自动跳过。
隔离方式：每所学校在同一进程内通过修改 config 模块属性完成切换，
         不修改 config.py 文件，无需担心中断残留。
"""
from __future__ import annotations
import os
import sys
import glob
import datetime
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_DIR   = os.path.dirname(SCRIPT_DIR)
os.chdir(MAIN_DIR)
sys.path.insert(0, MAIN_DIR)

# ── 学校 → DBLP affiliation 关键词（用于作者消歧） ──────────────────────────
AFFILIATION_MAP: dict[str, list[str]] = {
    "sjtu_cs":           ["Shanghai Jiao Tong", "Jiao Tong University"],
    "sjtu_bme":          ["Shanghai Jiao Tong", "Jiao Tong University"],
    "sjtu_see":          ["Shanghai Jiao Tong", "Jiao Tong University"],
    "sjtu_icisee":       ["Shanghai Jiao Tong", "Jiao Tong University"],
    "sjtu_sais":         ["Shanghai Jiao Tong", "Jiao Tong University"],
    "fudan_ai":          ["Fudan"],
    "fudan_sds":         ["Fudan"],
    "fudan_bme":         ["Fudan"],
    "ruc_info":          ["Renmin University"],
    "seu_cse":           ["Southeast University"],
    "seu_cyber":         ["Southeast University"],
    "shanghaitech_sist": ["ShanghaiTech"],
    "ustc_cs":           ["University of Science and Technology of China", "USTC"],
    "ustc_auto":         ["University of Science and Technology of China", "USTC"],
    "ustc_aids":         ["University of Science and Technology of China", "USTC"],
}


def detect_schools() -> list[str]:
    """从 schools/ 目录自动检测学校名（排除 base.py / __init__.py）。"""
    schools_dir = os.path.join(MAIN_DIR, "schools")
    exclude = {"__init__", "base"}
    names = []
    for f in sorted(glob.glob(os.path.join(schools_dir, "*.py"))):
        name = os.path.splitext(os.path.basename(f))[0]
        if name not in exclude:
            names.append(name)
    return names


def _output_dir(school_name: str) -> str:
    """获取该学校的输出目录路径（不修改 config 状态）。"""
    import config
    from schools.base import get_parser
    p = get_parser(school_name)
    return os.path.join(config.OUTPUT_ROOT, p.output_dir_name)


def is_done(school_name: str) -> bool:
    """检查该学校是否已有 CSV 输出（断点续跑判断依据）。"""
    try:
        out_dir = _output_dir(school_name)
        return len(glob.glob(os.path.join(out_dir, "推荐名单*.csv"))) > 0
    except Exception:
        return False


def run_school(school_name: str) -> None:
    """切换 config 属性后执行该学校的完整流水线。"""
    import config
    config.SCHOOL = school_name
    config.SCHOOL_DBLP_AFFILIATION = AFFILIATION_MAP.get(school_name, [""])
    from core import pipeline
    pipeline.run()


def _ts() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main() -> None:
    args = sys.argv[1:]

    # --list 模式：仅打印状态
    if "--list" in args:
        schools = detect_schools()
        print(f"共 {len(schools)} 所学校:\n")
        for s in schools:
            try:
                done = is_done(s)
                status = "[√ 已完成]" if done else "[ 待执行 ]"
            except Exception:
                status = "[? 检测失败]"
            aff = AFFILIATION_MAP.get(s, ["(未配置)"])
            print(f"  {status}  {s:<22}  DBLP: {', '.join(aff)}")
        return

    # 指定单校强制执行
    if args and not args[0].startswith("--"):
        force_school = args[0]
        schools_all = detect_schools()
        if force_school not in schools_all:
            print(f"错误：{force_school} 不在已知学校列表中: {schools_all}")
            sys.exit(1)
        pending = [force_school]
        print(f"强制执行: {force_school}")
    else:
        schools_all = detect_schools()
        print(f"检测到 {len(schools_all)} 所学校: {', '.join(schools_all)}\n")
        pending, skipped = [], []
        for s in schools_all:
            (skipped if is_done(s) else pending).append(s)
        if skipped:
            print(f"已有输出，跳过: {', '.join(skipped)}")
        print(f"待执行 ({len(pending)}): {', '.join(pending) or '无'}\n")
        if not pending:
            print("全部学校已完成，无需执行。")
            return

    results: dict[str, str] = {}
    for i, school in enumerate(pending, 1):
        aff = AFFILIATION_MAP.get(school, [])
        print(f"\n{'='*62}")
        print(f"[{i}/{len(pending)}]  {school}   |   {_ts()}")
        print(f"  DBLP affiliation: {aff}")
        print(f"{'='*62}\n")
        t0 = datetime.datetime.now()
        try:
            run_school(school)
            elapsed = str(datetime.datetime.now() - t0).split(".")[0]
            results[school] = f"OK（{elapsed}）"
            print(f"\n[OK] {school}  耗时 {elapsed}  完成于 {_ts()}")
        except Exception as e:
            elapsed = str(datetime.datetime.now() - t0).split(".")[0]
            results[school] = f"ERROR: {e}"
            print(f"\n[ERROR] {school}  耗时 {elapsed}: {e}")
            traceback.print_exc()
            print("  ↳ 继续执行下一所学校…")

    print(f"\n\n{'='*62}")
    print(f"全部完成  {_ts()}")
    print(f"{'='*62}")
    for s, r in results.items():
        mark = "✓" if r.startswith("OK") else "✗"
        print(f"  {mark}  {s:<22}  {r}")


if __name__ == "__main__":
    main()
