# -*- coding: utf-8 -*-
"""上海交通大学电气工程学院 解析器。

数据来源（已实测）：
- 列表：POST https://see.sjtu.edu.cn/active/ajax_teacher_list.html
        参数 page / cat_id=40 / cat_code=jiaoshiml / type=1（或 type=2）→ JSON.content 为 HTML
        HTML 结构：<div class="name-list"><div class="name"><span>字母</span></div>
                    <div class="name-item"><li><a href=".../jiaoshiml/X.html">姓名</a></li></div>
        一次性返回全部（约 157 人，type=1 时），无分页
- 个人页：GET https://see.sjtu.edu.cn/jiaoshiml/X.html（UTF-8）
        头部 <div class="js-info">：
          <div class="tit"><p>姓名</p><span>职称</span></div>
          <div class="detail"><p>电话|邮箱|地址…</p></div>
        内容区 <div class="js-box"> 内多个 <div class="js-box-item">：
          <div class="name">标题</div><div class="txt"><p>内容</p></div>
"""
from __future__ import annotations
import re
import html as htmllib

from schools.base import SchoolParser
from core.models import TeacherStub, Teacher
from core.utils import http_get, http_post, log, polite_sleep
import config

LIST_API = "https://see.sjtu.edu.cn/active/ajax_teacher_list.html"
DETAIL_PREFIX = "https://see.sjtu.edu.cn/jiaoshiml/"


def _clean(s: str) -> str:
    """去 HTML 标签、解实体、压空白。"""
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t\r\n\xa0　]+", " ", s)
    return s.strip()


class SjtuSeeParser(SchoolParser):
    name = "sjtu_see"
    display_name = "上海交通大学电气工程学院"
    output_dir_name = "交大电气导师联系"

    # ---------------------------------------------------------------------- 列表
    def fetch_teacher_list(self) -> list[TeacherStub]:
        stubs: list[TeacherStub] = []
        seen: set[str] = set()
        page = 1
        while True:
            data = {
                "page": page,
                "cat_id": "40",
                "cat_code": "jiaoshiml",
                "type": "1",
            }
            try:
                resp = http_post(
                    LIST_API, data=data, timeout=config.HTTP_TIMEOUT,
                    headers={"X-Requested-With": "XMLHttpRequest"},
                )
                payload = resp.json()
            except Exception as e:
                log(f"[sjtu_see] 列表第 {page} 页抓取失败: {e}")
                break

            content = payload.get("content", "") or ""
            new_stubs = self._parse_list_html(content, seen)
            log(f"[sjtu_see] 列表第 {page} 页：新增 {len(new_stubs)} 人（累计 {len(stubs) + len(new_stubs)}）")
            stubs.extend(new_stubs)

            # 该接口一次性返回全部，首页无新增则停止
            if not new_stubs or page >= 2:
                break
            page += 1
            polite_sleep(0.5)
        return stubs

    def _parse_list_html(self, content: str, seen: set[str]) -> list[TeacherStub]:
        """从 <div class="name-item"> 内的 <li><a href="...">姓名</a></li> 提取教师。
        结构：
          <div class="name-list">
            <div class="name"><span>A</span></div>
            <div class="name-item">
              <li><a href="https://see.sjtu.edu.cn/jiaoshiml/aiqian.html">姓名</a></li>
            </div>
          </div>
        """
        stubs: list[TeacherStub] = []
        for m in re.finditer(
            r'<a\s[^>]*href="(https://see\.sjtu\.edu\.cn/jiaoshiml/[^"]+\.html)"[^>]*>\s*([^<]+?)\s*</a>',
            content,
        ):
            url = m.group(1).strip()
            name = _clean(m.group(2))
            if not name or url in seen:
                continue
            seen.add(url)
            stubs.append(TeacherStub(name=name, detail_url=url, institute="电气工程学院"))
        return stubs

    # ------------------------------------------------------------------ 个人详情
    def fetch_teacher_detail(self, stub: TeacherStub) -> Teacher:
        t = Teacher(name=stub.name, detail_url=stub.detail_url, institute=stub.institute)
        try:
            resp = http_get(stub.detail_url, timeout=config.HTTP_TIMEOUT)
            resp.encoding = "utf-8"
            h = resp.text
        except Exception as e:
            log(f"[sjtu_see] 个人页抓取失败 {stub.name}: {e}")
            return t

        # ---- 基本信息（js-info）----
        # js-info 块
        m_info = re.search(r'<div class="js-info">(.*?)</div>\s*</div>\s*</div>\s*<div class="js-box">', h, re.S)
        if not m_info:
            # 宽松匹配
            m_info = re.search(r'<div class="js-info">(.*?)<div class="js-box', h, re.S)
        info_html = m_info.group(1) if m_info else h[:3000]

        # 姓名
        m = re.search(r'<div class="tit">\s*<p>\s*([^<]+?)\s*</p>', info_html)
        if m:
            t.name = _clean(m.group(1)) or t.name

        # 职称
        m = re.search(r'<div class="tit">\s*<p>[^<]*</p>\s*<span>\s*([^<]+?)\s*</span>', info_html)
        if m:
            t.title = _clean(m.group(1))

        # detail：邮箱、电话、地址、个人主页
        m_detail = re.search(r'<div class="detail">(.*?)</div>', info_html, re.S)
        if m_detail:
            detail_html = m_detail.group(1)
            em = re.search(r'邮\s*[箱件][：:]\s*([\w.\-+]+@[\w.\-]+)', detail_html)
            if not em:
                em = re.search(r'([\w.\-+]+@sjtu\.edu\.cn)', detail_html)
            if em:
                t.email = em.group(1).strip()
            hp = re.search(r'(https?://[^\s<>"，。]+)', detail_html)
            if hp:
                url_cand = hp.group(1).rstrip(".,;")
                if "see.sjtu.edu.cn/jiaoshiml" not in url_cand:
                    t.homepage = url_cand

        # ---- 分节内容（js-box-item）----
        sections = self._parse_sections(h)

        bio_keys = ["个人简介", "研究方向", "研究兴趣"]
        for k in bio_keys:
            if sections.get(k):
                t.bio = sections[k]
                break

        paper_keys = ["学术论文", "代表性论文", "科研成果", "研究成果"]
        for k in paper_keys:
            if sections.get(k):
                t.papers_listed = self._parse_papers(sections[k])
                break

        return t

    def _parse_sections(self, h: str) -> dict[str, str]:
        """提取 js-box-item 各节：<div class="name">标题</div><div class="txt">内容</div>。"""
        out: dict[str, str] = {}
        for m in re.finditer(
            r'<div class="js-box-item">\s*<div class="name">\s*([^<]+?)\s*</div>\s*<div class="txt">(.*?)</div>\s*</div>',
            h, re.S,
        ):
            title = _clean(m.group(1))
            body = _clean(m.group(2))
            if title:
                out[title] = body
        return out

    def _parse_papers(self, raw: str) -> list[str]:
        """从论文段落提取标题（按换行/编号分割）。"""
        if not raw:
            return []
        items = re.split(r'\n+|\r\n+', raw)
        titles: list[str] = []
        for item in items:
            item = re.sub(r'^[\[\（\(]?\d+[\]\）\)][.\s]*', '', item.strip(" ."))
            if len(item) > 10:
                titles.append(item[:300])
        return titles[:15]
