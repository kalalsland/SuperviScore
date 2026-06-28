# -*- coding: utf-8 -*-
"""输出：排序 CSV（全部） + Top N Markdown 详情 + 套磁信草稿。"""
from __future__ import annotations
import os
import csv
from core.models import Teacher
from core.utils import log
from core import llm_client
import config


def _flags_str(t: Teacher) -> str:
    return " / ".join(t.score.flags) if t.score and t.score.flags else ""


def write_csv(teachers: list[Teacher], out_dir: str):
    path = os.path.join(out_dir, "推荐名单.csv")
    # utf-8-sig 让 Excel 正确识别中文
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["排名", "推荐分", "姓名", "职称", "博导(启发式)", "方向匹配分",
                    "引用量", "h-index", "代表作", "研究所", "细化方向",
                    "套磁切入建议", "关键标记/嫌疑",
                    "个人主页", "Scholar", "GitHub", "DBLP", "详情页URL"])
        for i, t in enumerate(teachers, 1):
            sc = t.score
            anal = t.analysis or {}
            tags = "、".join((anal.get("refined_directions") or {}).get("tags", []))
            strat = (anal.get("approach_strategy") or "")
            reps = "；".join(t.representative_works[:3]) if t.representative_works else ""
            w.writerow([
                i,
                sc.final_score if sc else 0,
                t.name,
                t.title,
                "是" if (sc and sc.is_phd_advisor_guess) else "否(待核实)",
                sc.match_score if sc else 0,
                t.citations if t.citations is not None else "",
                t.h_index if t.h_index is not None else "",
                reps,
                t.institute,
                tags,
                strat,
                _flags_str(t),
                t.homepage,
                t.scholar_url,
                t.github_url,
                t.dblp_url,
                t.detail_url,
            ])
    log(f"[report] 已写出 CSV: {path}（{len(teachers)} 人）")
    return path


LETTER_SYS = """你是套磁邮件写作高手。为一位直博申请者撰写一封发给目标导师的中文套磁邮件草稿。
要求：真诚、具体、不浮夸；开头点明申请直博意向；中间用1-2个"申请人经历 × 导师近期工作"的
具体结合点证明匹配度（必须基于给定信息，不得编造导师论文）；结尾礼貌请求进一步交流。
篇幅 250-400 字。只输出邮件正文，不要解释。"""


def _letter_user(t: Teacher, profile: dict) -> str:
    anal = t.analysis or {}
    papers = "\n".join(
        f"- [{p.year}] {p.title}" + (f"（{p.abstract[:120]}）" if p.abstract else "")
        for p in t.papers[:5]) or "(无)"
    return f"""# 目标导师
姓名: {t.name}（{t.title}，{t.institute}）
细化方向: {'、'.join((anal.get('refined_directions') or {}).get('tags', []))}
近期论文:
{papers}
套磁切入建议: {anal.get('approach_strategy', '')}

# 申请人（直博）
姓名: {profile.get('name', '')}
方向: {'、'.join(profile.get('research_areas', []))}
代表成果: {'；'.join(profile.get('achievements', [])[:4])}
卖点: {profile.get('highlights', '')[:300]}
可对接点: {'；'.join((anal.get('match_with_user') or {}).get('overlap_points', []))}

请写出套磁邮件正文。"""


