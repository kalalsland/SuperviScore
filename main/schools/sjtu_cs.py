# -*- coding: utf-8 -*-
"""上海交通大学计算机学院（含网安/密码学院）解析器。

数据来源（已实测）：
- 列表：POST https://www.cs.sjtu.edu.cn/active/ajax_teacher_list.html
        参数 page/cat_id=20/cat_code=jiaoshiml/type=1/zm/zc/search → JSON.content 为 HTML
        HTML 按研究所分组：<div class="name">研究所名</div> ... <a href=".../jiaoshiml/X.html">姓名</a>
- 个人页：GET https://www.cs.sjtu.edu.cn/jiaoshiml/X.html （UTF-8）
        姓名 <div class="name">、职称 <div class="zw">、研究所/主页在 <div class="dt"> 的 <p> 内、
        分节 <div class="name"><p>个人简介|论文发表|...</p></div> + 同级 <div class="txt">
"""
from __future__ import annotations
import re
import json
import html as htmllib

from schools.base import SchoolParser
from core.models import TeacherStub, Teacher, Paper
from core.utils import http_get, http_post, log, polite_sleep
import config

LIST_API = "https://www.cs.sjtu.edu.cn/active/ajax_teacher_list.html"
DETAIL_PREFIX = "https://www.cs.sjtu.edu.cn/jiaoshiml/"


def _clean(s: str) -> str:
    """去标签、解 HTML 实体、压空白。"""
    s = re.sub(r"<[^>]+>", " ", s)
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t\r\n\xa0]+", " ", s)
    s = s.replace("　", "")   # 去全角空格（姓名中常见，如 陈　榕）
    return s.strip()


class SjtuCsParser(SchoolParser):
    name = "sjtu_cs"
    display_name = "上海交通大学计算机学院（网络空间安全学院、密码学院）"
    output_dir_name = "交大导师联系"

    # ------------------------------------------------------------------ 列表
    def fetch_teacher_list(self) -> list[TeacherStub]:
        stubs: list[TeacherStub] = []
        seen: set[str] = set()
        page = 1
        while True:
            data = {
                "page": page, "cat_id": "20", "cat_code": "jiaoshiml",
                "type": "1", "zm": "", "zc": "", "search": "",
            }
            try:
                resp = http_post(LIST_API, data=data, timeout=config.HTTP_TIMEOUT,
                                 headers={"X-Requested-With": "XMLHttpRequest"})
                payload = resp.json()
            except Exception as e:
                log(f"[sjtu] 列表第 {page} 页抓取失败: {e}")
                break

            content = payload.get("content", "") or ""
            page_stubs = self._parse_list_html(content)
            new_count = 0
            for st in page_stubs:
                if st.detail_url in seen:
                    continue
                seen.add(st.detail_url)
                stubs.append(st)
                new_count += 1

            log(f"[sjtu] 列表第 {page} 页：新增 {new_count} 人（累计 {len(stubs)}）")
            # 该接口一次性返回全部；若某页无新增则停止（翻页兜底）
            if new_count == 0:
                break
            page += 1
            if page > 20:   # 安全上限
                break
            polite_sleep(0.5)
        return stubs

    def _parse_list_html(self, content: str) -> list[TeacherStub]:
        """按研究所分组解析。结构：
            <div class="rc-item"><div class="tit"><div class="name">研究所</div>...
              <div class="dt"> ... <a href=".../jiaoshiml/X.html">姓名</a> ... </div></div>
        """
        stubs: list[TeacherStub] = []
        # 按 rc-item 切块，每块开头是研究所名
        blocks = re.split(r'<div class="rc-item">', content)
        for block in blocks:
            m_inst = re.search(r'<div class="name">\s*([^<]+?)\s*</div>', block)
            institute = _clean(m_inst.group(1)) if m_inst else ""
            for m in re.finditer(
                r'href="(https://www\.cs\.sjtu\.edu\.cn/jiaoshiml/[^"]+\.html)"[^>]*>\s*([^<]+?)\s*</a>',
                block,
            ):
                url = m.group(1)
                name = _clean(m.group(2))
                if name:
                    stubs.append(TeacherStub(name=name, detail_url=url, institute=institute))
        return stubs

    # -------------------------------------------------------------- 个人详情
    def fetch_teacher_detail(self, stub: TeacherStub) -> Teacher:
        t = Teacher(name=stub.name, detail_url=stub.detail_url, institute=stub.institute)
        try:
            resp = http_get(stub.detail_url, timeout=config.HTTP_TIMEOUT)
            resp.encoding = "utf-8"
            h = resp.text
        except Exception as e:
            log(f"[sjtu] 个人页抓取失败 {stub.name}: {e}")
            return t

        # 姓名 / 职称
        m = re.search(r'<div class="txt">\s*<div class="name">\s*([^<]+?)\s*</div>', h)
        if m:
            t.name = _clean(m.group(1)) or t.name
        m = re.search(r'<div class="zw">\s*([^<]*?)\s*</div>', h)
        if m:
            t.title = _clean(m.group(1))

        # dt 区：所在研究所 / 个人主页
        m = re.search(r'所在研究所[：:]\s*([^<]+)', h)
        if m and not t.institute:
            t.institute = _clean(m.group(1))
        m = re.search(r'个人主页[：:].*?href="([^"]+)"', h, re.S)
        if m:
            t.homepage = m.group(1).strip()
        # 邮箱（部分老师页面有）
        m = re.search(r'邮箱[：:]\s*([\w.\-]+@[\w.\-]+)', h)
        if not m:
            m = re.search(r'mailto:([\w.\-]+@[\w.\-]+)', h)
        if m:
            t.email = m.group(1)

        # 分节内容：个人简介 / 论文发表
        sections = self._parse_sections(h)
        t.bio = sections.get("个人简介", "")
        papers_raw = sections.get("论文发表", "")
        t.papers_listed = self._parse_listed_papers(papers_raw)
        return t

    def _parse_sections(self, h: str) -> dict:
        """提取 js-dt 下各 <div class="item"> 的 标题→正文。"""
        out: dict[str, str] = {}
        for m in re.finditer(
            r'<div class="name"><p>([^<]+)</p></div>\s*<div class="txt">(.*?)</div>\s*</div>',
            h, re.S,
        ):
            title = _clean(m.group(1))
            body = _clean(m.group(2))
            if title:
                out[title] = body
        return out

    def _parse_listed_papers(self, papers_raw: str) -> list[str]:
        """官网论文列表 → 标题数组（用 [ 会议 ] 作为分隔符切条）。"""
        if not papers_raw:
            return []
        # 形如：[ FAST ] Title. Authors. Venue, year. [ SOSP ] Title2 ...
        items = re.split(r'\[\s*[A-Za-z0-9&/\-\' ]+\s*\]', papers_raw)
        titles = []
        for it in items:
            it = it.strip(" .")
            if not it:
                continue
            # 取首句作为标题（到第一个 ". " 大写句点前），过长截断
            t = re.split(r'\.\s+[A-Z]', it, maxsplit=1)[0]
            t = t.strip(" .")
            if len(t) > 8:
                titles.append(t[:300])
        return titles[:15]
