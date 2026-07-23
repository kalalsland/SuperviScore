# -*- coding: utf-8 -*-
"""南京大学软件学院 解析器。

数据来源（已实测）：
- 列表页：https://software.nju.edu.cn//szll/szdw/index.html
  单页，分"教授"和"副教授、高级工程师"两张 <table>，每个 <td> 内含
  <a href="https://software.nju.edu.cn//USERNAME/index.html" style="color:#333;">姓名</a>
- 个人页（software.nju.edu.cn 子路径）：
  左侧 class="mc" 含邮箱/电话/地址；
  右侧 id="aRight" 的 <div class="middle clearfix"> 含正文简介。
"""
from __future__ import annotations
import re
import html as htmllib

from schools.base import SchoolParser
from core.models import TeacherStub, Teacher
from core.utils import http_get, log, polite_sleep
import config

BASE_URL = "https://software.nju.edu.cn"
LIST_URL = "https://software.nju.edu.cn//szll/szdw/index.html"


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t\r\n\xa0　]+", " ", s)
    return s.strip()


def _make_abs(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return BASE_URL + href
    return BASE_URL + "/" + href


class NjuSoftwareParser(SchoolParser):
    name = "nju_software"
    display_name = "南京大学软件学院"
    output_dir_name = "南大软件导师联系"

    # ------------------------------------------------------------------ #
    #  列表抓取
    # ------------------------------------------------------------------ #

    def fetch_teacher_list(self) -> list[TeacherStub]:
        stubs: list[TeacherStub] = []
        seen: set[str] = set()
        try:
            resp = http_get(LIST_URL, timeout=config.HTTP_TIMEOUT)
            resp.encoding = "utf-8"
            h = resp.text
        except Exception as e:
            log(f"[nju_software] 列表页抓取失败: {e}")
            return stubs

        new_stubs = self._parse_list_html(h, seen)
        log(f"[nju_software] 列表页共解析 {len(new_stubs)} 人")
        stubs.extend(new_stubs)
        return stubs

    def _parse_list_html(self, h: str, seen: set) -> list[TeacherStub]:
        """解析列表 HTML。

        HTML 结构（class="content" 内按分类排列）：
          <div class="con_title">教授</div>
          <table ...>
            <tbody>
              <tr>
                <td ...><a href="https://software.nju.edu.cn//USERNAME/index.html"
                           style="color:#333;">姓名（备注）</a></td>
              </tr>
            </tbody>
          </table>
          <div class="con_title">副教授、高级工程师（...）</div>
          <table ...> ... </table>
        """
        stubs: list[TeacherStub] = []

        # 提取 content 区域
        content_m = re.search(r'<div class="content">(.*?)<!--\s*End', h, re.S)
        block = content_m.group(1) if content_m else h

        # 逐段（分类标题 + 后续表格）处理
        # 用 con_title 切分
        segments = re.split(r'(<div class="con_title">.*?</div>)', block, flags=re.S)

        current_category = "专业教师"
        for seg in segments:
            title_m = re.match(r'<div class="con_title">(.*?)</div>', seg, re.S)
            if title_m:
                current_category = _clean(title_m.group(1))
                continue

            # 在该段落内找所有 <a href="..."> 指向 /*/index.html 的链接
            for m in re.finditer(
                r'<a\s+href=["\']'
                r'(https?://software\.nju\.edu\.cn//([^/"\']+)/index\.html)'
                r'["\'][^>]*>(.*?)</a>',
                seg, re.S | re.I
            ):
                url = m.group(1).strip()
                slug = m.group(2)
                # 过滤导航路径（含 / 的多级路径如 szll/szdw）
                if "/" in slug:
                    continue
                raw_name = _clean(m.group(3))

                # 去掉括号备注，如"骆  斌（兼职博导）" → "骆斌"
                name = re.sub(r"（[^）]*）|\([^)]*\)", "", raw_name).strip()
                # 折叠内部空格（"骆  斌" → "骆斌"）
                name = re.sub(r"\s+", "", name)

                if not name or len(name) < 2 or len(name) > 5:
                    continue
                # 过滤页面导航文字（非人名）
                if not re.fullmatch(r'[一-龥·•A-Za-z\s]+', name):
                    continue
                _NAV_WORDS = {"专业教师", "科研团队", "师资力量", "兼职教师",
                              "博士后", "教职工", "学院", "中心", "研究所"}
                if name in _NAV_WORDS:
                    continue
                if url in seen:
                    continue
                seen.add(url)

                institute = f"南京大学软件学院·{current_category}"
                stub = TeacherStub(name=name, detail_url=url, institute=institute)
                stub._category = current_category   # type: ignore[attr-defined]
                stubs.append(stub)

        return stubs

    # ------------------------------------------------------------------ #
    #  详情页抓取
    # ------------------------------------------------------------------ #

    def fetch_teacher_detail(self, stub: TeacherStub) -> Teacher:
        t = Teacher(name=stub.name, detail_url=stub.detail_url,
                    institute=stub.institute)
        t.homepage = stub.detail_url

        # 职称从列表分类推断
        category = getattr(stub, "_category", "")
        if "教授" in category:
            t.title = "教授" if category == "教授" else "副教授"

        try:
            resp = http_get(stub.detail_url, timeout=config.HTTP_TIMEOUT)
            resp.encoding = "utf-8"
            h = resp.text
        except Exception as e:
            log(f"[nju_software] 个人页失败 {stub.name}: {e}")
            return t

        # 去除 script/style 干扰
        h2 = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S)

        self._parse_contact(h2, t)
        self._parse_bio(h2, t)
        self._parse_papers(h2, t)
        return t

    def _parse_contact(self, h: str, t: Teacher) -> None:
        """从 class="mc" 提取邮箱和电话。"""
        mc_m = re.search(r'class="mc">(.*?)</div>\s*</div>\s*</div>', h, re.S)
        if not mc_m:
            return
        mc = mc_m.group(1)

        # 邮箱：<div class="aa"> <span> 邮箱： </span> </div>\n<div class="con">xxx</div>
        email_m = re.search(
            r'邮箱[：:].*?<div class="con">\s*(.*?)\s*</div>',
            mc, re.S
        )
        if email_m:
            raw = _clean(email_m.group(1))
            em = re.search(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}', raw)
            if em:
                t.email = em.group(0)

        # 如果 mc 没找到，扫整页
        if not t.email:
            em2 = re.search(
                r'[A-Za-z0-9._%+\-]+@(?:[A-Za-z0-9.\-]*\.)?nju\.edu\.cn',
                h
            )
            if em2:
                t.email = em2.group(0)

    def _parse_bio(self, h: str, t: Teacher) -> None:
        """从 id="aRight" 提取简介正文。"""
        right_m = re.search(r'id="aRight">(.*?)(?:</div>\s*){3,}', h, re.S)
        if not right_m:
            # 宽松备用：找 middle clearfix
            right_m = re.search(r'class="middle clearfix">(.*?)</div>\s*</div>', h, re.S)
        if not right_m:
            return
        body = right_m.group(1)

        # 职称关键词
        title_m = re.search(
            r"(讲席教授|特任教授|特任副教授|特任研究员|特任副研究员"
            r"|教授|副教授|研究员|副研究员|讲师|助理教授|助理研究员)",
            _clean(body)
        )
        if title_m and not t.title:
            t.title = title_m.group(1)

        bio_text = _clean(body)
        if len(bio_text) > 80:
            t.bio = bio_text[:2000]

    def _parse_papers(self, h: str, t: Teacher) -> None:
        """从 aRight 区域的 <li> 提取看起来像论文的条目。"""
        right_m = re.search(r'id="aRight">(.*)', h, re.S)
        block = right_m.group(1) if right_m else h

        papers: list[str] = []
        for li_m in re.finditer(r"<li[^>]*>(.*?)</li>", block, re.S):
            text = _clean(li_m.group(1))
            if len(text) > 40 and re.search(
                r"\b(AAAI|ICLR|NeurIPS|ICML|ACL|EMNLP|CVPR|ICCV|ECCV|KDD|SIGIR"
                r"|WWW|SIGMOD|VLDB|OSDI|SOSP|IEEE|ACM|Trans|Journal|Conference|arXiv"
                r"|TOSEM|ISSTA|ISSTA|FSE|ICSE|ASE|PLDI|POPL|OOPSLA)\b",
                text, re.I
            ):
                papers.append(text[:300])
        t.papers_listed = papers[:30]
