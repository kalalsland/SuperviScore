# -*- coding: utf-8 -*-
"""上海科技大学信息科学与技术学院 解析器。

数据来源（已实测）：
- 列表页使用 sudyCloud CMS，教师数据通过 AJAX POST 到
  /_wp3services/generalQuery?queryObj=teacherHome 获取 JSON。
- siteId=43，每次可拉全量（rows=999），无需翻页。
- 只抓"常任教授"（col=0）和"特聘教授"（col=1）和"研究人员"（col=3）三类。
- 每位教师的 cnUrl 形如 http://sist.shanghaitech.edu.cn/xxx/main.htm。
- 详情页结构：
    .box_fr .fr_name           → 姓名
    .box_fr .fr_position       → 职称
    div.email span             → 邮箱
    div.person span a (个人主页) → 主页链接
    div.person_area span       → 研究方向
    div.conn1                  → 简介正文（HTML 富文本）
    div.conn7 li               → 官网论文列表（每 li 含作者、标题、期刊）
"""
from __future__ import annotations
import json
import re
import html as htmllib
from urllib.parse import urljoin, quote

from schools.base import SchoolParser
from core.models import TeacherStub, Teacher
from core.utils import http_get, http_post, log, polite_sleep
import config

BASE_URL = "https://sist.shanghaitech.edu.cn"
LIST_URL = "https://sist.shanghaitech.edu.cn/szdwx/list.htm"
API_URL = "https://sist.shanghaitech.edu.cn/_wp3services/generalQuery?queryObj=teacherHome"
SITE_ID = 43

# 要抓的分类：(exField8 值, 标签)
CATEGORIES = [
    ("常任教授", "常任教授"),
    ("特聘教授", "特聘教授"),
    ("研究人员", "研究人员"),
]

# POST 请求中固定的 returnInfos
_RETURN_INFOS = json.dumps([
    {"field": "title", "name": "title"},
    {"field": "graduateSchool", "name": "graduateSchool"},
    {"field": "phone", "name": "phone"},
    {"field": "email", "name": "email"},
    {"field": "cnUrl", "name": "cnUrl"},
    {"field": "headerPic", "name": "headerPic"},
    {"field": "exField1", "name": "exField1"},   # 职称
    {"field": "exField4", "name": "exField4"},   # 研究方向
    {"field": "exField5", "name": "exField5"},   # 研究中心
    {"field": "exField8", "name": "exField8"},   # 分类（常任教授/特聘教授/…）
], ensure_ascii=False)

_ORDERS = json.dumps([{"field": "siteSort", "type": "asc"}])


def _clean(s: str) -> str:
    """去 HTML 标签、HTML 实体、折叠空白。"""
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t\r\n\xa0　]+", " ", s)
    return s.strip()


