# -*- coding: utf-8 -*-
"""GitHub（best-effort）：找老师的 GitHub 主页与代表仓库（按 star）。

用公开 API（未鉴权有 60次/小时限额；设环境变量 GITHUB_TOKEN 可提额）。
任何失败返回空，绝不阻断主流程。GitHub 匹配同名风险高 → 只在
个人主页/简介里出现 github.com 链接，或检索到高置信用户时才采纳。
"""
from __future__ import annotations
import os
import re
from core.utils import http_get_proxied, log, polite_sleep
import config

API = "https://api.github.com"


def _headers():
    h = {"Accept": "application/vnd.github+json"}
    tok = os.environ.get("GITHUB_TOKEN", "")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def extract_github_url(*texts: str) -> str:
    """从主页/简介文本里直接抽 github.com/用户名（最可靠，无歧义）。"""
    blob = " ".join(t or "" for t in texts)
    m = re.search(r"github\.com/([A-Za-z0-9][\w-]{0,38})", blob)
    if m and m.group(1).lower() not in ("about", "features", "topics", "orgs"):
        return f"https://github.com/{m.group(1)}"
    return ""


def fetch_user_repos(username: str, top: int = 5) -> dict:
    """取该用户按 star 排序的代表仓库。返回 {github_url, stars, repos:[(name,stars,desc)]} 或 {}。"""
    if not username:
        return {}
    url = f"{API}/users/{username}/repos?per_page=100&sort=pushed"
    try:
        resp = http_get_proxied(url, timeout=config.HTTP_TIMEOUT, retries=2, backoff=4.0,
                                headers=_headers())
        if resp.status_code != 200:
            return {}
        repos = resp.json()
    except Exception as e:
        log(f"[github] 仓库抓取失败(降级) {username}: {e}")
        return {}
    if not isinstance(repos, list):
        return {}
    own = [r for r in repos if not r.get("fork")]
    own.sort(key=lambda r: r.get("stargazers_count", 0), reverse=True)
    total_stars = sum(r.get("stargazers_count", 0) for r in own)
    reps = [(r.get("name", ""), r.get("stargazers_count", 0), (r.get("description") or "")[:120])
            for r in own[:top] if r.get("stargazers_count", 0) > 0]
    polite_sleep(getattr(config, "GITHUB_SLEEP", 1.0))
    return {
        "github_url": f"https://github.com/{username}",
        "stars": total_stars,
        "repos": reps,
    }


def find_profile(name_en: str, homepage: str = "", bio: str = "") -> dict:
    """优先从主页/简介里直接抽 github 链接；抽到才取仓库。
    （不做纯姓名搜索——同名风险太高，宁缺毋滥。）
    """
    gh = extract_github_url(homepage, bio)
    if not gh:
        return {}
    username = gh.rstrip("/").rsplit("/", 1)[-1]
    return fetch_user_repos(username)
