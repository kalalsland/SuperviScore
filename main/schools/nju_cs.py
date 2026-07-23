# -*- coding: utf-8 -*-
"""南京大学计算机学院 解析器。

数据来源（已实测）：
- 列表页使用南大标准 CMS（UTF-8），每个职称分类独立 URL
- 列表结构：<ul class="wp_article_list"> / <li class="list_item iN">
  每条目含 <span class='Article_Title'><a href='...' title='姓名'>姓名</a>
- 所有条目均在第一页，无翻页（list2.htm 为空）
- 详情页 <div class='wp_articlecontent'> 下：
    <div class="detail"> 含 bio 文本
    <div class="other"> 含 <span>电话：...</span> <span>电子邮件：xxx<span>@</span>nju.edu.cn</span>
  邮箱中 @ 被 <span style="color:red;">@</span> 包裹（反爬混淆），解析时需处理
"""
from __future__ import annotations
import re
import html as htmllib

from schools.base import SchoolParser
from core.models import TeacherStub, Teacher
from core.utils import http_get, log, polite_sleep  # noqa: F401
import config

BASE_URL = "https://cs.nju.edu.cn"

LIST_URLS: list[tuple[str, str]] = [
    ("https://cs.nju.edu.cn/2639/list.htm", "教授"),
    ("https://cs.nju.edu.cn/2640/list.htm", "副教授"),
    ("https://cs.nju.edu.cn/zzp/list.htm", "正职人员"),
    ("https://cs.nju.edu.cn/kxkbd/list.htm", "客座/兼职"),
    ("https://cs.nju.edu.cn/2641/list.htm", "助理教授"),
    ("https://cs.nju.edu.cn/2645/list.htm", "研究员"),
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


def _deobfuscate_email(raw: str) -> str:
    """还原被 <span>@</span> 混淆的邮箱地址。"""
    # 把 <span ...>@</span> 替换为普通 @，再去标签
    raw = re.sub(r"<span[^>]*>@</span>", "@", raw)
    return _clean(raw)


class NjuCsParser(SchoolParser):
    name = "nju_cs"
    display_name = "南京大学计算机学院"
    output_dir_name = "南大计算机导师联系"

    # ------------------------------------------------------------------ #
    #  列表抓取
    # ------------------------------------------------------------------ #

    def fetch_teacher_list(self) -> list[TeacherStub]:
        stubs: list[TeacherStub] = []
        seen: set[str] = set()  # 以 detail_url 去重
        for list_url, category in LIST_URLS:
            try:
                resp = http_get(list_url, timeout=config.HTTP_TIMEOUT)
                resp.encoding = "utf-8"
                h = resp.text
            except Exception as e:
                log(f"[nju_cs] {category} 列表获取失败: {e}")
                continue
            new_stubs = self._parse_list_html(h, seen, category)
            log(f"[nju_cs] {category} 新增 {len(new_stubs)} 人")
            stubs.extend(new_stubs)
            polite_sleep(0.5)
        log(f"[nju_cs] 列表抓取完毕，共 {len(stubs)} 人")
        return stubs

    def _parse_list_html(self, h: str, seen: set, category: str) -> list[TeacherStub]:
        """从列表页 HTML 解析 TeacherStub。

        HTML 结构：
          <ul class="wp_article_list">
            <li class="list_item iN">
              <div class="fields pr_fields">
                <span class='Article_Index'>N</span>
                <span class='Article_Title'>
                  <a href='/xx/yy/cZZZaWWW/page.htm' target='_blank' title='姓名'>姓名</a>
                </span>
              </div>
            </li>
          </ul>
        """
        stubs = []

        # 定位列表容器，避免在导航区误匹配
        block_m = re.search(r'<ul class="wp_article_list">(.*?)</ul>', h, re.S)
        if not block_m:
            return stubs
        block = block_m.group(1)

        # 每个 <li class="list_item ..."> 块
        li_pattern = re.compile(r'<li class="list_item[^"]*">(.*?)</li>', re.S)
        for li_m in li_pattern.finditer(block):
            li_html = li_m.group(1)

            # 提取 href + title（姓名）
            a_m = re.search(
                r"<a\s+href=['\"]([^'\"]+)['\"][^>]*title=['\"]([^'\"]+)['\"]",
                li_html
            )
            if not a_m:
                continue

            raw_href = a_m.group(1).strip()
            raw_name = a_m.group(2).strip()

            detail_url = _make_abs(raw_href)
            # 清理姓名：去掉括号内的附加说明（如"院士、博导"），保留姓名主体
            name_clean = _clean(raw_name)
            # 提取姓名主体（括号前部分），支持中文全角/半角括号
            name_main = re.split(r"[（(]", name_clean)[0].strip()
            name = name_main if name_main else name_clean

            if not name or not detail_url:
                continue
            if detail_url in seen:
                continue
            seen.add(detail_url)

            stub = TeacherStub(
                name=name,
                detail_url=detail_url,
                institute=f"南京大学·计算机学院·{category}",
            )
            stubs.append(stub)
        return stubs

    # ------------------------------------------------------------------ #
    #  详情页抓取
    # ------------------------------------------------------------------ #

    def fetch_teacher_detail(self, stub: TeacherStub) -> Teacher:
        t = Teacher(name=stub.name, detail_url=stub.detail_url, institute=stub.institute)

        # 用列表分类预填职称（如"教授"/"副教授"/"研究员"等）
        parts = stub.institute.split("·")
        if len(parts) >= 3:
            t.title = parts[-1]

        try:
            resp = http_get(stub.detail_url, timeout=config.HTTP_TIMEOUT)
            resp.encoding = "utf-8"
            h = resp.text
        except Exception as e:
            log(f"[nju_cs] 个人页获取失败 {stub.name}: {e}")
            return t

        self._parse_detail_html(h, t)
        return t

    def _parse_detail_html(self, h: str, t: Teacher) -> None:
        """从详情页 HTML 填充 Teacher 字段（原地修改 t）。

        详情页结构（wp_articlecontent 内）：
          <div class="content">
            <img .../>
            <div class="detail">Bio 文本</div>
          </div>
          <div class="other">
            <span>电话：XXXXXXXX</span>
            <span>电子邮件：user<span style="color:red;">@</span>nju.edu.cn</span>
          </div>
        """
        # 去掉 script/style 干扰
        h2 = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S)

        # 定位正文区域
        art_m = re.search(r"wp_articlecontent['\"]?>(.*?)(?=</div>\s*</div>\s*</div>|$)",
                          h2, re.S)
        body = art_m.group(1) if art_m else h2

        # ---- Bio（<div class="detail"> 内的纯文本）----
        detail_m = re.search(r'<div\s+class=[\'"]detail[\'"]>(.*?)</div>', body, re.S)
        if detail_m:
            bio_text = _clean(detail_m.group(1))
            if len(bio_text) > 20:
                t.bio = bio_text[:2000]

        # ---- other 区域（电话 + 邮箱）----
        other_m = re.search(r'<div\s+class=[\'"]other[\'"]>(.*?)</div>', body, re.S)
        if other_m:
            other_html = other_m.group(1)

            # 邮箱：先还原混淆的 @，再提取
            email_raw = _deobfuscate_email(other_html)
            email_m = re.search(
                r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
                email_raw
            )
            if email_m:
                t.email = email_m.group(0).strip()

        # ---- 邮箱备用（全页搜索，应对少数格式异常页面）----
        if not t.email:
            h_deob = _deobfuscate_email(h2)
            email_m2 = re.search(
                r"\b([A-Za-z0-9._%+\-]+@(?:[A-Za-z0-9.\-]*\.)?nju\.edu\.cn)\b",
                h_deob
            )
            if email_m2:
                t.email = email_m2.group(1).strip()

        # ---- 个人主页（正文中的超链接，排除站内导航链接）----
        # 查找正文里指向外部或个人主页的链接
        homepage_m = re.search(
            r"(?:个\s*人\s*主?页|个\s*人\s*网\s*站|Homepage)[^<]{0,20}"
            r"<a[^>]+href=['\"]([^'\"]+)['\"]",
            body, re.S | re.I
        )
        if homepage_m:
            t.homepage = homepage_m.group(1).strip()
        else:
            # 备用：href 含 cs.nju.edu.cn 以外的 .edu.cn 或个人域名
            hp_m2 = re.search(
                r'href=[\'"]'
                r'(https?://(?!cs\.nju\.edu\.cn|www\.nju\.edu\.cn)[^\'"]+)'
                r'[\'"]',
                body
            )
            if hp_m2:
                candidate = hp_m2.group(1).strip()
                # 排除常见非个人主页域名
                if not re.search(
                    r"(keysoftlab|cselab|software\.nju|cc\.nju|jw\.nju"
                    r"|nju\.edu\.cn/info|weibo\.com|wechat|qq\.com)",
                    candidate
                ):
                    t.homepage = candidate

        # ---- 职称（从 Bio 或页面标题推断）----
        if not t.title:
            title_src = (t.bio or "") + _clean(body[:500])
            title_m = re.search(
                r"(讲席教授|特任教授|特任副教授|特任研究员|特任副研究员"
                r"|教授|副教授|研究员|副研究员|讲师|助理教授|助理研究员|博士后)",
                title_src
            )
            if title_m:
                t.title = title_m.group(1)

        # ---- 论文列表（ol/li 中像论文的条目）----
        papers: list[str] = []
        li_texts = re.findall(r"<li[^>]*>(.*?)</li>", body, re.S)
        for li in li_texts:
            text = _clean(li)
            if len(text) > 40 and re.search(
                r"\b(AAAI|ICLR|NeurIPS|ICML|ACL|EMNLP|NAACL|CVPR|ICCV|ECCV"
                r"|KDD|SIGIR|WWW|SIGMOD|VLDB|OSDI|SOSP|NDSI|USENIX"
                r"|IEEE|ACM|Trans|Journal|Conference|arXiv)\b",
                text, re.I
            ):
                papers.append(text[:300])
        t.papers_listed = papers[:30]
