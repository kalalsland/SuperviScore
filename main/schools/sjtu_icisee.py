# -*- coding: utf-8 -*-
"""上海交通大学集成电路学院（信息与电子工程学院） 解析器。

数据来源（已实测）：
- 列表：POST https://icisee.sjtu.edu.cn/active/ajax_teacher_list.html
        参数 page / cat_code=jiaoshiml / yjszxfl=全部 / name / zm=All
        → JSON {content, page, count}，count=268（约）
        HTML 结构：<li><a href=".../jiaoshiml/X.html">
                    <div class="imgk">…</div>
                    <div class="name">姓名<span>职称</span><p>研究所</p></div>
                  </a></li>
        支持真实分页（每页 10 人，共约 27 页），需逐页遍历
- 个人页：GET https://icisee.sjtu.edu.cn/jiaoshiml/X.html（UTF-8）
        头部 <div class="js-info">：
          <div class="tit"><p>姓名</p><span>职称</span></div>
          <div class="detail"><p>研究所|电话|邮箱|个人主页…</p></div>
        内容区多个 <div class="js-item">：
          <div class="tit">标题</div>
          <div class="detail"><div class="txt"><p>内容</p></div></div>
"""
from __future__ import annotations
import re
import html as htmllib

from schools.base import SchoolParser
from core.models import TeacherStub, Teacher
from core.utils import http_get, http_post, log, polite_sleep
import config

LIST_API = "https://icisee.sjtu.edu.cn/active/ajax_teacher_list.html"
DETAIL_PREFIX = "https://icisee.sjtu.edu.cn/jiaoshiml/"


def _clean(s: str) -> str:
    """去 HTML 标签、解实体、压空白。"""
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t\r\n\xa0　]+", " ", s)
    return s.strip()


