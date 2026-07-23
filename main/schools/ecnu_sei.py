# -*- coding: utf-8 -*-
"""华东师范大学软件工程学院 解析器。

数据来源（已实测）：
- 列表页：https://sei.ecnu.edu.cn/33188/list.htm
  结构：单页静态，<div class="wp_articlecontent"> 内含一张 HTML 表格
  列：姓名（链接至 faculty.ecnu.edu.cn）| 研究方向 | 邮箱
  当前共 41 名博士生导师（学术学位）；若后续出现分页（list2.htm）也可正确翻页。
- 详情页：https://faculty.ecnu.edu.cn/...
  通用华师教工个人页格式：
    - 个人资料 section → 电子邮箱
    - 个人简介 section → bio 文本（职称从中推断）
    - 研究方向 section → 研究方向文本
    - 学术成果 section → 论文列表（■ 分隔）
"""
from __future__ import annotations
import re
import html as htmllib

from schools.base import SchoolParser
from core.models import TeacherStub, Teacher
from core.utils import http_get, log, polite_sleep  # noqa: F401
import config

LIST_URL = "https://sei.ecnu.edu.cn/33188/list.htm"
BASE_URL = "https://sei.ecnu.edu.cn"
FACULTY_BASE = "https://faculty.ecnu.edu.cn"


def _clean(s: str) -> str:
    """去 HTML 标签、转义实体、折叠空白。"""
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t\r\n\xa0　]+", " ", s)
    return s.strip()


def _make_abs(href: str, base: str = BASE_URL) -> str:
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return base + href
    return base + "/" + href


class EcnuSeiParser(SchoolParser):
    name = "ecnu_sei"
    display_name = "华东师范大学软件工程学院"
    output_dir_name = "华师软件导师联系"

    # ------------------------------------------------------------------ #
    #  列表抓取
    # ------------------------------------------------------------------ #

    def fetch_teacher_list(self) -> list[TeacherStub]:
        stubs: list[TeacherStub] = []
        seen: set[str] = set()
        page = 1
        while True:
            url = LIST_URL if page == 1 else LIST_URL.replace("list.htm", f"list{page}.htm")
            try:
                resp = http_get(url, timeout=config.HTTP_TIMEOUT)
                resp.encoding = "utf-8"
                h = resp.text
            except Exception as e:
                log(f"[ecnu_sei] 第{page}页获取失败: {e}")
                break

            new_stubs = self._parse_list_html(h, seen)
            log(f"[ecnu_sei] 第{page}页 新增 {len(new_stubs)} 人")
            stubs.extend(new_stubs)

            if not new_stubs:
                break
            if not self._has_next_page(h, page):
                break
            page += 1
            if page > 20:
                break
            polite_sleep(0.5)

        log(f"[ecnu_sei] 列表抓取完毕，共 {len(stubs)} 人")
        return stubs

    def _parse_list_html(self, h: str, seen: set) -> list[TeacherStub]:
        """解析列表页 HTML 中的教师表格。

        表格结构（3 列）：
          <td> <a href="faculty URL">姓名</a> </td>
          <td> 研究方向文本 </td>
          <td> 邮箱文本 </td>
        """
        stubs: list[TeacherStub] = []

        # 定位主体表格
        table_m = re.search(
            r'<table[^>]*class="[^"]*wp_editor_art_table[^"]*"[^>]*>(.*?)</table>',
            h, re.S
        )
        if not table_m:
            return stubs
        table_html = table_m.group(1)

        # 逐行解析（跳过 firstRow 表头）
        row_pattern = re.compile(r'<tr(?:\s[^>]*)?>(.*?)</tr>', re.S)
        for row_m in row_pattern.finditer(table_html):
            row_html = row_m.group(1)
            if 'firstRow' in row_html or '<th' in row_html:
                continue

            # 提取三列
            tds = re.findall(r'<td[^>]*>(.*?)</td>', row_html, re.S)
            if len(tds) < 3:
                continue

            td_name, td_research, td_email = tds[0], tds[1], tds[2]

            # 姓名列 → 链接 + 名字
            link_m = re.search(
                r'<a\s+href=[\'"]([^\'"]+)[\'"][^>]*>([^<]+)</a>',
                td_name, re.S
            )
            if not link_m:
                continue
            raw_href = link_m.group(1).strip()
            name = _clean(link_m.group(2))
            if not name:
                continue

            detail_url = _make_abs(raw_href, FACULTY_BASE)
            if detail_url in seen:
                continue
            seen.add(detail_url)

            # 研究方向（备用，列表页已有，详情页会覆盖）
            research = _clean(td_research)
            # 邮箱（备用）
            email_hint = _clean(td_email)

            stub = TeacherStub(
                name=name,
                detail_url=detail_url,
                institute="华师软件工程学院",
            )
            stub._research_hint = research    # type: ignore[attr-defined]
            stub._email_hint = email_hint     # type: ignore[attr-defined]
            stubs.append(stub)

        return stubs

    @staticmethod
    def _has_next_page(h: str, current_page: int) -> bool:
        next_page = current_page + 1
        if re.search(rf'list{next_page}\.htm', h):
            return True
        if re.search(r'class=["\']next["\']', h):
            return True
        return False

    # ------------------------------------------------------------------ #
    #  详情页抓取
    # ------------------------------------------------------------------ #

    def fetch_teacher_detail(self, stub: TeacherStub) -> Teacher:
        t = Teacher(
            name=stub.name,
            detail_url=stub.detail_url,
            institute=stub.institute,
        )

        # 先用列表页的邮箱填充（详情页优先覆盖）
        email_hint = getattr(stub, "_email_hint", "")
        if email_hint and "@" in email_hint:
            t.email = email_hint

        try:
            resp = http_get(stub.detail_url, timeout=config.HTTP_TIMEOUT)
            resp.encoding = "utf-8"
            h = resp.text
        except Exception as e:
            log(f"[ecnu_sei] 个人页获取失败 {stub.name}: {e}")
            return t

        _parse_faculty_detail(h, t)
        return t


