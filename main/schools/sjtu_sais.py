# -*- coding: utf-8 -*-
"""上海交通大学自动化与感知学院 解析器。

数据来源（已实测）：
- 列表：POST https://sais.sjtu.edu.cn/active/ajax_teacher_list.html
        参数 cat_id=18 / cat_code=faculty / type=1 / zm / search → JSON.content 为 HTML
        HTML 结构：<div class="js-list"> <li><a href=".../faculty/X.html" class="name">姓名</a></li> ...
        一次性返回全部教师（约 200 人），无真实翻页
- 个人页：GET https://sais.sjtu.edu.cn/faculty/X.html（UTF-8）
        头部 <div class="js-info">：
          <div class="tit"><p>姓名</p><span>职称</span></div>
          <div class="detail"><p>联系方式/邮箱…</p></div>
        内容区 <div class="js-box"> 内多个 <div class="js-item">：
          <div class="tit"><p>研究方向|个人简介|科研项目|科研成果|…</p></div>
          <div class="detail"><div class="txt"><p>内容</p></div></div>
"""
from __future__ import annotations
import re
import html as htmllib

from schools.base import SchoolParser
from core.models import TeacherStub, Teacher
from core.utils import http_get, http_post, log, polite_sleep
import config

LIST_API = "https://sais.sjtu.edu.cn/active/ajax_teacher_list.html"
DETAIL_PREFIX = "https://sais.sjtu.edu.cn/faculty/"


def _clean(s: str) -> str:
    """去 HTML 标签、解实体、压空白。"""
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t\r\n\xa0　]+", " ", s)
    return s.strip()


