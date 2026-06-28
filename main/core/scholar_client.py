# -*- coding: utf-8 -*-
"""Google Scholar（best-effort）：取作者的引用量 / h-index / 代表作。

Scholar 无官方 API 且反爬严格，本模块一律"尽力而为"：
任何失败都返回空结果，绝不抛异常阻断主流程（由 paper_client 决定降级）。
用户本机有代理时通常可用；Claude 沙箱内不可达会自动降级。
"""
from __future__ import annotations
import re
import html as htmllib
from core.utils import http_get, log, polite_sleep
import config

SEARCH = "https://scholar.google.com/citations"


def _clean(s: str) -> str:
    return htmllib.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def find_author(name_en: str, affiliation_hint: str = "") -> dict:
    """搜作者主页，返回 {scholar_url, citations, h_index, affiliation, name} 或 {}。"""
    q = f"{name_en} {affiliation_hint}".strip()
    params = {"view_op": "search_authors", "mauthors": q, "hl": "en"}
    try:
        resp = http_get(SEARCH, params=params, timeout=config.HTTP_TIMEOUT,
                        retries=2, backoff=4.0)
        h = resp.text
    except Exception as e:
        log(f"[scholar] 搜索失败(降级) {q}: {e}")
        return {}
    if "gs_ai_name" not in h and "user=" not in h:
        # 没有结果或被反爬挡住
        return {}
    # 第一个作者卡片
    m = re.search(r'href="/citations\?user=([\w-]+)[^"]*"', h)
    if not m:
        return {}
    user_id = m.group(1)
    polite_sleep(getattr(config, "SCHOLAR_SLEEP", 3.0))
    return fetch_author_profile(user_id)


def fetch_author_profile(user_id: str) -> dict:
    """按 user_id 抓主页：引用量、h-index、代表作（默认按被引降序，即代表作）。"""
    url = f"https://scholar.google.com/citations?user={user_id}&hl=en&view_op=list_works&pagesize=10"
    try:
        resp = http_get(url, timeout=config.HTTP_TIMEOUT, retries=2, backoff=4.0)
        h = resp.text
    except Exception as e:
        log(f"[scholar] 主页抓取失败(降级) {user_id}: {e}")
        return {}

    out = {"scholar_url": f"https://scholar.google.com/citations?user={user_id}"}
    # 引用量 / h-index：表格 gsc_rsb_std（第1行总引用，第2列近5年；h-index 在第2行）
    stats = re.findall(r'<td class="gsc_rsb_std">(\d+)</td>', h)
    if stats:
        try:
            out["citations"] = int(stats[0])
            if len(stats) >= 3:
                out["h_index"] = int(stats[2])
        except ValueError:
            pass
    # 姓名 / 单位
    mn = re.search(r'<div id="gsc_prf_in">([^<]+)</div>', h)
    if mn:
        out["name"] = _clean(mn.group(1))
    ma = re.search(r'class="gsc_prf_il">([^<]+)</div>', h)
    if ma:
        out["affiliation"] = _clean(ma.group(1))
    # 代表作（works 列表按 pubdate；标题在 gsc_a_at）
    titles = re.findall(r'class="gsc_a_at"[^>]*>([^<]+)</a>', h)
    out["representative_works"] = [_clean(t) for t in titles[:5]]
    return out