# ------------------------------------------------------------------ #
#  通用华师教工详情页解析（与 ecnu_cs 共享相同格式）
# ------------------------------------------------------------------ #

def _parse_faculty_detail(h: str, t: Teacher) -> None:
    """从 faculty.ecnu.edu.cn 个人页 HTML 填充 Teacher（原地修改）。"""
    h2 = re.sub(r"<(script|style)[^>]+>.*?</\1>", " ", h, flags=re.S)

    # 按 section 分割（每个 <div class="tt"> 是一个小节标题）
    parts = re.split(r'<div\s+class="tt">', h2)
    sections: dict[str, str] = {}
    for part in parts[1:]:
        title_m = re.search(
            r'<h3\s+class="tit">\s*<span[^>]*>\s*(.*?)\s*</span>',
            part, re.S
        )
        if not title_m:
            continue
        sec_title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip()
        con_m = re.search(r'<div\s+class="con[^"]*">(.*)', part, re.S)
        sections[sec_title] = con_m.group(1) if con_m else part

    # ---- 邮箱（来自个人资料 section）----
    profile = sections.get("个人资料", "")
    if profile:
        em_m = re.search(
            r'电子邮箱[^<]*</span>\s*<span\s+class="txt">\s*([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})',
            profile
        )
        if em_m:
            t.email = em_m.group(1).strip()

    # 若仍无邮箱，尝试全文匹配
    if not t.email:
        em_m2 = re.search(
            r'\b([A-Za-z0-9._%+\-]+@(?:sei|cs|ecnu|geo)\.ecnu\.edu\.cn)\b',
            h2
        )
        if em_m2:
            t.email = em_m2.group(1).strip()

    # ---- 职称（个人资料的专业技术职务，或从简介文本推断）----
    if profile:
        ztw_m = re.search(
            r'专业技术职务[^<]*</span>\s*<span\s+class="txt">\s*([^<\s][^<]+)',
            profile
        )
        if ztw_m:
            raw = ztw_m.group(1).strip()
            if raw:
                t.title = raw

    bio_sec = sections.get("个人简介", "")
    if not t.title and bio_sec:
        bio_text = _clean(bio_sec)
        title_m = re.search(
            r"(讲席教授|特任教授|特任副教授|特任研究员|特任副研究员"
            r"|教授|副教授|研究员|副研究员|讲师|助理教授|助理研究员)",
            bio_text
        )
        if title_m:
            t.title = title_m.group(1)

    # ---- Bio（个人简介）----
    if bio_sec:
        bio_text = _clean(bio_sec)
        if len(bio_text) > 30:
            t.bio = bio_text[:2000]

    # ---- 研究方向 ----
    research_sec = sections.get("研究方向", "")
    if research_sec:
        research_text = _clean(research_sec)
        if len(research_text) > 5:
            # 追加到 bio 末尾（供 analyzer 使用）
            if t.bio:
                t.bio = t.bio + "\n【研究方向】" + research_text[:500]
            else:
                t.bio = "【研究方向】" + research_text[:500]

    # ---- 论文列表（学术成果，以 ■ 分隔条目）----
    achiev_sec = sections.get("学术成果", "")
    if achiev_sec:
        achiev_clean = _clean(achiev_sec)
        # ■ 前缀是华师个人页惯用论文条目标记
        entries = re.split(r'■\s*', achiev_clean)
        papers: list[str] = []
        for entry in entries[1:]:  # 第 0 个是 ■ 前的前缀文字
            entry = entry.strip()
            if len(entry) > 30:
                papers.append(entry[:300])
        t.papers_listed = papers[:30]

    # 若 ■ 标记不够，用 li 文本补充
    if not t.papers_listed:
        li_texts = re.findall(r"<li[^>]*>(.*?)</li>", achiev_sec, re.S)
        papers = []
        for li in li_texts:
            text = _clean(li)
            if len(text) > 40 and re.search(
                r"\b(AAAI|ICLR|NeurIPS|ICML|ACL|EMNLP|CVPR|ICCV|ECCV|KDD|SIGIR|WWW"
                r"|SIGMOD|VLDB|IEEE|ACM|Trans|Journal|Conference|arXiv)\b",
                text, re.I
            ):
                papers.append(text[:300])
        t.papers_listed = papers[:30]

    # ---- 个人主页（外部链接）----
    ext_links = re.findall(
        r'href=[\'\"](https?://(?!faculty\.ecnu\.edu\.cn)[^\'\"]{10,})[\'\"]\s*[^>]*>\s*(?:个人主页|个人网页|Homepage)',
        h2, re.I | re.S
    )
    if ext_links:
        t.homepage = ext_links[0].strip()
