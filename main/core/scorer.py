# -*- coding: utf-8 -*-
"""打分引擎（纯函数，可单测）。

最终分 = (BASE + Σ加分 − Σ减分) × 活跃度指数系数。
所有权重在 config.WEIGHTS；嫌疑/标记写入 flags 交用户自核。
排序第一优先级：直博"套磁能上岸"。
"""
from __future__ import annotations
import math
from core.models import Teacher, ScoreResult
import config

W = config.WEIGHTS


def _guess_is_advisor(title: str) -> bool:
    """职称启发式：是否大概率博导。"""
    if not title:
        return True   # 未知职称不卡人
    for kw in config.NON_ADVISOR_TITLES:
        if kw in title:
            # 但"副教授/特别研究员"虽含"研究员"也算 PI，靠下面的 ADVISOR 兜
            if not any(a in title for a in config.ADVISOR_TITLES + config.JUNIOR_PI_TITLES):
                return False
    return True


def _consecutive_first_author(papers) -> int:
    """从最近往前数，本人连续一作的最大长度。"""
    streak = 0
    for p in papers:   # papers 已按时间倒序
        if p.author_position == "first":
            streak += 1
        else:
            break
    return streak


def _first_author_count(papers) -> int:
    return sum(1 for p in papers if p.author_position == "first")


def _latest_year(papers):
    years = [p.year for p in papers if p.year]
    return max(years) if years else None


def _is_too_senior(teacher: Teacher, analysis: dict) -> bool:
    bio = teacher.bio or ""
    if any(kw in bio for kw in config.TOO_SENIOR_KEYWORDS):
        return True
    sen = (analysis.get("seniority") or {})
    return sen.get("level") == "star" or bool(sen.get("is_too_hard_to_get_in"))


def _is_junior_pi(teacher: Teacher, analysis: dict) -> bool:
    if any(j in (teacher.title or "") for j in config.JUNIOR_PI_TITLES):
        return True
    return (analysis.get("seniority") or {}).get("level") == "junior"


def score(teacher: Teacher, analysis: dict, current_year: int = 2026) -> ScoreResult:
    breakdown: dict[str, float] = {}
    flags: list[str] = []

    base = config.BASE_SCORE
    breakdown["基础分"] = base
    total = base

    # 身份判定：LLM 是否认为检索到的论文确为本人。
    idm = analysis.get("identity_match") or {}
    id_conf = float(idm.get("confidence", teacher.identity_confidence) or 0)
    is_same = idm.get("is_same_person", True)
    # is_same_person=False 且高置信 → 论文是别人的，不能用于一作/活跃度判断
    papers_trustworthy = bool(teacher.papers) and not (is_same is False and id_conf >= 0.6)
    if teacher.papers and not papers_trustworthy:
        flags.append(f"⚠检索论文疑似误匹配(非本人,conf={id_conf:.2f})·已忽略论文相关评分·方向/活跃度待核实")

    # ---------- 加分：方向匹配（最重要）----------
    match = float((analysis.get("match_with_user") or {}).get("score", 0) or 0)
    # 身份低置信 → 匹配分打折（论文可能不是本人，方向判断不可靠）
    match_eff = match
    if id_conf < 0.5 and is_same is not False:
        match_eff = match * 0.7
        flags.append(f"⚠身份核验偏低(conf={id_conf:.2f})·论文是否本人待核实")
    add_match = match_eff * W["match_user"]
    breakdown["方向匹配"] = round(add_match, 2)
    total += add_match

    # ---------- 减分：非博导（职称启发式）----------
    is_advisor = _guess_is_advisor(teacher.title)
    if not is_advisor:
        breakdown["非博导(职称启发式)"] = -W["not_advisor_penalty"]
        total -= W["not_advisor_penalty"]
        flags.append(f"⚠职称『{teacher.title}』疑非博导·硬性条件待核实")

    # ---------- 减分：抢学生嫌疑（连续一作）---------- 仅当论文可信
    if papers_trustworthy:
        streak = _consecutive_first_author(teacher.papers)
        fa_count = _first_author_count(teacher.papers)
        if streak >= 2:
            pen = W["grab_student_penalty"] * (1 + 0.3 * (streak - 2))  # 越长扣越多
            breakdown["抢学生嫌疑(连续一作)"] = -round(pen, 2)
            total -= pen
            flags.append(f"⚠近作连续{streak}篇本人一作·疑亲自下场/抢学生")
        elif fa_count >= 1 and fa_count == len(teacher.papers):
            # 全部一作但不连续（数据少）也提示
            flags.append(f"近作{fa_count}篇均一作·留意是否独立小作坊")

    # ---------- 减分：方向频繁切换 ----------
    drift = (analysis.get("direction_drift") or {}).get("level", "low")
    if drift == "high":
        breakdown["方向频繁切换"] = -W["direction_drift_penalty"]
        total -= W["direction_drift_penalty"]
        flags.append("⚠研究方向近年频繁切换")

    # ---------- 减分：太牛难入 ----------
    if _is_too_senior(teacher, analysis):
        breakdown["太牛难入"] = -W["too_senior_penalty"]
        total -= W["too_senior_penalty"]
        flags.append("⚠顶尖大牛·直博套磁上岸难度高")

    # ---------- 加分：新晋 PI（直博更易上岸）----------
    if _is_junior_pi(teacher, analysis) and is_advisor:
        breakdown["新晋PI(易上岸)"] = W["junior_pi_bonus"]
        total += W["junior_pi_bonus"]
        flags.append("✓新晋PI/青年导师·直博相对好进")

    # ---------- 加分：资历适中好接触 ----------
    sen_level = (analysis.get("seniority") or {}).get("level", "mid")
    if sen_level == "mid" and not _is_too_senior(teacher, analysis):
        breakdown["资历适中"] = W["approachable_bonus"]
        total += W["approachable_bonus"]

    # ---------- 减分：论文无佐证（含误匹配被忽略的情况）----------
    if not papers_trustworthy:
        breakdown["论文无佐证"] = -W["no_paper_penalty"]
        total -= W["no_paper_penalty"]
        if not teacher.papers:
            flags.append("⚠未检索到论文佐证·活跃度/身份待核实")

    # ---------- 乘性：活跃度指数衰减（远离科研一线）---------- 仅当论文可信
    activity_factor = 1.0
    if papers_trustworthy:
        latest = _latest_year(teacher.papers)
        if latest:
            gap = current_year - latest
            over = max(0, gap - W["inactive_grace_years"])
            if over > 0:
                activity_factor = math.exp(-W["inactive_decay_lambda"] * over)
                flags.append(f"⚠最新论文在{latest}年·已{gap}年·疑远离科研一线(×{activity_factor:.2f})")
        else:
            activity_factor = 0.6   # 完全无年份信息，温和打折
    breakdown["活跃度系数"] = round(activity_factor, 3)

    final = max(0.0, total) * activity_factor

    return ScoreResult(
        final_score=round(final, 2),
        is_phd_advisor_guess=is_advisor,
        match_score=match,
        breakdown=breakdown,
        flags=flags,
    )
