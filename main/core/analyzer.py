# -*- coding: utf-8 -*-
"""每位老师一次结构化 LLM 调用：
身份核验 + 研究方向细化 + 与用户经历的匹配 + 方向漂移 + 资历评估 + 套磁要点。
"""
from __future__ import annotations
import json
from core.models import Teacher
from core.utils import Cache
from core import llm_client
import config

ANALYZE_SYS = """你是资深的研究生招生匹配与套磁策略顾问，服务对象是一位"直博"申请者。
你的判断将用于给导师排序，第一优先级是"套磁能否上岸"。请客观、谨慎，
对不确定的地方明确给出低置信度，不要编造。"""

ANALYZE_USER_TMPL = """# 申请人画像（直博申请者）
研究方向: {user_areas}
方法/技能: {user_skills}
代表成果: {user_achievements}
最想从事: {user_preferred}
卖点: {user_highlights}

# 待评估的导师
姓名: {name}
职称: {title}
所在研究所: {institute}
个人简介(官网): {bio}
官网列出的论文标题(部分): {listed_papers}

# 论文检索结果（DBLP优先, arXiv补摘要）
检索消歧置信度: {identity_conf}  (低于0.5说明不确定是否本人)
论文来源: {paper_source}
最近论文:
{papers_block}

# 请基于以上信息输出 JSON：
{{
  "identity_match": {{
    "is_same_person": true/false,
    "confidence": 0.0-1.0,
    "reason": "结合简介中的方向/机构与检索到论文的领域是否一致来判断, 一句话"
  }},
  "refined_directions": {{
    "tags": ["细化后的研究方向标签, 中文, 3-5个"],
    "summary": "一句话总结该导师当前主攻方向"
  }},
  "match_with_user": {{
    "score": 0-100,
    "overlap_points": ["申请人与导师可对接的具体结合点, 1-4条"],
    "reason": "为何给这个分数, 一句话"
  }},
  "direction_drift": {{
    "level": "low/medium/high",
    "reason": "近几篇方向是否频繁切换, 一句话"
  }},
  "seniority": {{
    "level": "junior/mid/senior/star",
    "is_too_hard_to_get_in": true/false,
    "reason": "院士/杰青/Fellow/大量顶会=star且难入; 新晋PI=junior更易上岸; 一句话"
  }},
  "is_recruiting_phd_guess": true/false,
  "approach_strategy": "针对该导师, 给这位直博申请者的一句话套磁切入建议"
}}"""


def _papers_block(t: Teacher) -> str:
    if not t.papers:
        return "(未检索到论文)"
    lines = []
    for i, p in enumerate(t.papers, 1):
        pos = {"first": "一作", "last": "末位(通讯)", "middle": "中间",
               "unknown": "?"}.get(p.author_position, "?")
        abs_part = f" 摘要: {p.abstract[:400]}" if p.abstract else ""
        lines.append(f"{i}. [{p.year}] [{p.venue}] 署名:{pos} {p.title}.{abs_part}")
    return "\n".join(lines)


PRESCREEN_SYS = "你是研究生导师方向匹配助手。根据导师简介判断与申请人的方向相关性，只输出一个 0-100 的整数，不要任何解释。"


def quick_screen(teacher: Teacher, user_profile: dict, cache: Cache = None) -> int:
    """轻量预筛：只看简介和官网论文标题，输出方向匹配分 0-100。
    调用量小（max_tokens=4），用于在 DBLP/Scholar/LLM 细化前快速过滤方向偏差大的导师。
    """
    ckey = f"prescreen|{teacher.name}|{teacher.detail_url}"
    if cache:
        cached = cache.get("prescreen", ckey)
        if cached is not None:
            return int(cached)

    user_areas = ", ".join(user_profile.get("research_areas", []))
    user_preferred = ", ".join(user_profile.get("preferred_directions", []))
    papers_hint = "; ".join(teacher.papers_listed[:5]) or "(无)"
    bio_hint = (teacher.bio or "")[:600]

    prompt = (f"申请人方向: {user_areas}；最感兴趣: {user_preferred}\n"
              f"导师简介: {bio_hint}\n"
              f"官网论文(部分): {papers_hint}\n"
              f"方向匹配分(0-100):")
    try:
        raw = llm_client.chat(PRESCREEN_SYS, prompt, max_tokens=4, temperature=0.0)
        score = int("".join(filter(str.isdigit, (raw or "").strip()))[:3] or "50")
        score = max(0, min(100, score))
    except Exception:
        score = 50   # 调用失败 → 保守保留

    if cache:
        cache.set("prescreen", ckey, score)
    return score


def analyze(teacher: Teacher, user_profile: dict, cache: Cache = None) -> dict:
    ckey = f"analyze|{teacher.name}|{teacher.detail_url}|{len(teacher.papers)}|{teacher.identity_confidence:.2f}"
    if cache:
        cached = cache.get("analysis", ckey)
        if cached is not None:
            return cached

    user = ANALYZE_USER_TMPL.format(
        user_areas=", ".join(user_profile.get("research_areas", [])),
        user_skills=", ".join(user_profile.get("methods_skills", []))[:600],
        user_achievements=", ".join(user_profile.get("achievements", []))[:600],
        user_preferred=", ".join(user_profile.get("preferred_directions", [])),
        user_highlights=(user_profile.get("highlights", "") or "")[:400],
        name=teacher.name,
        title=teacher.title,
        institute=teacher.institute,
        bio=(teacher.bio or "")[:1500],
        listed_papers="; ".join(teacher.papers_listed[:8]) or "(无)",
        identity_conf=round(teacher.identity_confidence, 2),
        paper_source=("DBLP" if teacher.papers and teacher.dblp_url else
                      ("arXiv" if teacher.papers else "无")),
        papers_block=_papers_block(teacher),
    )

    result = llm_client.chat_json(ANALYZE_SYS, user, max_tokens=1800)
    if result is None:
        result = {
            "identity_match": {"is_same_person": False, "confidence": 0.0,
                               "reason": "LLM 分析失败"},
            "refined_directions": {"tags": [], "summary": ""},
            "match_with_user": {"score": 0, "overlap_points": [], "reason": "分析失败"},
            "direction_drift": {"level": "medium", "reason": ""},
            "seniority": {"level": "mid", "is_too_hard_to_get_in": False, "reason": ""},
            "is_recruiting_phd_guess": True,
            "approach_strategy": "",
            "_failed": True,
        }
    if cache:
        cache.set("analysis", ckey, result)
    return result
