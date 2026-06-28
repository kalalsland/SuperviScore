# -*- coding: utf-8 -*-
"""arXiv 检索：按论文标题补摘要；也支持按作者兜底搜索。

arXiv API: http://export.arxiv.org/api/query  （Atom XML）
"""
from __future__ import annotations
import re
import xml.etree.ElementTree as ET
from core.utils import http_get, log, polite_sleep
import config

API = "http://export.arxiv.org/api/query"
NS = {"a": "http://www.w3.org/2005/Atom"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def abstract_by_title(title: str) -> str:
    """按标题精确度匹配取摘要；匹配不上返回 ""。"""
    if not title:
        return ""
    q = re.sub(r"[^\w\s]", " ", title)
    q = re.sub(r"\s+", " ", q).strip()
    params = {"search_query": f'ti:"{q}"', "max_results": 3}
    try:
        resp = http_get(API, params=params, timeout=config.HTTP_TIMEOUT)
        root = ET.fromstring(resp.text)
    except Exception as e:
        log(f"[arxiv] 标题检索失败 {title[:40]}: {e}")
        polite_sleep(config.ARXIV_SLEEP)
        return ""
    target = _norm(title)
    best = ""
    for entry in root.findall("a:entry", NS):
        et = entry.find("a:title", NS)
        su = entry.find("a:summary", NS)
        if et is None or su is None:
            continue
        cand = _norm(et.text or "")
        summary = re.sub(r"\s+", " ", (su.text or "")).strip()
        # 标题高度重合才采纳（防张冠李戴）
        if cand == target or (target and (target in cand or cand in target)):
            best = summary
            break
        # 词重叠度兜底
        a, b = set(cand.split()), set(target.split())
        if a and b and len(a & b) / max(1, len(b)) >= 0.8:
            best = summary
            break
    polite_sleep(config.ARXIV_SLEEP)
    return best


def recent_by_author(author_en: str, max_results: int = 10) -> list[dict]:
    """按作者名兜底检索（DBLP 找不到时用）。返回 [{title,abstract,year,authors,url}]。"""
    if not author_en:
        return []
    params = {"search_query": f'au:"{author_en}"',
              "sortBy": "submittedDate", "sortOrder": "descending",
              "max_results": max_results}
    try:
        resp = http_get(API, params=params, timeout=config.HTTP_TIMEOUT)
        root = ET.fromstring(resp.text)
    except Exception as e:
        log(f"[arxiv] 作者检索失败 {author_en}: {e}")
        polite_sleep(config.ARXIV_SLEEP)
        return []
    papers = []
    for entry in root.findall("a:entry", NS):
        title = re.sub(r"\s+", " ", (entry.findtext("a:title", "", NS) or "")).strip()
        summary = re.sub(r"\s+", " ", (entry.findtext("a:summary", "", NS) or "")).strip()
        pub = entry.findtext("a:published", "", NS)
        year = None
        m = re.match(r"(\d{4})", pub or "")
        if m:
            year = int(m.group(1))
        authors = [a.findtext("a:name", "", NS) for a in entry.findall("a:author", NS)]
        url = entry.findtext("a:id", "", NS)
        papers.append({"title": title, "abstract": summary, "year": year,
                       "authors": authors, "url": url})
    polite_sleep(config.ARXIV_SLEEP)
    return papers
