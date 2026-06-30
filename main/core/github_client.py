# -*- coding: utf-8 -*-
"""GitHub（best-effort）：从教师主页/简介里找 GitHub 用户名，抓其 Profile 主页。

策略：
1. 从 homepage/bio 文本里直接抽 github.com/<username>（最可靠，无歧义）
2. 抓 https://github.com/<username> 的 HTML 主页（Profile 页），提取：
   - 用户 bio（自我介绍）
   - 个人网站链接（profile URL）
   - pinned 仓库名称（最多6个）
   - 总 public 仓库 star 数（通过 API）
3. 不做纯姓名搜索——同名风险太高，宁缺毋滥。

任何失败都返回空，绝不阻断主流程。
"""
from __future__ import annotations
import os
import re
import html as htmllib
from core.utils import http_get, http_get_proxied, log, polite_sleep
import config

API = "https://api.github.com"


def _headers():
    h = {"Accept": "application/vnd.github+json",
         "User-Agent": "Mozilla/5.0"}
    tok = os.environ.get("GITHUB_TOKEN", "")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _clean(s: str) -> str:
    return htmllib.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def extract_github_username(*texts: str) -> str:
    """从主页/简介文本里直接抽 github.com/用户名。"""
    blob = " ".join(t or "" for t in texts)
    m = re.search(r"github\.com/([A-Za-z0-9][\w-]{0,38})(?:[/?#]|$|\s|[\"'])", blob)
    if m:
        slug = m.group(1).lower()
        if slug not in ("about", "features", "topics", "orgs", "explore",
                        "marketplace", "sponsors", "login", "join"):
            return m.group(1)
    return ""


def fetch_user_profile(username: str) -> dict:
    """抓 github.com/<username> 的 Profile 主页 HTML + REST API user 端点。

    返回:
      {
        'github_url': str,
        'bio': str,              # 用户自我介绍
        'website': str,          # 个人网站链接（profile 里填的 URL）
        'pinned': [str],         # pinned 仓库/项目名称
        'stars': int,            # 所有公开仓库 star 总数
        'repos': [(name, stars, desc)],  # 前5高 star 仓库（用于 representative_works）
        'location': str,
        'company': str,
      }
    或 {} 表示失败。
    """
    if not username:
        return {}

    out: dict = {"github_url": f"https://github.com/{username}"}

    # --- 1. REST API：取 bio / website / company / location / public_repos ---
    try:
        resp = http_get(f"{API}/users/{username}", timeout=config.HTTP_TIMEOUT,
                        retries=2, backoff=4.0, headers=_headers())
        if resp.status_code == 200:
            u = resp.json()
            out["bio"] = (u.get("bio") or "").strip()
            out["website"] = (u.get("blog") or "").strip()
            out["location"] = (u.get("location") or "").strip()
            out["company"] = (u.get("company") or "").strip()
        elif resp.status_code == 404:
            log(f"[github] 用户不存在: {username}")
            return {}
    except Exception as e:
        log(f"[github] API 用户信息失败(降级) {username}: {e}")

    polite_sleep(getattr(config, "GITHUB_SLEEP", 1.0))

    # --- 2. REST API：取仓库列表，计算 star 总数 + top repos ---
    try:
        resp = http_get(f"{API}/users/{username}/repos",
                        params={"per_page": 100, "sort": "updated"},
                        timeout=config.HTTP_TIMEOUT, retries=2, backoff=4.0,
                        headers=_headers())
        if resp.status_code == 200:
            repos = resp.json()
            if isinstance(repos, list):
                own = [r for r in repos if not r.get("fork")]
                own.sort(key=lambda r: r.get("stargazers_count", 0), reverse=True)
                out["stars"] = sum(r.get("stargazers_count", 0) for r in own)
                out["repos"] = [
                    (r.get("name", ""),
                     r.get("stargazers_count", 0),
                     (r.get("description") or "")[:120])
                    for r in own[:5] if r.get("stargazers_count", 0) > 0
                ]
    except Exception as e:
        log(f"[github] 仓库列表失败(降级) {username}: {e}")

    polite_sleep(getattr(config, "GITHUB_SLEEP", 1.0))

    # --- 3. 抓 HTML 主页：取 pinned 项目（API 没有直接提供） ---
    try:
        resp = http_get(f"https://github.com/{username}",
                        timeout=config.HTTP_TIMEOUT, retries=2, backoff=4.0,
                        headers={"User-Agent": "Mozilla/5.0 (compatible)"})
        h = resp.text
        # pinned 仓库：<span class="repo">name</span>  或
        # <span class="text-bold ...">owner/repo</span> 在 pinned-item-list-item
        pinned = re.findall(
            r'<span[^>]+class="[^"]*repo[^"]*"[^>]*>\s*([^<]+?)\s*</span>', h)
        if not pinned:
            # newer GitHub HTML: data-hovercard-type="repository"
            pinned = re.findall(
                r'data-hovercard-type="repository"[^>]*href="/[^/]+/([^"]+)"', h)
        out["pinned"] = [_clean(p) for p in pinned[:6]]
    except Exception as e:
        log(f"[github] 主页 HTML 抓取失败(降级) {username}: {e}")
        out.setdefault("pinned", [])

    if not out.get("stars") and not out.get("bio") and not out.get("pinned"):
        return {}

    return out


def find_profile(name_en: str, homepage: str = "", bio: str = "") -> dict:
    """优先从主页/简介里直接抽 github 用户名；抽到才抓 Profile 主页。
    （不做纯姓名搜索——同名风险太高，宁缺毋滥。）
    """
    username = extract_github_username(homepage, bio)
    if not username:
        return {}
    result = fetch_user_profile(username)
    if not result:
        return {}
    # 把旧接口的 github_url / stars / repos 字段保持兼容
    return result