def write_detail(t: Teacher, rank: int, profile: dict, detail_dir: str, gen_letter: bool = True):
    anal = t.analysis or {}
    sc = t.score
    rd = anal.get("refined_directions") or {}
    mw = anal.get("match_with_user") or {}
    idm = anal.get("identity_match") or {}

    lines = []
    lines.append(f"# 第 {rank} 名 · {t.name}（推荐分 {sc.final_score if sc else 0}）\n")
    lines.append(f"- **职称**：{t.title}　|　**研究所**：{t.institute}")
    lines.append(f"- **博导(职称启发式)**：{'是' if sc and sc.is_phd_advisor_guess else '否 ⚠待核实'}")
    impact = []
    if t.citations is not None:
        impact.append(f"引用 {t.citations}")
    if t.h_index is not None:
        impact.append(f"h-index {t.h_index}")
    if t.github_stars:
        impact.append(f"GitHub ★{t.github_stars}")
    if impact:
        lines.append(f"- **学术影响力**：{'　|　'.join(impact)}")
    if t.representative_works:
        lines.append(f"- **代表作**：{'；'.join(t.representative_works[:5])}")
    lines.append(f"- **个人主页**：{t.homepage or '(无)'}")
    if t.scholar_url:
        lines.append(f"- **Google Scholar**：{t.scholar_url}")
    if t.github_url:
        lines.append(f"- **GitHub**：{t.github_url}")
    lines.append(f"- **DBLP**：{t.dblp_url or '(无)'}")
    lines.append(f"- **个人页**：{t.detail_url}\n")

    lines.append("## 研究方向（LLM 细化）")
    lines.append(f"{rd.get('summary', '')}")
    if rd.get("tags"):
        lines.append("标签：" + "、".join(rd["tags"]))
    lines.append("")

    lines.append(f"## 方向匹配（{sc.match_score if sc else 0}/100）")
    lines.append(mw.get("reason", ""))
    for op in mw.get("overlap_points", []):
        lines.append(f"- {op}")
    lines.append("")

    lines.append("## 打分明细")
    if sc:
        for k, v in sc.breakdown.items():
            lines.append(f"- {k}: {v}")
        if sc.flags:
            lines.append("\n**标记/嫌疑：**")
            for fl in sc.flags:
                lines.append(f"- {fl}")
    lines.append("")

    lines.append("## 近期论文")
    if t.papers:
        for p in t.papers:
            pos = {"first": "一作", "last": "末位(通讯)", "middle": "中间", "unknown": "?"}.get(
                p.author_position, "?")
            lines.append(f"- **[{p.year}] [{p.venue}]** 署名:{pos} — {p.title}")
            if p.abstract:
                lines.append(f"  > {p.abstract[:300]}")
    else:
        lines.append("(未检索到论文佐证)")
    lines.append("")

    lines.append(f"## 身份核验")
    lines.append(f"是否本人: {idm.get('is_same_person')}　置信度: {idm.get('confidence')}")
    lines.append(f"{idm.get('reason', '')}\n")

    lines.append("## 套磁策略")
    lines.append(anal.get("approach_strategy", "") + "\n")

    if gen_letter:
        lines.append("## 套磁邮件草稿")
        try:
            letter = llm_client.chat(LETTER_SYS, _letter_user(t, profile),
                                     temperature=0.5, max_tokens=900)
        except Exception as e:
            letter = f"(套磁信生成失败: {e})"
        lines.append(letter)

    safe_name = t.name.replace("/", "_").replace("\\", "_")
    path = os.path.join(detail_dir, f"{rank:02d}_{safe_name}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def write_all(teachers: list[Teacher], profile: dict, out_dir: str):
    """teachers 已按推荐分倒序。
    - CSV：全部老师
    - 详情 Markdown：综合分 Top N 详情
    - 套磁信：只给「综合分前 K」∪「方向匹配分前 K」生成（K=config.TOP_N_LETTER）
      —— 既覆盖最可能上岸的，也不漏掉方向最契合的。
    """
    os.makedirs(out_dir, exist_ok=True)
    write_csv(teachers, out_dir)

    top_n = config.TOP_N_DETAIL
    k = getattr(config, "TOP_N_LETTER", 5)

    # 选出要写套磁信的老师：综合前 k ∪ 匹配前 k
    by_overall = teachers[:k]
    by_match = sorted(teachers, key=lambda x: (x.score.match_score if x.score else 0),
                      reverse=True)[:k]
    letter_set = set(id(t) for t in by_overall) | set(id(t) for t in by_match)
    log(f"[report] 套磁信对象：综合前{k} ∪ 匹配前{k} = {len(letter_set)} 人")

    detail_dir = os.path.join(out_dir, f"top{top_n}详情")
    os.makedirs(detail_dir, exist_ok=True)
    for i, t in enumerate(teachers[:top_n], 1):
        try:
            gen = id(t) in letter_set
            p = write_detail(t, i, profile, detail_dir, gen_letter=gen)
            tag = "详情+套磁信" if gen else "详情"
            log(f"[report] {tag} 第{i}名 {t.name} -> {os.path.basename(p)}")
        except Exception as e:
            log(f"[report] 详情写出失败 {t.name}: {e}")

    # 方向匹配前 k 若不在 top_n 详情内，也单独补一份带套磁信的详情
    extra_rank = top_n
    for t in by_match:
        if t in teachers[:top_n]:
            continue
        extra_rank += 1
        try:
            p = write_detail(t, extra_rank, profile, detail_dir, gen_letter=True)
            log(f"[report] 匹配高分补充 {t.name}（匹配{t.score.match_score if t.score else 0}）"
                f" -> {os.path.basename(p)}")
        except Exception as e:
            log(f"[report] 补充详情失败 {t.name}: {e}")

    log(f"[report] 详情已写入: {detail_dir}")
