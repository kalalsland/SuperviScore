# -*- coding: utf-8 -*-
"""DBLP 检索：作者消歧（PID）+ 取最近论文（标题/年/会议/合作者）。

DBLP 无摘要 —— 摘要由 arxiv_client 按标题补。
"""
from __future__ import annotations
import re
import xml.etree.ElementTree as ET
from core.utils import http_get, log, polite_sleep
import config

AUTHOR_API = "https://dblp.org/search/author/api"
PUBL_API = "https://dblp.org/search/publ/api"
PID_XML = "https://dblp.org/pid/{pid}.xml"


def _as_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _dblp_get(url, **kw):
    """DBLP 专用 GET：更多重试 + 更长退避（限流较狠）。"""
    return http_get(url, timeout=config.HTTP_TIMEOUT,
                    retries=config.DBLP_RETRIES, backoff=config.DBLP_BACKOFF, **kw)


def pinyin_to_name(pinyin: str) -> str:
    """个人页 URL 里的拼音（如 chenhaibo / wangzhaoguo）→ DBLP 英文检索名。
    交大命名规律：姓+名连写、全小写、无分隔。无法准确切分姓名边界时直接整体检索，
    DBLP 的模糊匹配 + 后续机构消歧能兜住。
    """
    return pinyin.strip().lower()


def search_author_pids(query_name: str, max_hits: int = 8) -> list[dict]:
    """返回候选作者 [{author, pid, url, aliases:[...]}]。"""
    params = {"q": query_name, "format": "json", "h": max_hits}
    try:
        resp = _dblp_get(AUTHOR_API, params=params)
        data = resp.json()
    except Exception as e:
        log(f"[dblp] 作者检索失败 {query_name}: {e}")
        return []
    hits = _as_list(data.get("result", {}).get("hits", {}).get("hit"))
    out = []
    for hit in hits:
        info = hit.get("info", {})
        url = info.get("url", "")
        pid = ""
        m = re.search(r"/pid/([^/]+/[^/.]+)", url)
        if m:
            pid = m.group(1)
        aliases = _as_list(info.get("aliases", {}).get("alias"))
        aliases = [a.get("text") if isinstance(a, dict) else a for a in aliases]
        out.append({
            "author": info.get("author", ""),
            "pid": pid,
            "url": url,
            "aliases": aliases,
            "score": int(hit.get("@score", 0)),
        })
    polite_sleep(config.DBLP_SLEEP)
    return out


def fetch_publications_by_pid(pid: str, limit: int = 30) -> list[dict]:
    """取该 PID 的论文。使用 DBLP person 的 .xml feed（比 search publ 的 pid: 查询可靠）。
    若 pid 是消歧节点（如 31/6601），其 xml 里没有论文，需由上层换具体 homonym（31/6601-1）。
    """
    if not pid:
        return []
    try:
        resp = _dblp_get(PID_XML.format(pid=pid))
        root = ET.fromstring(resp.content)   # 用 bytes 让 ET 处理声明编码
    except Exception as e:
        log(f"[dblp] 论文 xml 抓取失败 pid={pid}: {e}")
        polite_sleep(config.DBLP_SLEEP)
        return []

    papers = []
    for r in root.findall("r"):
        for pub in list(r):           # inproceedings / article / ...
            title = (pub.findtext("title", "") or "").strip().rstrip(".")
            year = pub.findtext("year", "")
            try:
                year = int(year) if year else None
            except Exception:
                year = None
            authors = [a.text for a in pub.findall("author") if a.text]
            authors = [re.sub(r"\s+\d{4}$", "", a) for a in authors]   # 去同名后缀
            venue = (pub.findtext("booktitle", "") or pub.findtext("journal", "") or "").strip()
            ee = pub.findtext("ee", "") or ""
            papers.append({
                "title": title, "year": year, "venue": venue,
                "authors": authors, "url": ee,
            })
    polite_sleep(config.DBLP_SLEEP)
    return papers


def list_homonyms(pid: str) -> list[dict]:
    """对消歧节点，列出具体同名者 [{pid, affiliations:[...]}]（如 31/6601 → 31/6601-1 等）。
    若本身就是具体人（xml 里直接有论文），返回 [{pid, affiliations:[本人单位]}]。
    """
    if not pid:
        return []
    try:
        resp = _dblp_get(PID_XML.format(pid=pid))
        root = ET.fromstring(resp.content)
    except Exception as e:
        log(f"[dblp] homonym xml 抓取失败 pid={pid}: {e}")
        polite_sleep(config.DBLP_SLEEP)
        return [{"pid": pid, "affiliations": []}]

    polite_sleep(config.DBLP_SLEEP)
    # 具体人：xml 里有论文（<r>）
    if root.find("r") is not None:
        affs = [n.text for n in root.findall("person/note[@type='affiliation']") if n.text]
        return [{"pid": pid, "affiliations": affs}]

    # 消歧节点：解析 homonyms
    out = []
    for h in root.findall("homonyms/h"):
        person = h.find("person")
        if person is None:
            continue
        key = person.get("key", "")
        m = re.search(r"homepages/(.+)$", key)
        if not m:
            continue
        affs = [n.text for n in person.findall("note[@type='affiliation']") if n.text]
        out.append({"pid": m.group(1), "affiliations": affs})
    return out or [{"pid": pid, "affiliations": []}]