def _make_abs(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return BASE_URL + href
    return BASE_URL + "/" + href


class ShanghaitechSistParser(SchoolParser):
    name = "shanghaitech_sist"
    display_name = "上海科技大学信息科学与技术学院"
    output_dir_name = "上科大SIST导师联系"

    # ------------------------------------------------------------------ #
    #  列表抓取（调 JSON API，无翻页，rows=999 一次拉完）
    # ------------------------------------------------------------------ #

    def fetch_teacher_list(self) -> list[TeacherStub]:
        stubs: list[TeacherStub] = []
        seen: set[str] = set()

        for cat_value, cat_label in CATEGORIES:
            conditions = json.dumps([
                {"field": "published", "value": "1", "judge": "="},
                {"field": "language", "value": "1", "judge": "="},
                {"field": "exField8", "value": cat_value, "judge": "="},
            ], ensure_ascii=False)

            post_data = {
                "siteId": str(SITE_ID),
                "columnId": "",
                "conditions": conditions,
                "returnInfos": _RETURN_INFOS,
                "pageIndex": "1",
                "orders": _ORDERS,
                "rows": "999",
                "articleType": "1",
                "level": "1",
            }

            try:
                resp = http_post(
                    API_URL,
                    data=post_data,
                    timeout=config.HTTP_TIMEOUT,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
                        "Referer": LIST_URL,
                    },
                )
                resp.encoding = "utf-8"
                result = resp.json()
            except Exception as e:
                log(f"[shanghaitech_sist] API 请求失败 ({cat_label}): {e}")
                continue

            items = result.get("data") or []
            new_count = 0
            for item in items:
                cn_url = (item.get("cnUrl") or "").strip()
                name = _clean(item.get("title") or "")
                if not name or not cn_url:
                    continue
                # 标准化 URL（有时是 http，有时是 https）
                if cn_url.startswith("http://"):
                    cn_url_https = "https://" + cn_url[7:]
                else:
                    cn_url_https = cn_url
                if cn_url_https in seen:
                    continue
                seen.add(cn_url_https)

                stub = TeacherStub(
                    name=name,
                    detail_url=cn_url_https,
                    institute=f"上科大SIST·{cat_label}",
                )
                # 列表已给出的字段作为 hint，detail 阶段覆写
                stub._email_hint = (item.get("email") or "").strip()       # type: ignore[attr-defined]
                stub._title_hint = _clean(item.get("exField1") or "")     # type: ignore[attr-defined]
                stub._research_hint = _clean(item.get("exField4") or "")  # type: ignore[attr-defined]
                stubs.append(stub)
                new_count += 1

            log(f"[shanghaitech_sist] {cat_label}: 获取 {new_count} 人")
            polite_sleep(0.5)

        log(f"[shanghaitech_sist] 列表抓取完毕，共 {len(stubs)} 人")
        return stubs

    # ------------------------------------------------------------------ #
    #  详情页抓取
    # ------------------------------------------------------------------ #

    def fetch_teacher_detail(self, stub: TeacherStub) -> Teacher:
        t = Teacher(
            name=stub.name,
            detail_url=stub.detail_url,
            institute=stub.institute,
        )

        # 先用列表已知数据填充（detail 页可能有更精确的，会覆写）
        email_hint = getattr(stub, "_email_hint", "")
        if email_hint and "@" in email_hint:
            t.email = email_hint
        title_hint = getattr(stub, "_title_hint", "")
        if title_hint:
            t.title = title_hint
        research_hint = getattr(stub, "_research_hint", "")

        try:
            resp = http_get(stub.detail_url, timeout=config.HTTP_TIMEOUT)
            resp.encoding = "utf-8"
            h = resp.text
        except Exception as e:
            log(f"[shanghaitech_sist] 个人页获取失败 {stub.name}: {e}")
            # 至少用列表数据填 bio
            if research_hint:
                t.bio = f"研究方向：{research_hint}"
            return t

        self._parse_detail_html(h, t, research_hint)
        return t

    # ------------------------------------------------------------------ #
    #  详情页 HTML 解析（原地修改 t）
    # ------------------------------------------------------------------ #

    def _parse_detail_html(self, h: str, t: Teacher, research_hint: str = "") -> None:
        # 去掉 script/style 干扰
        h2 = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S | re.I)

        # ---- 姓名（.fr_name）----
        name_m = re.search(r'class=["\']fr_name["\'][^>]*>(.*?)</div>', h2, re.S)
        if name_m:
            name_text = _clean(name_m.group(1))
            if name_text:
                t.name = name_text

        # ---- 职称（.fr_position）----
        pos_m = re.search(r'class=["\']fr_position["\'][^>]*>(.*?)</div>', h2, re.S)
        if pos_m:
            pos_text = _clean(pos_m.group(1))
            if pos_text:
                t.title = pos_text

        # ---- 邮箱（div.email > span）----
        email_m = re.search(
            r'class=["\']email["\'][^>]*>.*?<span>(.*?)</span>',
            h2, re.S
        )
        if email_m:
            email_text = _clean(email_m.group(1))
            if email_text and "@" in email_text:
                t.email = email_text
        # 备用：直接从全文提取 shanghaitech.edu.cn 邮箱
        if not t.email:
            fallback_m = re.search(
                r'\b([A-Za-z0-9._%+\-]+@(?:shanghaitech|sist\.shanghaitech)\.edu\.cn)\b',
                h2
            )
            if fallback_m:
                t.email = fallback_m.group(1).strip()

        # ---- 个人主页（div.person > span > a，标签含"个人主页"）----
        # 结构：<div class="person"> 个人主页：  <span><a href="URL">...</a></span></div>
        homepage_m = re.search(
            r'个人主页[^<]{0,20}<span>\s*<a\s+href=["\']([^"\']+)["\']',
            h2, re.S
        )
        if homepage_m:
            hp = homepage_m.group(1).strip()
            if hp and hp not in ("#", "javascript:void(0)"):
                t.homepage = hp

        # ---- 研究方向（.person_area > span）----
        research_m = re.search(
            r'person_area["\'][^>]*>.*?<span>(.*?)</span>',
            h2, re.S
        )
        research_text = ""
        if research_m:
            research_text = _clean(research_m.group(1))

        # ---- Bio（conn1：简介正文）----
        bio_m = re.search(r'class=["\']conn1 conn["\'][^>]*>(.*?)(?:class=["\']conn2 conn["\']|$)',
                          h2, re.S)
        bio_html = bio_m.group(1) if bio_m else ""
        bio_text = _clean(bio_html)

        # 把研究方向拼到 bio 开头（方便后续分析器读取）
        if research_text:
            t.bio = f"研究方向：{research_text}\n\n{bio_text}"[:3000]
        elif research_hint:
            t.bio = f"研究方向：{research_hint}\n\n{bio_text}"[:3000]
        else:
            t.bio = bio_text[:3000]

        # ---- 论文（conn7：官网论文列表）----
        papers_m = re.search(r'class=["\']conn7 conn["\'][^>]*>(.*?)(?:class=["\']conn8 conn["\']|$)',
                             h2, re.S)
        papers: list[str] = []
        if papers_m:
            papers_html = papers_m.group(1)
            li_blocks = re.findall(r"<li>(.*?)</li>", papers_html, re.S)
            for li in li_blocks:
                spans = re.findall(r"<span[^>]*>(.*?)</span>", li, re.S)
                parts = [_clean(sp) for sp in spans if _clean(sp)]
                # 去掉纯数字序号
                parts = [p for p in parts if not re.fullmatch(r"\d+\.?", p)]
                if parts:
                    paper_str = " ".join(parts)
                    if len(paper_str) > 20:
                        papers.append(paper_str[:400])
        t.papers_listed = papers[:50]
