# -*- coding: utf-8 -*-
"""中国科学技术大学计算机学院 解析器。

数据来源（已实测）：
- 列表页使用 USTC 标准 CMS（sudyCloud），UTF-8 编码
- 每个分类有独立 list URL，分页格式：list.htm / list2.htm / list3.htm ...
- 各 li.news 卡片含 div.news_title > a（姓名 + 详情链接），
  div.news_tel（邮箱），div.news_email（研究方向，可多行）
- 详情页 <div class="wp_articlecontent"> 含正文
  - 电话、E-Mail、个人主页/实验室主页（超链接或纯文本）均在正文
"""
from __future__ import annotations
import re
import html as htmllib

from schools.base import SchoolParser
from core.models import TeacherStub, Teacher
from core.utils import http_get, http_post, log, polite_sleep  # noqa: F401
import config

BASE_URL = "https://cs.ustc.edu.cn"

LIST_URLS: list[tuple[str, str]] = [
    ("https://cs.ustc.edu.cn/zgj_23225/list.htm", "正高级"),
    ("https://cs.ustc.edu.cn/js_23235/list.htm", "教授"),
    ("https://cs.ustc.edu.cn/trjs/list.htm", "特任教授"),
    ("https://cs.ustc.edu.cn/tryjy_23237/list.htm", "特任研究员"),
    ("https://cs.ustc.edu.cn/fjs_23239/list.htm", "副教授"),
    ("https://cs.ustc.edu.cn/trfyjy/list.htm", "特任副研究员"),
    ("https://cs.ustc.edu.cn/kzjs/list.htm", "客座/兼职"),
]

# 正高级页 (zgj_23225) 是一个聚合入口，实际教师数少，
# 但同时也出现在子分类中，由 seen 集合去重。


def _clean(s: str) -> str:
    """去 HTML 标签、转义实体、折叠空白。"""
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t\r\n\xa0　]+", " ", s)
    return s.strip()


def _make_abs(href: str) -> str:
    """把相对路径变为绝对 URL。"""
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return BASE_URL + href
    return BASE_URL + "/" + href


