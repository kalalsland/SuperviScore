# -*- coding: utf-8 -*-
"""scorer 规则单测（不联网、不调 LLM）。运行: python -m tests.test_scorer"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.utils import force_utf8  # 强制 UTF-8 输出，避免 Windows GBK 崩溃
force_utf8()
from core.models import Teacher, Paper
from core import scorer


def mk_paper(year, pos):
    return Paper(title=f"T{year}", year=year, venue="X", authors=["A"], author_position=pos)


def analysis(match=70, drift="low", sen="mid", too_hard=False, id_conf=0.9,
             recruit=True, is_same=True):
    return {
        "identity_match": {"is_same_person": is_same, "confidence": id_conf, "reason": ""},
        "refined_directions": {"tags": ["a"], "summary": ""},
        "match_with_user": {"score": match, "overlap_points": [], "reason": ""},
        "direction_drift": {"level": drift, "reason": ""},
        "seniority": {"level": sen, "is_too_hard_to_get_in": too_hard, "reason": ""},
        "is_recruiting_phd_guess": recruit,
    }


def approx(a, b, tol=0.5):
    return abs(a - b) <= tol


def main():
    fails = []

    # 1. 连续2篇一作 → 抢学生重扣
    t = Teacher("张三", "u", title="教授",
                papers=[mk_paper(2025, "first"), mk_paper(2024, "first"),
                        mk_paper(2023, "last")])
    r = scorer.score(t, analysis(), current_year=2026)
    if "抢学生嫌疑(连续一作)" not in r.breakdown:
        fails.append("连续2篇一作未触发抢学生扣分")

    # 2. 偶发一作（不连续）→ 不重扣
    t2 = Teacher("李四", "u", title="教授",
                 papers=[mk_paper(2025, "last"), mk_paper(2024, "first"),
                         mk_paper(2023, "last")])
    r2 = scorer.score(t2, analysis(), current_year=2026)
    if "抢学生嫌疑(连续一作)" in r2.breakdown:
        fails.append("偶发一作被误判为抢学生")

    # 3. 最新论文 5 年前 → 指数衰减明显 (<1)
    t3 = Teacher("王五", "u", title="教授",
                 papers=[mk_paper(2020, "last")])
    r3 = scorer.score(t3, analysis(), current_year=2026)
    if r3.breakdown["活跃度系数"] >= 0.5:
        fails.append(f"5年前论文衰减不足: {r3.breakdown['活跃度系数']}")

    # 4. 近一年论文 → 活跃度系数 = 1
    t4 = Teacher("赵六", "u", title="教授", papers=[mk_paper(2025, "last")])
    r4 = scorer.score(t4, analysis(), current_year=2026)
    if not approx(r4.breakdown["活跃度系数"], 1.0):
        fails.append(f"近一年论文不应衰减: {r4.breakdown['活跃度系数']}")

    # 5. 讲师/实验师 → 非博导标记 + 大扣
    t5 = Teacher("孙七", "u", title="实验师", papers=[mk_paper(2025, "last")])
    r5 = scorer.score(t5, analysis(), current_year=2026)
    if r5.is_phd_advisor_guess or "非博导(职称启发式)" not in r5.breakdown:
        fails.append("实验师未被判为非博导")

    # 6. 太牛 star → 难入扣分
    t6 = Teacher("周八", "u", title="特聘教授",
                 bio="国家杰出青年基金获得者、ACM Fellow",
                 papers=[mk_paper(2025, "last")])
    r6 = scorer.score(t6, analysis(sen="star", too_hard=True), current_year=2026)
    if "太牛难入" not in r6.breakdown:
        fails.append("大牛未触发难入扣分")

    # 7. 新晋 PI → 加分
    t7 = Teacher("吴九", "u", title="副教授", papers=[mk_paper(2025, "first")])
    r7 = scorer.score(t7, analysis(sen="junior"), current_year=2026)
    if "新晋PI(易上岸)" not in r7.breakdown:
        fails.append("新晋PI未加分")

    # 8. 高匹配 vs 低匹配 → 分数单调
    th = Teacher("高匹配", "u", title="教授", papers=[mk_paper(2025, "last")])
    tl = Teacher("低匹配", "u", title="教授", papers=[mk_paper(2025, "last")])
    rh = scorer.score(th, analysis(match=90), 2026)
    rl = scorer.score(tl, analysis(match=20), 2026)
    if not (rh.final_score > rl.final_score):
        fails.append("匹配分高者总分未更高")

    # 9. 身份低置信 → 匹配分打折 + flag
    t9 = Teacher("同名", "u", title="教授", papers=[mk_paper(2025, "last")])
    r9 = scorer.score(t9, analysis(match=80, id_conf=0.3), 2026)
    if not any("身份核验偏低" in f for f in r9.flags):
        fails.append("身份低置信未标记")

    # 10. 无论文 → 扣分 + flag
    t10 = Teacher("查无", "u", title="教授", papers=[])
    r10 = scorer.score(t10, analysis(), 2026)
    if "论文无佐证" not in r10.breakdown:
        fails.append("无论文未扣分")

    # 11. 论文被判误匹配(非本人,高置信) → 忽略一作/活跃度扣分, 改记论文无佐证
    t11 = Teacher("误匹配", "u", title="教授",
                  papers=[mk_paper(2018, "first"), mk_paper(2018, "first")])
    r11 = scorer.score(t11, analysis(is_same=False, id_conf=0.9), 2026)
    if "抢学生嫌疑(连续一作)" in r11.breakdown:
        fails.append("误匹配论文不应触发抢学生扣分")
    if r11.breakdown["活跃度系数"] != 1.0:
        fails.append("误匹配论文不应触发活跃度衰减")
    if "论文无佐证" not in r11.breakdown:
        fails.append("误匹配应记为论文无佐证")

    if fails:
        print("❌ 单测失败:")
        for f in fails:
            print("  -", f)
        sys.exit(1)
    print("✅ scorer 全部 11 项规则通过")


if __name__ == "__main__":
    main()
