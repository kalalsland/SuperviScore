# -*- coding: utf-8 -*-
"""Google Scholar（best-effort）：取作者的引用量 / h-index / 代表作。

国内直连 scholar.google.com 常被墙（SSL EOF），故本模块统一走 VPN 代理
（utils.http_get_proxied，代理地址取 config.SCHOLAR_PROXY 或系统代理）。

实测：从代理出口 IP 访问 Scholar 的「作者搜索」接口会被导向 Google 登录墙，
但「作者主页」(citations?user=ID) 可正常访问。因此发现作者 user_id 改用
DuckDuckGo HTML 搜索（对脚本友好、无需登录），拿到 ID 后直接抓主页。

任何失败都返回空结果，绝不抛异常阻断主流程（由 paper_client 决定降级）。
"""
from __future__ import annotations
import re
import html as htmllib
from core.utils import http_get_proxied, http_post_proxied, log, polite_sleep
import config

DDG = "https://html.duckduckgo.com/html/"

# --- 熔断器：连不通（被墙/代理失效）时，几次失败后整轮放弃，不再逐人重试 ---
_FAIL_STREAK = 0
_DISABLED = False
_FAIL_LIMIT = 4        # 连续失败达到此数 → 本轮停用 Scholar


def _note_failure():
    global _FAIL_STREAK, _DISABLED
    _FAIL_STREAK += 1
    if _FAIL_STREAK >= _FAIL_LIMIT and not _DISABLED:
        _DISABLED = True
        log(f"[scholar] 连续 {_FAIL_STREAK} 次失败，本轮停用 Scholar（自动降级到 DBLP/arXiv）")


def _note_success():
    global _FAIL_STREAK
    _FAIL_STREAK = 0


def _clean(s: str) -> str:
    return htmllib.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _discover_user_id(name_en: str, affiliation_hint: str = "") -> str:
    """用 DuckDuckGo 搜 “name affiliation google scholar”，抽第一个 citations?user=ID。"""
    q = f"{name_en} {affiliation_hint} google scholar".strip()
    try:
        resp = http_post_proxied(DDG, data={"q": q},
                                 headers={"Referer": "https://html.duckduckgo.com/",
                                          "Accept": "text/html,application/xhtml+xml"},
                                 timeout=config.HTTP_TIMEOUT, retries=2, backoff=3.0)
        h = resp.text
        _note_success()
    except Exception as e:
        _note_failure()
        log(f"[scholar] DDG 搜索失败(降级) {q}: {e}")
        return ""
    # DDG 把外链编码进 uddg=...；user 参数可能是 user=ID 或 user%3DID
    m = re.search(r"scholar\.google\.[^\"'&<> ]*?user(?:=|%3[dD])([A-Za-z0-9_-]{8,})", h)
    if m:
        return m.group(1)
    # 退一步：整页找任意 citations?user=
    m = re.search(r"citations%3Fuser%3D([A-Za-z0-9_-]{8,})", h) or \
        re.search(r"citations\?user=([A-Za-z0-9_-]{8,})", h)
    return m.group(1) if m else ""


def find_author(name_en: str, affiliation_hint: str = "") -> dict:
    """发现作者并取其主页指标，返回 {scholar_url, citations, h_index, ...} 或 {}。"""
    if _DISABLED:
        return {}
    user_id = _discover_user_id(name_en, affiliation_hint)
    if not user_id:
        return {}
    polite_sleep(getattr(config, "SCHOLAR_SLEEP", 3.0))
    prof = fetch_author_profile(user_id)
    if prof and not _profile_matches(prof, name_en, affiliation_hint):
        # 同名误匹配（DDG 模糊命中别人）→ 宁缺毋滥，丢弃，让 DBLP/arXiv 兜底
        log(f"[scholar] 命中疑似同名他人，丢弃：查「{name_en} / {affiliation_hint}」"
            f"得「{prof.get('name','')} / {prof.get('affiliation','')}」")
        return {}
    return prof


def _profile_matches(prof: dict, name_en: str, affiliation_hint: str) -> bool:
    """校验 Scholar 主页是否确为目标人。

    以「姓名词覆盖」为硬条件（能挡住 DDG 模糊命中的纯同名他人），单位仅作软信号：
    Scholar 主页单位常写缩写（SJTU/MIT…），与 affiliation 关键词字面不一致是常态，
    故单位不一致不否决——最终身份核验交给 analyzer 用全量上下文判定。
    """
    pname = (prof.get("name") or "").lower()
    qwords = [w for w in re.split(r"\s+", name_en.lower().strip()) if w]
    # 姓名：查询里的每个词都应出现在主页姓名中（容忍顺序/大小写）
    return bool(qwords) and all(w in pname for w in qwords)


def fetch_author_profile(user_id: str) -> dict:
    """按 user_id 抓主页（走代理）：引用量、h-index、代表作（按被引降序）。"""
    if _DISABLED:
        return {}
    url = (f"https://scholar.google.com/citations?user={user_id}"
           f"&hl=en&view_op=list_works&sortby=cited-by&pagesize=10")
    try:
        resp = http_get_proxied(url, timeout=config.HTTP_TIMEOUT, retries=2, backoff=3.0)
        h = resp.text
        _note_success()
    except Exception as e:
        _note_failure()
        log(f"[scholar] 主页抓取失败(降级) {user_id}: {e}")
        return {}
    if "gsc_prf_in" not in h:        # 被导向登录/同意页等 → 视为失败
        _note_failure()
        return {}

    out = {"scholar_url": f"https://scholar.google.com/citations?user={user_id}"}
    # 引用量 / h-index：表格 gsc_rsb_std（第1行总引用，h-index 在第3个数）
    stats = re.findall(r'<td class="gsc_rsb_std">(\d+)</td>', h)
    if stats:
        try:
            out["citations"] = int(stats[0])
            if len(stats) >= 3:
                out["h_index"] = int(stats[2])
        except ValueError:
            pass
    mn = re.search(r'<div id="gsc_prf_in">([^<]+)</div>', h)
    if mn:
        out["name"] = _clean(mn.group(1))
    ma = re.search(r'class="gsc_prf_il">([^<]+)</div>', h)
    if ma:
        out["affiliation"] = _clean(ma.group(1))
    # 代表作（已按被引降序）：标题在 gsc_a_at
    titles = re.findall(r'class="gsc_a_at"[^>]*>([^<]+)</a>', h)
    out["representative_works"] = [_clean(t) for t in titles[:5]]
    return out
