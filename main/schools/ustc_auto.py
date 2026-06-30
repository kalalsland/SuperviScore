# -*- coding: utf-8 -*-
"""中国科学技术大学自动化学院 解析器。

数据来源（已实测）：
- 列表页使用 USTC sudyCloud CMS，UTF-8 编码
- 汇总页 /25970/list.htm 包含三个分类（正高/副高/其他），
  每个分类对应一个 <ul class="news_list clearfix"> 块
- 各 <li class="news nN clearfix"> 含单个 <a title="姓名" href="/yyyy/mm/cXXXaYYY/page.htm">
- 汇总页每一页内容相同（pagination 为装饰性），只需抓第一页
- 详情页 <div class='wp_articlecontent'> 含正文（邮箱、主页、职称、简介）
"""
from __future__ import annotations
import re
import html as htmllib

from schools.base import SchoolParser
from core.models import TeacherStub, Teacher
from core.utils import http_get, log, polite_sleep  # noqa: F401
import config

BASE_URL = "https://auto.ustc.edu.cn"

# 自动化系汇总页：一个页面内含正高/副高/其他三块
LIST_URLS: list[tuple[str, str]] = [
    ("https://auto.ustc.edu.cn/25970/list.htm", "自动化学院"),
]


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


class UstcAutoParser(SchoolParser):
    name = "ustc_auto"
    display_name = "中国科学技术大学自动化学院"
    output_dir_name = "中科大自动化导师联系"

    # ------------------------------------------------------------------ #
    #  列表抓取
    # ------------------------------------------------------------------ #

    def fetch_teacher_list(self) -> list[TeacherStub]:
        stubs: list[TeacherStub] = []
        seen: set[str] = set()          # 以 detail_url 去重
        for base_list_url, category in LIST_URLS:
            # 自动化系汇总页内容在所有分页中完全相同，只抓第一页即可
            try:
                resp = http_get(base_list_url, timeout=config.HTTP_TIMEOUT)
                resp.encoding = "utf-8"
                h = resp.text
            except Exception as e:
                log(f"[ustc_auto] {category} 获取失败: {e}")
                continue
            new_stubs = self._parse_list_html(h, seen, category)
            log(f"[ustc_auto] {category} 新增 {len(new_stubs)} 人")
            stubs.extend(new_stubs)
        log(f"[ustc_auto] 列表抓取完毕，共 {len(stubs)} 人")
        return stubs

    def _parse_list_html(self, h: str, seen: set, category: str) -> list[TeacherStub]:
        """从列表页 HTML 解析 TeacherStub。

        HTML 结构（每位教师是一个 <li class="news nN clearfix">）：
          <li class="news n1 clearfix">
            <a title="姓名" href="/yyyy/mm/cXXXaYYY/page.htm" target="_blank">
              <span class="szjt">></span><span class="szbt">姓名</span>
            </a>
          </li>

        页面同时包含正高/副高/其他三个 <ul class="news_list clearfix"> 块。
        """
        stubs = []
        # 找到所有列表块
        blocks = re.findall(r'<ul class="news_list[^"]*">(.*?)</ul>', h, re.S)
        if not blocks:
            return stubs

        li_pattern = re.compile(r'<li class="news[^"]*">(.*?)</li>', re.S)
        for block in blocks:
            for li_m in li_pattern.finditer(block):
                li_html = li_m.group(1)

                # 提取详情链接 + 姓名（从 <a title="姓名" href="...">）
                a_m = re.search(
                    r'<a\s[^>]*title=[\'"]([^\'"]+)[\'"][^>]*href=[\'"]([^\'"]+)[\'"]',
                    li_html
                )
                if not a_m:
                    a_m = re.search(
                        r'<a\s[^>]*href=[\'"]([^\'"]+)[\'"][^>]*title=[\'"]([^\'"]+)[\'"]',
                        li_html
                    )
                    if not a_m:
                        continue
                    raw_href = a_m.group(1).strip()
                    raw_name = a_m.group(2).strip()
                else:
                    raw_name = a_m.group(1).strip()
                    raw_href = a_m.group(2).strip()

                # 仅处理指向详情页的链接
                if not raw_href.endswith("/page.htm"):
                    continue

                detail_url = _make_abs(raw_href)
                name = _clean(raw_name)

                if not name or detail_url in seen:
                    continue
                seen.add(detail_url)

                stub = TeacherStub(
                    name=name,
                    detail_url=detail_url,
                    institute=f"中科大·自动化学院",
                )
                stubs.append(stub)
        return stubs

    # ------------------------------------------------------------------ #
    #  详情页抓取
    # ------------------------------------------------------------------ #

    def fetch_teacher_detail(self, stub: TeacherStub) -> Teacher:
        t = Teacher(name=stub.name, detail_url=stub.detail_url, institute=stub.institute)

        try:
            resp = http_get(stub.detail_url, timeout=config.HTTP_TIMEOUT)
            resp.encoding = "utf-8"
            h = resp.text
        except Exception as e:
            log(f"[ustc_auto] 个人页获取失败 {stub.name}: {e}")
            return t

        self._parse_detail_html(h, t)
        return t

    def _parse_detail_html(self, h: str, t: Teacher) -> None:
        """从详情页 HTML 填充 Teacher 字段（原地修改 t）。"""
        # 去掉 script/style 干扰
        h2 = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S)

        # 定位正文区域 wp_articlecontent
        art_m = re.search(
            r"wp_articlecontent['\"]?>(.*?)(?:</div>\s*</div>\s*</div>|$)",
            h2, re.S
        )
        body = art_m.group(1) if art_m else h2

        # ---- 邮箱 ----
        email_m = re.search(
            r"[Ee][-\s]?[Mm]ail[：:]\s*([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})",
            body
        )
        if not email_m:
            # 自动化系详情页有时写 "Email：xxx@xxx"
            email_m = re.search(
                r"邮\s*件\s*[：:]\s*([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})",
                body
            )
        if email_m:
            t.email = email_m.group(1).strip()
        else:
            # 尝试在整页找 mailto: 链接
            mailto_m = re.search(r'href="mailto:([^"]+)"', body)
            if mailto_m:
                t.email = mailto_m.group(1).strip()
            elif not t.email:
                email_m2 = re.search(
                    r"\b([A-Za-z0-9._%+\-]+@(?:ustc|mail|ia)\.(?:edu\.cn|ac\.cn))\b",
                    h2
                )
                if email_m2:
                    t.email = email_m2.group(1).strip()

        # ---- 个人主页 ----
        homepage_m = re.search(
            r"个\s*人\s*主?页[^<]{0,20}<a[^>]+href=['\"]([^'\"]+)['\"]",
            body, re.S
        )
        if homepage_m:
            t.homepage = homepage_m.group(1).strip()
        else:
            hp_m2 = re.search(
                r'href=[\'"]([^\'"]*(?:staff|home|faculty)\.ustc\.edu\.cn[^\'"]*)[\'"]',
                body
            )
            if hp_m2:
                t.homepage = hp_m2.group(1).strip()

        # ---- 职称 ----
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

        # ---- 论文列表 ----
        papers: list[str] = []
        li_texts = re.findall(r"<li[^>]*>(.*?)</li>", body, re.S)
        for li in li_texts:
            text = _clean(li)
            if len(text) > 40 and re.search(
                r"\b(AAAI|ICLR|NeurIPS|ICML|ACL|EMNLP|CVPR|ICCV|ECCV|KDD|SIGIR|WWW"
                r"|SIGMOD|VLDB|OSDI|SOSP|IEEE|ACM|Trans|Journal|Conference|arXiv)\b",
                text, re.I
            ):
                papers.append(text[:300])
        t.papers_listed = papers[:30]
