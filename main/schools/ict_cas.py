# -*- coding: utf-8 -*-
"""中国科学院计算技术研究所 解析器。

数据来源：
- 博士生导师列表：http://www.ict.cas.cn/yjsjy/dsjj/bd/
  encoding: gb18030；113 人；单页无分页
  列表结构：<li class="...avatar-box..."> → <a href="../../../sourcedb/...">姓名</a>
- 详情页：http://www.ict.cas.cn/sourcedb/cn/jssrck/...html
  <dd class="swwls_imgtextlist_dd"> 内含 h5（姓名+职称）和 p.p-people-content 系列字段
  <h3 id="jl">  简历正文
  <h3 id="dblz"> 主要论著
"""
from __future__ import annotations
import re
import html as htmllib
from urllib.parse import urljoin

from schools.base import SchoolParser
from core.models import TeacherStub, Teacher
from core.utils import http_get, log, polite_sleep
import config

BASE_URL = "http://www.ict.cas.cn"
LIST_URL = "http://www.ict.cas.cn/yjsjy/dsjj/bd/"


def _fetch_page(url: str) -> str:
    """抓页面并正确解码（CAS 计算所页面声明 UTF-8，实际也是 UTF-8）。"""
    resp = http_get(url, timeout=config.HTTP_TIMEOUT, retries=3, backoff=4.0,
                    headers={"User-Agent": "Mozilla/5.0"})
    # 页面 <meta charset="utf-8">，Content-Type 可能不带 charset 导致 requests
    # 默认 ISO-8859-1；强制用 UTF-8 解码。
    resp.encoding = "utf-8"
    return resp.text


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t\r\n\xa0　]+", " ", s)
    return s.strip()


def _abs(href: str, base: str = BASE_URL) -> str:
    if not href:
        return ""
    return urljoin(base, href)


class IctCasParser(SchoolParser):
    name = "ict_cas"
    display_name = "中国科学院计算技术研究所"
    output_dir_name = "中科院计算所导师联系"

    def fetch_teacher_list(self) -> list[TeacherStub]:
        log(f"[ict_cas] 抓列表 {LIST_URL}")
        html = _fetch_page(LIST_URL)

        # <li class="...avatar-box..."> ... <a href="...">姓名</a> ... </li>
        items = re.findall(
            r'<li[^>]+avatar-box[^>]*>(.*?)</li>', html, re.DOTALL)
        log(f"[ict_cas] 找到 {len(items)} 个条目")

        stubs: list[TeacherStub] = []
        seen: set[str] = set()
        for item in items:
            # 所有 href，第一个是图片+名字的外层 <a>，内容里还有第二个同样 href 的 <a>
            hrefs = re.findall(r'href=["\']([^"\']+)["\']', item)
            if not hrefs:
                continue
            href = hrefs[0]
            detail_url = _abs(href, LIST_URL)
            if detail_url in seen:
                continue
            seen.add(detail_url)

            # 名字：第二个 <a> 的文本（不含 img）
            name_matches = re.findall(r'<a[^>]+href=["\'][^"\']+["\'][^>]*>([^<]{2,8})</a>', item)
            name = _clean(name_matches[-1]) if name_matches else ""
            if not name:
                continue

            stubs.append(TeacherStub(name=name, detail_url=detail_url, institute=""))
            polite_sleep(0)

        log(f"[ict_cas] 共 {len(stubs)} 位导师")
        return stubs

    def fetch_teacher_detail(self, stub: TeacherStub) -> Teacher:
        t = Teacher(name=stub.name, detail_url=stub.detail_url)
        try:
            html = _fetch_page(stub.detail_url)
            polite_sleep(getattr(config, "HTTP_SLEEP", 1.0))

            # ── 卡片区 dd.swwls_imgtextlist_dd ──────────────────────────────
            dd_m = re.search(r'<dd[^>]+swwls_imgtextlist_dd[^>]*>(.*?)</dd>',
                             html, re.DOTALL)
            if dd_m:
                dd = dd_m.group(1)

                # 姓名 + 职称 from <h5><span>姓名  职称  </span></h5>
                h5_m = re.search(r'<h5[^>]*><span[^>]*>(.*?)</span>', dd, re.DOTALL)
                if h5_m:
                    parts = [p.strip() for p in _clean(h5_m.group(1)).split() if p.strip()]
                    if len(parts) >= 2:
                        t.name = parts[0]
                        t.title = " ".join(parts[1:])
                    elif len(parts) == 1:
                        t.name = parts[0]

                def _field(bid: str) -> str:
                    """从 <b id="bid">标签：</b><span>内容</span> 提取内容。"""
                    m = re.search(
                        rf'<b[^>]+id=["\']?{bid}["\']?[^>]*>[^<]*</b>\s*<span[^>]*>(.*?)</span>',
                        dd, re.DOTALL)
                    return _clean(m.group(1)) if m else ""

                research = _field("yj") or _field("yjfx")
                t.institute = _field("ssbm")
                # 导师类别写进 title 补充
                advisor_type = _field("qtbz")
                if advisor_type and advisor_type not in t.title:
                    t.title = f"{t.title}（{advisor_type}）" if t.title else advisor_type

                contact = _field("dzyj")
                # 提取邮箱
                email_m = re.search(r'[\w.+-]+@[\w.-]+\.\w+', contact)
                if email_m:
                    t.email = email_m.group(0)

                # 个人主页
                hp_m = re.search(
                    r'<b[^>]+id=["\']?grwy["\']?[^>]*>[^<]*</b>\s*<span[^>]*>(.*?)</span>',
                    dd, re.DOTALL)
                if hp_m:
                    a_m = re.search(r'href=["\']([^"\']+)["\']', hp_m.group(1))
                    if a_m and a_m.group(1) not in ("", "#"):
                        t.homepage = a_m.group(1)

            # ── 简历 <h3 id="jl"> ────────────────────────────────────────────
            jl_m = re.search(r'id=["\']jl["\'][^>]*>.*?</h3>\s*<div[^>]*>(.*?)</div>\s*</div>',
                             html, re.DOTALL)
            bio_parts = []
            if research:
                bio_parts.append(f"研究方向：{research}")
            if jl_m:
                bio_parts.append(_clean(jl_m.group(1))[:800])
            t.bio = "\n".join(bio_parts)

            # ── 主要论著 <h3 id="dblz"> ─────────────────────────────────────
            dblz_m = re.search(r'id=["\']dblz["\'][^>]*>.*?</h3>\s*<div[^>]*>(.*?)</div>\s*</div>',
                               html, re.DOTALL)
            if dblz_m:
                raw = _clean(dblz_m.group(1))
                # 按数字编号分割论文条目
                entries = re.split(r'(?<!\d)(\d{1,2})\.\s+', raw)
                papers = []
                for i in range(1, len(entries), 2):
                    title = entries[i + 1].strip() if i + 1 < len(entries) else ""
                    if title and len(title) > 10:
                        papers.append(title[:200])
                t.papers_listed = papers[:10]

        except Exception as e:
            log(f"[ict_cas] 详情抓取失败 {stub.name}: {e}")
        return t
