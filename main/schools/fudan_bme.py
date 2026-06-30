# -*- coding: utf-8 -*-
"""复旦大学生物医学工程与技术创新学院 解析器。

数据来源（已实测）：
  列表：POST https://bme-college.fudan.edu.cn/_wp3services/generalQuery?queryObj=teacherHome
        siteId=1082，全部46人一页返回，JSON 中直接含 title/email/phone/cnUrl/exField3（系所）。
  详情：个人主页 http://bme-college.fudan.edu.cn/<slug>/main.htm
        页面包含：个人简介、主要研究方向、代表性成果、学术任职、学习/工作经历 等。
"""
from __future__ import annotations
import json
import re
import html as htmllib

from schools.base import SchoolParser
from core.models import TeacherStub, Teacher
from core.utils import http_get, http_post, log, polite_sleep
import config

BASE_URL = "https://bme-college.fudan.edu.cn"
LIST_API = "https://bme-college.fudan.edu.cn/_wp3services/generalQuery?queryObj=teacherHome"

# POST 参数（模拟 teacher2.js 中的请求）
_RETURN_INFOS = json.dumps([
    {"field": "title",      "name": "title"},
    {"field": "exField1",   "name": "exField1"},    # 职级（正高/副高/初中级）
    {"field": "exField7",   "name": "exField7"},    # 职称（教授/研究员/…）
    {"field": "exField3",   "name": "exField3"},    # 系所中心
    {"field": "exField10",  "name": "exField10"},   # 人才计划
    {"field": "phone",      "name": "phone"},
    {"field": "firstLetter","name": "firstLetter"},
    {"field": "email",      "name": "email"},
    {"field": "cnUrl",      "name": "cnUrl"},
    {"field": "headerPic",  "name": "headerPic"},
])
_ORDERS = json.dumps([{"field": "letter", "type": "asc"}])
_CONDITIONS = json.dumps([{"field": "scope", "value": 0, "judge": "="}])


def _clean(s: str) -> str:
    """Strip HTML tags, unescape entities, collapse whitespace."""
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t\r\n\xa0　]+", " ", s)
    return s.strip()


def _extract_section(h: str, section_title: str) -> str:
    """Extract text from <div class="con con2">…</div> that follows a <div class="tt">section_title</div>."""
    pattern = (
        r'<div[^>]*class="tt"[^>]*>\s*'
        + re.escape(section_title)
        + r'\s*</div>\s*<div[^>]*class="con con2"[^>]*>(.*?)</div>'
    )
    m = re.search(pattern, h, re.S | re.I)
    if not m:
        return ""
    return _clean(m.group(1))


