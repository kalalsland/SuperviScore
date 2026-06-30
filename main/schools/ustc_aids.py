# -*- coding: utf-8 -*-
"""中国科学技术大学人工智能与数据科学学院（SAIDS）解析器。

数据来源（已实测）：
- 列表页使用 USTC sudyCloud CMS，UTF-8 编码
- 三个分类有独立 list URL：
    zjs  → 正教授级（教授）
    fjs  → 副教授级（副教授）
    jzbd → 兼职博导
- 每个分类页结构：<ul class="wp_article_list"> 下含
  <li class="list_item iN"><span class='Article_Title'><a href='...' title='姓名'>
- 详情页 <div class='wp_articlecontent'> 含正文（邮件、主页、研究方向、简介）
"""
from __future__ import annotations
import re
import html as htmllib

from schools.base import SchoolParser
from core.models import TeacherStub, Teacher
from core.utils import http_get, log, polite_sleep  # noqa: F401
import config

BASE_URL = "https://saids.ustc.edu.cn"

LIST_URLS: list[tuple[str, str]] = [
    ("https://saids.ustc.edu.cn/zjs/list.htm", "正教授级"),
    ("https://saids.ustc.edu.cn/fjs/list.htm", "副教授级"),
    ("https://saids.ustc.edu.cn/jzbd/list.htm", "兼职博导"),
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


class UstcAidsParser(SchoolParser):
    name = "ustc_aids"
    display_name = "中国科学技术大学人工智能与数据科学学院"
    output_dir_name = "中科大SAIDS导师联系"

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
                    log(f"[ustc_aids] {category} 第{page}页获取失败: {e}")
                    break
                new_stubs = self._parse_list_html(h, seen, category)
                log(f"[ustc_aids] {category} 第{page}页 新增 {len(new_stubs)} 人")
                stubs.extend(new_stubs)
                if not new_stubs:
                    break
                if not self._has_next_page(h, page):
                    break
                page += 1
                if page > 20:
                    break
                polite_sleep(0.5)
        log(f"[ustc_aids] 列表抓取完毕，共 {len(stubs)} 人")
        return stubs

    def _parse_list_html(self, h: str, seen: set, category: str) -> list[TeacherStub]:
        """从列表页 HTML 解析 TeacherStub。

        HTML 结构（每位教师是一个 <li class="list_item iN">）：
          <li class="list_item i1">
            <div class="fields pr_fields">
              <span class='Article_Index'>1</span>
              <span class='Article_Title'>
                <a href='/yyyy/mm/cXXXaYYY/page.htm' target='_blank' title='姓名'>姓名</a>
              </span>
            </div>
            <div class="fields ex_fields">
              <span class='Article_PublishDate'>yyyy-mm-dd</span>
            </div>
          </li>
        """
        stubs = []
        # 找到列表容器
        block_m = re.search(r'<ul class="wp_article_list[^"]*">(.*?)</ul>', h, re.S)
        if not block_m:
            return stubs
        block = block_m.group(1)

        li_pattern = re.compile(r'<li class="list_item[^"]*">(.*?)</li>', re.S)
        for li_m in li_pattern.finditer(block):
            li_html = li_m.group(1)

            # 提取详情链接 + 姓名（从 Article_Title span 内的 <a>）
            a_m = re.search(
                r"<span[^>]*class=['\"]Article_Title['\"][^>]*>"
                r".*?<a\s[^>]*href=['\"]([^'\"]+)['\"][^>]*title=['\"]([^'\"]+)['\"]",
                li_html, re.S
            )
            if not a_m:
                # 备用：直接找带 title 属性的 <a>
                a_m = re.search(
                    r'<a\s[^>]*href=[\'"]([^\'"]+)[\'"][^>]*title=[\'"]([^\'"]+)[\'"]',
                    li_html
                )
            if not a_m:
                continue

            raw_href = a_m.group(1).strip()
            raw_name = a_m.group(2).strip()

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
                institute=f"中科大·SAIDS·{category}",
            )
            stubs.append(stub)
        return stubs

    @staticmethod
    def _has_next_page(h: str, current_page: int) -> bool:
        """检测页面中是否有下一页（USTC CMS 使用 listN.htm 格式）。"""
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
        t = Teacher(name=stub.name, detail_url=stub.detail_url, institute=stub.institute)

        try:
            resp = http_get(stub.detail_url, timeout=config.HTTP_TIMEOUT)
            resp.encoding = "utf-8"
            h = resp.text
        except Exception as e:
            log(f"[ustc_aids] 个人页获取失败 {stub.name}: {e}")
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
        # SAIDS 详情页格式示例：邮 件：yanyongz@ustc.edu.cn
        email_m = re.search(
            r"邮\s*件\s*[：:]\s*([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})",
            body
        )
        if not email_m:
            email_m = re.search(
                r"[Ee][-\s]?[Mm]ail[：:]\s*([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})",
                body
            )
        if email_m:
            t.email = email_m.group(1).strip()
        else:
            # 尝试 mailto: 链接
            mailto_m = re.search(r'href="mailto:([^"]+)"', body)
            if mailto_m:
                t.email = mailto_m.group(1).strip()
            elif not t.email:
                email_m2 = re.search(
                    r"\b([A-Za-z0-9._%+\-]+@(?:ustc|mail)\.edu\.cn)\b",
                    h2
                )
                if email_m2:
                    t.email = email_m2.group(1).strip()

        # ---- 个人主页 ----
        # SAIDS 详情页格式：个人主页：<a href="...">...</a>
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

        # ---- Bio（正文纯文本，截取前 2000 字；包含研究方向等信息） ----
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