class SjtuIciseeParser(SchoolParser):
    name = "sjtu_icisee"
    display_name = "上海交通大学集成电路学院（信息与电子工程学院）"
    output_dir_name = "交大集成电路导师联系"

    # ---------------------------------------------------------------------- 列表
    def fetch_teacher_list(self) -> list[TeacherStub]:
        stubs: list[TeacherStub] = []
        seen: set[str] = set()
        page = 1
        while True:
            data = {
                "page": str(page),
                "cat_code": "jiaoshiml",
                "yjszxfl": "全部",
                "name": "",
                "zm": "All",
            }
            try:
                resp = http_post(
                    LIST_API, data=data, timeout=config.HTTP_TIMEOUT,
                    headers={
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": "https://icisee.sjtu.edu.cn/jiaoshiml.html",
                    },
                )
                payload = resp.json()
            except Exception as e:
                log(f"[sjtu_icisee] 列表第 {page} 页抓取失败: {e}")
                break

            content = payload.get("content", "") or ""
            total_count = int(payload.get("count", 0) or 0)
            new_stubs = self._parse_list_html(content, seen)
            log(f"[sjtu_icisee] 列表第 {page} 页：新增 {len(new_stubs)} 人（累计 {len(stubs) + len(new_stubs)}/{total_count}）")
            stubs.extend(new_stubs)

            if not new_stubs:
                break
            if total_count and len(stubs) >= total_count:
                break
            page += 1
            if page > 50:  # 安全上限
                break
            polite_sleep(0.5)
        return stubs

    def _parse_list_html(self, content: str, seen: set[str]) -> list[TeacherStub]:
        """从列表 HTML 提取教师。
        结构（每条）：
          <a href="https://icisee.sjtu.edu.cn/jiaoshiml/X.html">
            <div class="imgk">…</div>
            <div class="name">姓名<span>职称</span><p class="line-2">研究所</p></div>
          </a>
        """
        stubs: list[TeacherStub] = []
        # 先提取所有 <a href> 块（含 class="name" div）
        for m in re.finditer(
            r'<a\s[^>]*href="(https://icisee\.sjtu\.edu\.cn/jiaoshiml/[^"]+\.html)"[^>]*>\s*'
            r'(?:<div class="imgk">.*?</div>\s*)?'
            r'<div class="name">\s*([^<]+)',
            content, re.S,
        ):
            url = m.group(1).strip()
            name_raw = m.group(2)
            name = _clean(name_raw.split("<")[0])
            if not name or url in seen:
                continue
            seen.add(url)

            # 提取职称和研究所（从同一 <a> 块内的 <span> 和 <p>）
            # 简单从 <a> 块抓 span 和 p
            block_start = m.start()
            block_end = min(block_start + 600, len(content))
            block = content[block_start:block_end]
            title = ""
            institute = ""
            sm = re.search(r'<span[^>]*>\s*([^<]+?)\s*</span>', block)
            if sm:
                title = _clean(sm.group(1))
            pm = re.search(r'<p[^>]*>\s*([^<]+?)\s*</p>', block)
            if pm:
                institute = _clean(pm.group(1))

            st = TeacherStub(
                name=name,
                detail_url=url,
                institute=institute or "集成电路学院（信息与电子工程学院）",
            )
            st._title = title  # type: ignore[attr-defined]
            stubs.append(st)
        return stubs

    # ------------------------------------------------------------------ 个人详情
    def fetch_teacher_detail(self, stub: TeacherStub) -> Teacher:
        t = Teacher(name=stub.name, detail_url=stub.detail_url, institute=stub.institute)
        t.title = getattr(stub, "_title", "") or ""

        try:
            resp = http_get(stub.detail_url, timeout=config.HTTP_TIMEOUT)
            resp.encoding = "utf-8"
            h = resp.text
        except Exception as e:
            log(f"[sjtu_icisee] 个人页抓取失败 {stub.name}: {e}")
            return t

        # ---- 基本信息（js-info）----
        m = re.search(r'<div class="tit">\s*<p>\s*([^<]+?)\s*</p>', h)
        if m:
            t.name = _clean(m.group(1)) or t.name

        m = re.search(r'<div class="tit">\s*<p>[^<]*</p>\s*<span>\s*([^<]+?)\s*</span>', h)
        if m and not t.title:
            t.title = _clean(m.group(1))

        # detail 区：研究所、电话、邮箱、个人主页
        m_info = re.search(r'<div class="js-info">(.*?)<div class="js-(?:list|item|box)', h, re.S)
        detail_html = m_info.group(1) if m_info else h[:3000]

        em = re.search(r'邮\s*[箱件][：:]\s*([\w.\-+]+@[\w.\-]+)', detail_html)
        if not em:
            em = re.search(r'([\w.\-+]+@sjtu\.edu\.cn)', detail_html)
        if em:
            t.email = em.group(1).strip()

        hp = re.search(r'(https?://[^\s<>"，。]+)', detail_html)
        if hp:
            url_cand = hp.group(1).rstrip(".,;")
            if "icisee.sjtu.edu.cn/jiaoshiml" not in url_cand:
                t.homepage = url_cand

        # ---- 分节内容（js-item）----
        sections = self._parse_sections(h)

        bio_keys = ["个人简介", "研究方向", "研究兴趣"]
        for k in bio_keys:
            if sections.get(k):
                t.bio = sections[k]
                break

        paper_keys = ["代表性论著", "学术论文", "代表性论文", "科研成果", "研究成果"]
        for k in paper_keys:
            if sections.get(k):
                t.papers_listed = self._parse_papers(sections[k])
                break

        return t

    def _parse_sections(self, h: str) -> dict[str, str]:
        """提取 js-item 各节。
        ICISEE 结构：
          <div class="js-item">
            <div class="tit">研究方向</div>
            <div class="detail"><div class="txt"><p>内容</p></div></div>
          </div>
        """
        out: dict[str, str] = {}
        for m in re.finditer(
            r'<div class="js-item">\s*<div class="tit">\s*([^<]+?)\s*</div>'
            r'\s*<div class="detail">\s*<div class="txt">(.*?)</div>\s*</div>\s*</div>',
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
