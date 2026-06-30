# -*- coding: utf-8 -*-
"""论文检索门面：DBLP 取列表（消歧）→ arXiv 按标题补摘要。

对外只暴露 recent_papers()，内核其余部分不关心 DBLP/arXiv 细节。
"""
from __future__ import annotations
import re
from core.models import Paper
from core.utils import log, Cache
from core import dblp_client, arxiv_client, scholar_client, github_client
from core.pinyin_names import dblp_query_variants
import config


def _surname_pinyin_variants(pinyin: str) -> list[str]:
    """从连写拼音生成 DBLP 英文检索式候选（名 姓 / 姓 名 / 整串）。"""
    return dblp_query_variants(pinyin)


def _author_position(target_aliases: list[str], authors: list[str]) -> str:
    """判断目标作者在合作者列表中的位置：first/last/middle/unknown。"""
    if not authors:
        return "unknown"
    norm = lambda s: re.sub(r"[^a-z]", "", (s or "").lower())
    targets = {norm(a) for a in target_aliases if a}
    for idx, a in enumerate(authors):
        na = norm(a)
        if na in targets or any(t and (t in na or na in t) for t in targets):
            if idx == 0:
                return "first"
            if idx == len(authors) - 1:
                return "last"
            return "middle"
    return "unknown"


def _aff_matches_school(affiliations: list[str]) -> bool:
    kws = [k.lower() for k in getattr(config, "SCHOOL_DBLP_AFFILIATION", [])]
    blob = " ".join(affiliations).lower()
    return any(k in blob for k in kws)


def _resolve_person(candidates: list[dict]) -> tuple[str, list[str], float]:
    """从 DBLP 候选作者解析出最可能是本校该老师的具体 pid。
    用 affiliation note 与本校关键词匹配做消歧（最可靠）。
    返回 (pid, aliases, confidence)。
    """
    if not candidates:
        return "", [], 0.0

    school_matched = []     # (pid, aliases, affs)
    fallback = []
    seen_pids = set()
    for cand in candidates:
        pid0 = cand.get("pid", "")
        # 归一到消歧根（去掉 -N 后缀）避免对同一人重复展开
        root_pid = re.sub(r"-\d+$", "", pid0)
        if root_pid in seen_pids:
            continue
        seen_pids.add(root_pid)
        aliases = [cand.get("author", "")] + cand.get("aliases", [])
        # 展开消歧节点为具体人
        for person in dblp_client.list_homonyms(pid0):
            pid = person["pid"]
            affs = person["affiliations"]
            entry = (pid, aliases, affs)
            if _aff_matches_school(affs):
                return pid, aliases, 0.95   # 命中本校 → 立即返回
            fallback.append(entry)
    # 无 affiliation 命中：退回检索分最高者，低置信，交 analyzer 核验
    if fallback:
        pid, aliases, _ = fallback[0]
        return pid, aliases, 0.35
    return "", [], 0.0


def recent_papers(cn_name: str, pinyin: str, institute_hint: str,
                  cache: Cache | None = None) -> dict:
    """返回 {
        'papers': list[Paper],   # 最近 N 篇（已尽量补摘要）
        'identity_confidence': float,
        'dblp_url': str,
        'source': 'dblp'|'arxiv'|'none',
        'aliases': [...],
    }
    """
    ckey = f"{cn_name}|{pinyin}|{institute_hint}"
    if cache:
        cached = cache.get("papers", ckey)
        if cached is not None:
            cached["papers"] = [Paper(**p) for p in cached["papers"]]
            return cached

    result = {"papers": [], "identity_confidence": 0.0,
              "dblp_url": "", "source": "none", "aliases": []}

    # 1) DBLP 作者消歧（依次尝试 名姓/姓名/整串，第一个有候选的胜出）
    candidates = []
    variants = _surname_pinyin_variants(pinyin)
    for query in variants:
        candidates = dblp_client.search_author_pids(query)
        if candidates:
            break
    pid, aliases, conf = _resolve_person(candidates)
    result["aliases"] = aliases
    result["identity_confidence"] = conf

    papers: list[Paper] = []
    if pid:
        result["dblp_url"] = f"https://dblp.org/pid/{pid}.html"
        pubs = dblp_client.fetch_publications_by_pid(pid, limit=30)
        # 按年份倒序，取最近 N
        pubs.sort(key=lambda x: (x.get("year") or 0), reverse=True)
        for pub in pubs[:config.RECENT_PAPERS]:
            pos = _author_position(aliases, pub["authors"])
            abstract = arxiv_client.abstract_by_title(pub["title"])
            papers.append(Paper(
                title=pub["title"], year=pub["year"], venue=pub["venue"],
                authors=pub["authors"], abstract=abstract,
                url=pub["url"], author_position=pos,
            ))
        if papers:
            result["source"] = "dblp"

    # 2) DBLP 无果 → arXiv 作者兜底（用第一个英文检索式：名 姓）
    if not papers:
        ax_query = variants[0]
        ax = arxiv_client.recent_by_author(ax_query, max_results=config.RECENT_PAPERS)
        for pub in ax:
            pos = _author_position([ax_query], pub["authors"])
            papers.append(Paper(
                title=pub["title"], year=pub["year"], venue="arXiv",
                authors=pub["authors"], abstract=pub["abstract"],
                url=pub["url"], author_position=pos,
            ))
        if papers:
            result["source"] = "arxiv"
            result["identity_confidence"] = min(result["identity_confidence"], 0.4)

    result["papers"] = papers
    if not papers:
        log(f"[paper] 未找到论文佐证: {cn_name}")

    # 写缓存（Paper → dict）
    if cache:
        to_store = dict(result)
        to_store["papers"] = [p.__dict__ for p in papers]
        cache.set("papers", ckey, to_store)
    return result


