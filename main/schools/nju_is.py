# -*- coding: utf-8 -*-
"""南京大学智能科学与技术学院 解析器。

数据来源（已实测）：
- 列表页使用 JS 动态渲染，数据来自 POST JSON API：
    POST https://is.nju.edu.cn/_wp3services/generalQuery?queryObj=teacherHome
  参数：siteId=786, articleType=1, level=1, conditions（按职称过滤）
  返回字段：title（姓名）、cnUrl（个人页）、exField1（研究方向）、exField2（职称）
- 覆盖四类：教授、副教授、准长聘（长聘副教授/准聘副教授/准聘助理教授）、兼职教授
- 个人页（is.nju.edu.cn 子路径 /USERNAME/main.htm）：
    <div class="personinfo clearfix"> 含姓名、邮件
    <div class="con_box"> → <div class="con_info"> 含个人简历正文
"""
from __future__ import annotations
import json
import re
import html as htmllib

from schools.base import SchoolParser
from core.models import TeacherStub, Teacher
from core.utils import http_get, http_post, log, polite_sleep
import config

BASE_URL = "https://is.nju.edu.cn"
API_URL = "https://is.nju.edu.cn/_wp3services/generalQuery?queryObj=teacherHome"
SITE_ID = 786

# (category_label, conditions 追加项)
_CATEGORIES: list[tuple[str, list[dict]]] = [
    ("教授",   [{"field": "exField2", "value": "教授", "judge": "="}]),
    ("副教授", [{"field": "exField2", "value": "副教授", "judge": "="}]),
    ("准长聘", [{"orConditions": [
        {"field": "exField2", "value": "长聘副教授",   "judge": "="},
        {"field": "exField2", "value": "准聘副教授",   "judge": "="},
        {"field": "exField2", "value": "准聘助理教授", "judge": "="},
    ]}]),
    ("兼职教授", [{"field": "exField2", "value": "兼职教授", "judge": "="}]),
]

_RETURN_INFOS = [
    {"field": "exField1", "name": "exField1"},   # 研究方向
    {"field": "exField2", "name": "exField2"},   # 职称
    {"field": "cnUrl",    "name": "cnUrl"},
    {"field": "title",    "name": "title"},       # 姓名
    {"field": "phone",    "name": "phone"},
]

_ORDERS = [{"field": "siteSort", "type": "asc"}]


def _clean(s: str) -> str:
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