class SjtuSaisParser(SchoolParser):
    name = "sjtu_sais"
    display_name = "上海交通大学自动化与感知学院"
    output_dir_name = "交大自动化导师联系"

    # ---------------------------------------------------------------------- 列表
    def fetch_teacher_list(self) -> list[TeacherStub]:
        stubs: list[TeacherStub] = []
        seen: set[str] = set()
        page = 1
        while True:
            data = {
                "cat_id": "18",
                "cat_code": "faculty",
                "type": "1",
                "zm": "",
                "search": "",
            }
            try:
                resp = http_post(
                    LIST_API, data=data, timeout=config.HTTP_TIMEOUT,
                    headers={"X-Requested-With": "XMLHttpRequest"},
                )
                payload = resp.json()
            except Exception as e:
                log(f"[sjtu_sais] 列表第 {page} 页抓取失败: {e}")
                break

            content = payload.get("content", "") or ""
            new_stubs = self._parse_list_html(content, seen)
            log(f"[sjtu_sais] 列表第 {page} 页：新增 {len(new_stubs)} 人（累计 {len(stubs) + len(new_stubs)}）")
            stubs.extend(new_stubs)

            # 该接口一次性返回全部，无新增即停止
            if not new_stubs:
                break
            page += 1
            if page > 5:
                break
            polite_sleep(0.5)
        return stubs

    def _parse_list_html(self, content: str, seen: set[str]) -> list[TeacherStub]:
        """从 <div class="js-list"> 内的 <li><a href="...">姓名</a></li> 提取教师。"""
        stubs: list[TeacherStub] = []
        # 匹配：<a href="https://sais.sjtu.edu.cn/faculty/X.html" class="name">姓名</a>
        for m in re.finditer(
            r'<a\s[^>]*href="(https://sais\.sjtu\.edu\.cn/faculty/[^"]+\.html)"[^>]*class="name"[^>]*>\s*([^<]+?)\s*</a>',
            content,
        ):
            url = m.group(1).strip()
            name = _clean(m.group(2))
            if not name or url in seen:
                continue
            seen.add(url)
            stubs.append(TeacherStub(name=name, detail_url=url, institute="自动化与感知学院"))
        return stubs

    # ------------------------------------------------------------------ 个人详情
    def fetch_teacher_detail(self, stub: TeacherStub) -> Teacher:
        t = Teacher(name=stub.name, detail_url=stub.detail_url, institute=stub.institute)
        try:
            resp = http_get(stub.detail_url, timeout=config.HTTP_TIMEOUT)
            resp.encoding = "utf-8"
            h = resp.text
        except Exception as e:
            log(f"[sjtu_sais] 个人页抓取失败 {stub.name}: {e}")
            return t

        # ---- 基本信息（js-info > tit + detail）----
        # 先把 js-info 区域切出来，避免导航/面包屑中的 class="tit" 干扰
        m_info = re.search(r'<div class="js-info">(.*?)<div class="js-box', h, re.S)
        info_html = m_info.group(1) if m_info else h[:4000]

        # 姓名（<div class="tit"><p>姓名</p>...）
        m = re.search(r'<div class="tit">\s*<p>\s*([^<]+?)\s*</p>', info_html)
        if m:
            t.name = _clean(m.group(1)) or t.name

        # 职称（<span>职称</span> 紧随 <p>姓名</p> 之后）
        m = re.search(r'<div class="tit">\s*<p>[^<]*</p>\s*<span>\s*([^<]+?)\s*</span>', info_html)
        if m:
            t.title = _clean(m.group(1))

        # detail 区：电话、邮箱、个人主页
        m_detail = re.search(r'<div class="detail">(.*?)</div>', info_html, re.S)
        if m_detail:
            detail_html = m_detail.group(1)
            em = re.search(r'邮\s*[箱件][：:]\s*([\w.\-+]+@[\w.\-]+)', detail_html)
            if not em:
                em = re.search(r'mailto:([\w.\-+]+@[\w.\-]+)', detail_html)
            if not em:
                em = re.search(r'([\w.\-+]+@sjtu\.edu\.cn)', detail_html)
            if em:
                t.email = em.group(1).strip()

            hp = re.search(r'(https?://[^\s<>"]+)', detail_html)
            if hp:
                url_cand = hp.group(1).rstrip(".,;")
                # 排除自身学院页
                if "sais.sjtu.edu.cn/faculty" not in url_cand:
                    t.homepage = url_cand

        # ---- 分节内容（js-item）----
        sections = self._parse_sections(h)

        # 个人简介优先级：个人简介 > 研究方向
        bio_keys = ["个人简介", "研究方向", "研究兴趣"]
        for k in bio_keys:
            if sections.get(k):
                t.bio = sections[k]
                break

        # 论文/科研成果
        paper_keys = ["科研成果", "学术论文", "代表性论文", "研究成果"]
        for k in paper_keys:
            if sections.get(k):
                t.papers_listed = self._parse_papers(sections[k])
                break

        return t

    def _parse_sections(self, h: str) -> dict[str, str]:
        """提取 js-item 各节：标题→正文文本。"""
        out: dict[str, str] = {}
        # js-item 结构：<div class="js-item"><div class="tit"><p>标题</p></div><div class="detail"><div class="txt">内容</div></div></div>
        for m in re.finditer(
            r'<div class="js-item">\s*<div class="tit">\s*<p>\s*([^<]+?)\s*</p>\s*</div>'
            r'\s*<div class="detail">\s*<div class="txt">(.*?)</div>\s*</div>\s*</div>',
            h, re.S,
        ):
            title = _clean(m.group(1))
            body = _clean(m.group(2))
            if title:
                out[title] = body
        return out

    def _parse_papers(self, raw: str) -> list[str]:
        """从科研成果段落中提取论文标题（按换行/句号分割）。"""
        if not raw:
            return []
        # 按换行或编号（1. 2. 等）分条
        items = re.split(r'\n+|\r\n+|(?<=[.。])\s+(?=\d)', raw)
        titles: list[str] = []
        for item in items:
            item = item.strip(" .\t")
            if len(item) < 10:
                continue
            # 论文标题通常以引号或字母/汉字开头
            if re.match(r'[\[\（\(]?\d+[\]\）\)]?\s*', item):
                item = re.sub(r'^[\[\（\(]?\d+[\]\）\)][.\s]*', '', item)
            if len(item) > 8:
                titles.append(item[:300])
        return titles[:15]