class FudanBmeParser(SchoolParser):
    name = "fudan_bme"
    display_name = "复旦大学生物医学工程学院"
    output_dir_name = "复旦生医工导师联系"

    # ------------------------------------------------------------------ #
    # LIST                                                                 #
    # ------------------------------------------------------------------ #

    def fetch_teacher_list(self) -> list[TeacherStub]:
        """Call the JSON API; all ~46 teachers are returned in one page."""
        stubs: list[TeacherStub] = []
        page = 1
        while True:
            payload = {
                "siteId": 1082,
                "level": 1,
                "articleType": 1,
                "pageIndex": page,
                "rows": 100,        # generous page size
                "orders": _ORDERS,
                "returnInfos": _RETURN_INFOS,
                "conditions": _CONDITIONS,
            }
            try:
                resp = http_post(
                    LIST_API,
                    data=payload,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
                        "Referer": f"{BASE_URL}/szdw/list.htm",
                    },
                    timeout=config.HTTP_TIMEOUT,
                )
                result = resp.json()
            except Exception as e:
                log(f"[fudan_bme] 列表 API 第{page}页失败: {e}")
                break

            data = result.get("data") or []
            log(f"[fudan_bme] 第{page}页 获取 {len(data)} 人（共 {result.get('total', '?')} 人）")
            for art in data:
                name = _clean(art.get("title", ""))
                if not name:
                    continue
                cn_url = (art.get("cnUrl") or "").strip()
                # Ensure URL is absolute
                if cn_url.startswith("/"):
                    cn_url = BASE_URL + cn_url
                institute = _clean(art.get("exField3", "") or "生物医学工程与技术创新学院")
                stub = TeacherStub(name=name, detail_url=cn_url, institute=institute)
                # Stash extra fields from list for use in detail
                stub._bme_title_level = _clean(art.get("exField1", ""))  # 正高/副高/初中级
                stub._bme_title = _clean(art.get("exField7", ""))        # 职称
                stub._bme_phone = _clean(art.get("phone", ""))
                stub._bme_email = _clean(art.get("email", ""))
                stub._bme_talent = _clean(art.get("exField10", ""))
                stubs.append(stub)

            page_count = result.get("pageCount", 1)
            if page >= page_count:
                break
            page += 1
            polite_sleep(0.5)

        log(f"[fudan_bme] 列表共获取 {len(stubs)} 人")
        return stubs

    # ------------------------------------------------------------------ #
    # DETAIL                                                               #
    # ------------------------------------------------------------------ #

    def fetch_teacher_detail(self, stub: TeacherStub) -> Teacher:
        t = Teacher(
            name=stub.name,
            detail_url=stub.detail_url,
            institute=stub.institute,
        )
        # Populate from list-phase data (always available)
        t.title = getattr(stub, "_bme_title", "") or ""
        t.email = getattr(stub, "_bme_email", "") or ""
        phone = getattr(stub, "_bme_phone", "") or ""
        talent = getattr(stub, "_bme_talent", "") or ""

        if not stub.detail_url:
            return t

        try:
            resp = http_get(stub.detail_url, timeout=config.HTTP_TIMEOUT)
            resp.encoding = "utf-8"
            h = resp.text
        except Exception as e:
            log(f"[fudan_bme] 个人页失败 {stub.name}: {e}")
            return t

        # --- email (prefer detail page; fallback to list value) ---
        m_email = re.search(
            r'<span>\s*电子邮箱[：:]\s*</span>\s*<span[^>]*class="co"[^>]*>\s*([^\s<]+)\s*</span>',
            h, re.I
        )
        if m_email:
            t.email = _clean(m_email.group(1))

        # --- phone (from detail page) ---
        m_phone = re.search(
            r'<span>\s*联系电话[：:]\s*</span>\s*<span[^>]*class="co"[^>]*>\s*([^<]+)\s*</span>',
            h, re.I
        )
        if m_phone:
            phone_detail = _clean(m_phone.group(1))
            if phone_detail:
                phone = phone_detail

        # --- title/职称 from detail page (sometimes richer) ---
        m_post = re.search(
            r'<span>\s*职称[：:]\s*</span>\s*<span[^>]*id="post-field"[^>]*>\s*([^<]+)\s*</span>',
            h, re.I
        )
        if m_post:
            detail_title = _clean(m_post.group(1)).strip("、，,")
            if detail_title and detail_title not in ("无",):
                t.title = detail_title

        # --- research area ---
        research_area = _extract_section(h, "主要研究方向")

        # --- bio (个人简介) ---
        bio = _extract_section(h, "个人简介")

        # --- academic positions (学术任职) ---
        academic = _extract_section(h, "学术任职")

        # --- work/education experience ---
        experience = _extract_section(h, "学习/工作经历")

        # Build combined bio
        parts = []
        if bio:
            parts.append(bio)
        if research_area:
            parts.append("主要研究方向：" + research_area)
        if academic:
            parts.append("学术任职：" + academic)
        if experience:
            parts.append("学习/工作经历：" + experience)
        if phone:
            parts.append("联系电话：" + phone)
        if talent and talent not in ("无", ""):
            parts.append("人才计划：" + talent)
        t.bio = " | ".join(parts)

        # --- papers (代表性成果) ---
        papers_block = _extract_section(h, "代表性成果")
        if papers_block:
            # Split into individual paper entries by numbered pattern [1] / 1. etc.
            raw_papers = re.split(r'(?<!\w)\[?\d{1,3}\]?[.\s]', papers_block)
            papers = []
            for p in raw_papers:
                p = p.strip()
                if len(p) > 20:   # ignore very short fragments
                    papers.append(p)
            t.papers_listed = papers[:30]  # cap at 30 to keep output sane

        return t