def enrich_impact(cn_name: str, pinyin: str, institute_hint: str,
                  homepage: str = "", bio: str = "",
                  cache: Cache | None = None) -> dict:
    """best-effort 取学术影响力：Google Scholar(引用量/h-index/代表作) + GitHub(star/代表仓库)。
    任何源失败都安静降级，返回的 dict 里对应字段为 None/空。
    返回 {
      'citations', 'h_index', 'scholar_url', 'scholar_works':[...],
      'github_url', 'github_stars', 'github_repos':[(name,star,desc)],
      'representative_works':[...],   # 综合代表作（scholar 优先, github 补）
      'impact_source': 'scholar+github'|'scholar'|'github'|'none',
    }
    """
    ckey = f"impact|{cn_name}|{pinyin}|{institute_hint}|{homepage}"
    if cache:
        cached = cache.get("impact", ckey)
        if cached is not None:
            return cached

    out = {"citations": None, "h_index": None, "scholar_url": "", "scholar_works": [],
           "github_url": "", "github_stars": None, "github_repos": [],
           "representative_works": [], "impact_source": "none"}

    aff_hint = (getattr(config, "SCHOOL_DBLP_AFFILIATION", []) or [""])[0]
    name_en = dblp_query_variants(pinyin)[0]   # "名 姓"

    # 1) Google Scholar（best-effort）
    sources = []
    if getattr(config, "USE_SCHOLAR", True):
        try:
            sch = scholar_client.find_author(name_en, aff_hint)
        except Exception as e:
            log(f"[impact] scholar 异常(降级) {cn_name}: {e}")
            sch = {}
        if sch:
            out["citations"] = sch.get("citations")
            out["h_index"] = sch.get("h_index")
            out["scholar_url"] = sch.get("scholar_url", "")
            out["scholar_works"] = sch.get("representative_works", [])
            if out["scholar_url"]:
                sources.append("scholar")

    # 2) GitHub（仅当主页/简介里出现 github 链接，避免同名误判）
    if getattr(config, "USE_GITHUB", True):
        try:
            gh = github_client.find_profile(name_en, homepage, bio)
        except Exception as e:
            log(f"[impact] github 异常(降级) {cn_name}: {e}")
            gh = {}
        if gh:
            out["github_url"] = gh.get("github_url", "")
            out["github_stars"] = gh.get("stars")
            out["github_repos"] = gh.get("repos", [])
            # 额外保存 profile 主页信息
            out["github_bio"] = gh.get("bio", "")
            out["github_website"] = gh.get("website", "")
            out["github_pinned"] = gh.get("pinned", [])
            if out["github_url"]:
                sources.append("github")

    # 综合代表作：Scholar 高被引优先，GitHub pinned 项目补充（比 star 排序更能代表本人工作）
    reps = list(out["scholar_works"])
    pinned = out.get("github_pinned", [])
    if pinned:
        for repo_name in pinned[:3]:
            reps.append(f"{repo_name}（GitHub Pinned）")
    elif out.get("github_repos"):
        for name, star, _desc in out["github_repos"][:2]:
            if star > 0:
                reps.append(f"{name}（GitHub ★{star}）")
    out["representative_works"] = reps[:6]
    out["impact_source"] = "+".join(sources) if sources else "none"

    if cache:
        cache.set("impact", ckey, out)
    return out