class NjuIsParser(SchoolParser):
    name = "nju_is"
    display_name = "南京大学智能科学与技术学院"
    output_dir_name = "南大智科导师联系"

    # ------------------------------------------------------------------ #
    #  列表抓取
    # ------------------------------------------------------------------ #

    def fetch_teacher_list(self) -> list[TeacherStub]:
        stubs: list[TeacherStub] = []
        seen: set[str] = set()

        for cat_label, extra_conds in _CATEGORIES:
            page = 1
            while True:
                conds = [{"field": "published", "value": "1", "judge": "="}] + extra_conds
                post_data = {
                    "siteId":      SITE_ID,
                    "pageIndex":   page,
                    "rows":        100,
                    "orders":      json.dumps(_ORDERS),
                    "returnInfos": json.dumps(_RETURN_INFOS),
                    "conditions":  json.dumps(conds),
                    "articleType": 1,
                    "level":       1,
                }
                try:
                    resp = http_post(
                        API_URL,
                        data=post_data,
                        timeout=config.HTTP_TIMEOUT,
                        headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
                    )
                    result = resp.json()
                except Exception as e:
                    log(f"[nju_is] {cat_label} 第{page}页 API 失败: {e}")
                    break

                items = result.get("data") or []
                new_stubs = self._parse_api_items(items, cat_label, seen)
                log(f"[nju_is] {cat_label} 第{page}页 新增 {len(new_stubs)} 人")
                stubs.extend(new_stubs)

                page_count = result.get("pageCount", 1)
                if page >= page_count or not items:
                    break
                page += 1
                if page > 20:
                    break
                polite_sleep(0.5)

        log(f"[nju_is] 列表抓取完毕，共 {len(stubs)} 人")
        return stubs

    def _parse_api_items(
        self, items: list[dict], cat_label: str, seen: set
    ) -> list[TeacherStub]:
        stubs: list[TeacherStub] = []
        for art in items:
            name = (art.get("title") or "").strip()
            raw_url = (art.get("cnUrl") or "").strip()
            if not name or not raw_url:
                continue
            detail_url = _make_abs(raw_url)
            if detail_url in seen:
                continue
            seen.add(detail_url)

            title_val = (art.get("exField2") or "").strip()
            research   = (art.get("exField1") or "").strip()

            institute = f"南京大学智能科学与技术学院·{cat_label}"
            stub = TeacherStub(name=name, detail_url=detail_url, institute=institute)
            stub._title_hint    = title_val   # type: ignore[attr-defined]
            stub._research_hint = research    # type: ignore[attr-defined]
            stubs.append(stub)
        return stubs

    # ------------------------------------------------------------------ #
    #  详情页抓取
    # ------------------------------------------------------------------ #

    def fetch_teacher_detail(self, stub: TeacherStub) -> Teacher:
        t = Teacher(name=stub.name, detail_url=stub.detail_url,
                    institute=stub.institute)
        t.homepage = stub.detail_url

        # 从列表 API 取得的职称和研究方向直接用
        t.title = getattr(stub, "_title_hint", "")
        research_hint = getattr(stub, "_research_hint", "")

        try:
            resp = http_get(stub.detail_url, timeout=config.HTTP_TIMEOUT)
            resp.encoding = "utf-8"
            h = resp.text
        except Exception as e:
            log(f"[nju_is] 个人页失败 {stub.name}: {e}")
            if research_hint:
                t.bio = f"研究方向：{research_hint}"
            return t

        h2 = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S)

        self._parse_contact(h2, t)
        self._parse_bio(h2, t, research_hint)
        self._parse_papers(h2, t)
        return t

    def _parse_contact(self, h: str, t: Teacher) -> None:
        """从 class="personinfo clearfix" 提取邮箱。"""
        pi_m = re.search(r'class="personinfo clearfix">(.*?)</div>\s*</div>', h, re.S)
        block = pi_m.group(1) if pi_m else h

        # 邮件：<div class="zd">邮件：<span><a href="mailto:...">...</a></span></div>
        mailto_m = re.search(r'mailto:([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})', block)
        if mailto_m:
            t.email = mailto_m.group(1)
            return

        # 备用：邮件标签后的文本
        email_text_m = re.search(
            r'邮件[：:]\s*<[^>]+>\s*([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})',
            block, re.S
        )
        if email_text_m:
            t.email = email_text_m.group(1)
            return

        # 宽松：扫全页 nju.edu.cn 邮箱
        if not t.email:
            em = re.search(
                r'[A-Za-z0-9._%+\-]+@(?:[A-Za-z0-9.\-]*\.)?nju\.edu\.cn',
                h
            )
            if em:
                t.email = em.group(0)

    def _parse_bio(self, h: str, t: Teacher, research_hint: str) -> None:
        """从 class="con_info" 提取个人简历正文。"""
        # <div class="con_info"> <div class="name">个人简历</div> <div class="con">...</div>
        con_m = re.search(
            r'class="con_info">.*?class="con">(.*?)</div>\s*</div>',
            h, re.S
        )
        bio_text = ""
        if con_m:
            bio_text = _clean(con_m.group(1))
            if len(bio_text) > 2000:
                bio_text = bio_text[:2000]

        if not t.title and bio_text:
            tm = re.search(
                r"(讲席教授|特任教授|特任副教授|特任研究员|特任副研究员"
                r"|教授|副教授|研究员|副研究员|讲师|助理教授|助理研究员"
                r"|长聘副教授|准聘副教授|准聘助理教授)",
                bio_text
            )
            if tm:
                t.title = tm.group(1)

        if research_hint and bio_text:
            t.bio = f"研究方向：{research_hint}\n\n{bio_text}"
        elif research_hint:
            t.bio = f"研究方向：{research_hint}"
        else:
            t.bio = bio_text

    def _parse_papers(self, h: str, t: Teacher) -> None:
        """从详情页 <li> 提取看起来像论文的条目。"""
        papers: list[str] = []
        for li_m in re.finditer(r"<li[^>]*>(.*?)</li>", h, re.S):
            text = _clean(li_m.group(1))
            if len(text) > 40 and re.search(
                r"\b(AAAI|ICLR|NeurIPS|ICML|ACL|EMNLP|CVPR|ICCV|ECCV|KDD|SIGIR"
                r"|WWW|SIGMOD|VLDB|OSDI|SOSP|IEEE|ACM|Trans|Journal|Conference|arXiv)\b",
                text, re.I
            ):
                papers.append(text[:300])
        t.papers_listed = papers[:30]