class UstcCsParser(SchoolParser):
    name = "ustc_cs"
    display_name = "中国科学技术大学计算机学院"
    output_dir_name = "中科大计算机导师联系"

    # ------------------------------------------------------------------ #
    #  列表抓取
    # ------------------------------------------------------------------ #

    def fetch_teacher_list(self) -> list[TeacherStub]:
        stubs: list[TeacherStub] = []
        seen: set[str] = set()          # 以 detail_url 去重
        for base_list_url, category in LIST_URLS:
            page = 1
            while True:
                if page == 1:
                    url = base_list_url
                else:
                    url = base_list_url.replace("list.htm", f"list{page}.htm")
                try:
                    resp = http_get(url, timeout=config.HTTP_TIMEOUT)
                    resp.encoding = "utf-8"
                    h = resp.text
                except Exception as e:
                    log(f"[ustc_cs] {category} 第{page}页获取失败: {e}")
                    break
                new_stubs = self._parse_list_html(h, seen, category)
                log(f"[ustc_cs] {category} 第{page}页 新增 {len(new_stubs)} 人")
                stubs.extend(new_stubs)
                # 如果本页没有新教师，停止翻页
                if not new_stubs:
                    break
                # 如果没有"下一页"链接，也停止
                if not self._has_next_page(h, page):
                    break
                page += 1
                if page > 20:
                    break
                polite_sleep(0.5)
        log(f"[ustc_cs] 列表抓取完毕，共 {len(stubs)} 人")
        return stubs

    def _parse_list_html(self, h: str, seen: set, category: str) -> list[TeacherStub]:
        """从列表页 HTML 解析 TeacherStub。

        HTML 结构（每位教师是一个 <li class="news ...">）：
          <div class="news_title"><a href='/yyyy/mm/dd/cXXXaYYY/page.htm' title='姓名'>姓名</a></div>
          <div class="news_tel">邮箱（有时是电话）</div>
          <div class="news_email">研究方向（可多个）</div>
        """
        stubs = []
        # 找到列表容器，截取相关段落，避免在导航菜单里误匹配
        # 列表容器标志：<ul class="news_list
        block_m = re.search(r'<ul class="news_list[^"]*">(.*?)</ul>', h, re.S)
        if not block_m:
            return stubs
        block = block_m.group(1)

        # 每个 <li class="news ..."> 块
        li_pattern = re.compile(r'<li class="news[^"]*">(.*?)</li>', re.S)
        for li_m in li_pattern.finditer(block):
            li_html = li_m.group(1)

            # 提取详情链接 + 姓名（从 news_title 里的 <a>）
            title_m = re.search(
                r'<div class="news_title">\s*<a\s+href=[\'"]([^\'"]+)[\'"][^>]*title=[\'"]([^\'"]+)[\'"]',
                li_html
            )
            if not title_m:
                # 备用：直接找第一个 <a> 带 title 属性的
                title_m = re.search(
                    r'<a\s+href=[\'"]([^\'"]+)[\'"][^>]*title=[\'"]([^\'"]+)[\'"]',
                    li_html
                )
            if not title_m:
                continue

            raw_href = title_m.group(1).strip()
            raw_name = title_m.group(2).strip()

            # 过滤掉明显非教师的链接（图片、主页等）
            if not raw_href.endswith("/page.htm"):
                continue

            detail_url = _make_abs(raw_href)
            name = _clean(raw_name)

            if not name or detail_url in seen:
                continue
            seen.add(detail_url)

            # 从 news_tel div 提取邮箱（有时放的是电话，有时是邮箱）
            tel_m = re.search(r'<div class="news_tel">(.*?)</div>', li_html, re.S)
            email_hint = _clean(tel_m.group(1)) if tel_m else ""

            # 从 news_email div 提取研究方向（多个 div 拼接）
            research_texts = re.findall(r'<div class="news_email">(.*?)</div>', li_html, re.S)
            research = "；".join(_clean(t) for t in research_texts if _clean(t))

            stub = TeacherStub(name=name, detail_url=detail_url, institute=f"中科大·计算机学院·{category}")
            # 把邮箱 hint 和研究方向暂存（detail 阶段优先用正文里的，这里作备用）
            stub._email_hint = email_hint          # type: ignore[attr-defined]
            stub._research_hint = research         # type: ignore[attr-defined]
            stubs.append(stub)
        return stubs

    @staticmethod
    def _has_next_page(h: str, current_page: int) -> bool:
        """检测页面中是否有"下一页"链接（USTC CMS 常用 list{N}.htm 格式）。"""
        next_page = current_page + 1
        # 形如 href="/xxx/list2.htm" 或 class="next"
        if re.search(rf'list{next_page}\.htm', h):
            return True
        if re.search(r'class=["\']next["\']', h):
            return True
        return False

    # ------------------------------------------------------------------ #
    #  详情页抓取
    # ------------------------------------------------------------------ #

    def fetch_teacher_detail(self, stub: TeacherStub) -> Teacher:
        t = Teacher(name=stub.name, detail_url=stub.detail_url, institute=stub.institute)

        # 先用列表页已有的邮箱 hint 填充
        email_hint = getattr(stub, "_email_hint", "")
        if email_hint and "@" in email_hint:
            t.email = email_hint

        try:
            resp = http_get(stub.detail_url, timeout=config.HTTP_TIMEOUT)
            resp.encoding = "utf-8"
            h = resp.text
        except Exception as e:
            log(f"[ustc_cs] 个人页获取失败 {stub.name}: {e}")
            return t

        self._parse_detail_html(h, t)
        return t

    def _parse_detail_html(self, h: str, t: Teacher) -> None:
        """从详情页 HTML 填充 Teacher 字段（原地修改 t）。"""
        # 去掉 script/style 干扰
        h2 = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S)

        # 定位正文区域 wp_articlecontent（最可靠）
        art_m = re.search(r"wp_articlecontent['\"]?>(.*?)(?:</div>\s*</div>\s*</div>|$)",
                          h2, re.S)
        body = art_m.group(1) if art_m else h2

        # ---- 邮箱 ----
        email_m = re.search(
            r"[Ee][-\s]?[Mm]ail[：:]\s*([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})",
            body
        )
        if email_m:
            t.email = email_m.group(1).strip()
        elif not t.email:
            # 尝试在整页里找邮箱
            email_m2 = re.search(
                r"\b([A-Za-z0-9._%+\-]+@(?:ustc|mail)\.edu\.cn)\b", h2
            )
            if email_m2:
                t.email = email_m2.group(1).strip()

        # ---- 个人主页（"个人主页" 或 "个人网页"）----
        homepage_m = re.search(
            r"个\s*人\s*主?页[^<]{0,10}<a[^>]+href=['\"]([^'\"]+)['\"]",
            body, re.S
        )
        if homepage_m:
            t.homepage = homepage_m.group(1).strip()
        else:
            # 备用：href 中含 staff.ustc.edu.cn 或 home.ustc.edu.cn
            hp_m2 = re.search(
                r'href=[\'"]([^\'"]*(?:staff|home)\.ustc\.edu\.cn[^\'"]*)[\'"]',
                body
            )
            if hp_m2:
                t.homepage = hp_m2.group(1).strip()

        # ---- 职称（从正文或 <title> 猜测）----
        title_m = re.search(
            r"(讲席教授|特任教授|特任副教授|特任研究员|特任副研究员"
            r"|教授|副教授|研究员|副研究员|讲师|助理教授|助理研究员|博士后)",
            body
        )
        if title_m:
            t.title = title_m.group(1)

        # ---- Bio（正文纯文本，截取前 2000 字） ----
        bio_text = _clean(body)
        if len(bio_text) > 80:
            t.bio = bio_text[:2000]

        # ---- 论文列表（ol/li 中看起来像论文的条目）----
        papers: list[str] = []
        li_texts = re.findall(r"<li[^>]*>(.*?)</li>", body, re.S)
        for li in li_texts:
            text = _clean(li)
            # 粗略判断是否像论文（含 arXiv/会议/期刊关键词，或有英文且长度 > 40）
            if len(text) > 40 and re.search(
                r"\b(AAAI|ICLR|NeurIPS|ICML|ACL|EMNLP|CVPR|ICCV|ECCV|KDD|SIGIR|WWW"
                r"|SIGMOD|VLDB|OSDI|SOSP|IEEE|ACM|Trans|Journal|Conference|arXiv)\b",
                text, re.I
            ):
                papers.append(text[:300])
        t.papers_listed = papers[:30]   # 最多保留 30 条
