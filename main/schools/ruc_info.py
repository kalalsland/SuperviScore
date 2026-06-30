# -*- coding: utf-8 -*-
"""中国人民大学信息学院 解析器。

数据来源（已实测）：
- 列表：http://info.ruc.edu.cn/jsky/szdw/ajxjgcx/bx/bx1/index.htm
        静态 HTML，共 5 页（index.htm / index1.htm … index4.htm），每页约 20 人。
        "不限" 职称 + "不限" 教学机构 = 全院教职人员，含博士后。
        每个 <div class="research"> 内含 <a href="..."> 及 <div class="text1">姓名</div>。
- 个人页：相对 URL 解析成绝对路径；页面 <title> 中含"姓名 - 职称 - 系所 - ..."，
          <div class="self_intro"> 含简介文本，<div class="contact"> 含电话/邮箱，
          各 <div class="pro_info"> 段落含研究方向/论文等结构化内容。
"""
from __future__ import annotations
import re
import html as htmllib
from urllib.parse import urljoin

from schools.base import SchoolParser
from core.models import TeacherStub, Teacher
from core.utils import http_get, log, polite_sleep
import config

BASE_URL = "http://info.ruc.edu.cn"

# 全院"不限 + 不限"列表，共 5 页
# index.htm  → 第 1 页
# index1.htm → 第 2 页  …  index4.htm → 第 5 页
LIST_BASE = "http://info.ruc.edu.cn/jsky/szdw/ajxjgcx/bx/bx1/index.htm"


def _clean(s: str) -> str:
    """去 HTML 标签、反转义、压缩空白。"""
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s or "", flags=re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t\r\n\xa0　]+", " ", s)
    return s.strip()


def _abs(url: str, base: str) -> str:
    """将相对 URL 转为绝对 URL。"""
    if not url:
        return ""
    if url.startswith("http"):
        return url
    return urljoin(base, url)


class RucInfoParser(SchoolParser):
    name = "ruc_info"
    display_name = "中国人民大学信息学院"
    output_dir_name = "人大信息导师联系"

    # 全院列表（不限职称、不限教学机构），分 5 页
    # 页码规律：第 1 页 = index.htm；第 N 页（N>=2）= index{N-1}.htm
    _MAX_PAGES = 10  # 保险起见允许最多 10 页

    def fetch_teacher_list(self) -> list[TeacherStub]:
        stubs: list[TeacherStub] = []
        seen: set[str] = set()
        page = 1
        while page <= self._MAX_PAGES:
            if page == 1:
                url = LIST_BASE
            else:
                url = LIST_BASE.replace("index.htm", f"index{page - 1}.htm")
            try:
                resp = http_get(url, timeout=config.HTTP_TIMEOUT)
                resp.encoding = "utf-8"
                h = resp.text
            except Exception as e:
                log(f"[ruc_info] 列表请求失败 {url}: {e}")
                break

            new_stubs = self._parse_list_html(h, seen, url)
            log(f"[ruc_info] {url} 第 {page} 页，新增 {len(new_stubs)} 人")

            if not new_stubs:
                # 空页 → 分页结束
                break

            stubs.extend(new_stubs)
            page += 1
            polite_sleep(0.5)

        return stubs

    def _parse_list_html(self, h: str, seen: set, page_url: str) -> list[TeacherStub]:
        """从列表页 HTML 中解析 TeacherStub 列表。

        每个教师卡片结构（简化）：
            <div class="research">
                <a href="../../jsjkxyjsx1/js2/418462...htm">
                    ...
                    <div class="text1">陈红</div>
                    ...
                </a>
            </div>
        """
        stubs: list[TeacherStub] = []

        # 匹配每个 research 块（包含 <a href> 及 text1 姓名）
        # 使用 re.DOTALL 让 . 匹配换行
        card_pattern = re.compile(
            r'<div\s+class="research">\s*'
            r'<a\s+href="([^"]+)"[^>]*>'
            r'(.*?)'
            r'</a>',
            re.S
        )
        name_pattern = re.compile(r'<div\s+class="text1">\s*([^<]+?)\s*</div>', re.S)

        for m in card_pattern.finditer(h):
            href = m.group(1).strip()
            block = m.group(2)

            nm = name_pattern.search(block)
            if not nm:
                continue
            name = _clean(nm.group(1))
            if not name:
                continue

            detail_url = _abs(href, page_url)
            if detail_url in seen:
                continue
            seen.add(detail_url)

            # 从 URL 路径推断教学机构（如 jsjkxyjsx1 → 计算机科学与技术系）
            institute = _dept_from_url(detail_url)

            stubs.append(TeacherStub(name=name, detail_url=detail_url, institute=institute))

        return stubs

    def fetch_teacher_detail(self, stub: TeacherStub) -> Teacher:
        t = Teacher(name=stub.name, detail_url=stub.detail_url, institute=stub.institute)
        try:
            resp = http_get(stub.detail_url, timeout=config.HTTP_TIMEOUT)
            resp.encoding = "utf-8"
            h = resp.text
        except Exception as e:
            log(f"[ruc_info] 个人页失败 {stub.name}: {e}")
            return t

        # ── 从 <title> 提取职称和系所 ──────────────────────────────────────
        # 格式：" 陈红 - 教授 - 计算机科学与技术系 - 按教学机构查询 - - 中国人民大学信息学院 "
        tm = re.search(r"<title>([^<]+)</title>", h)
        if tm:
            parts = [p.strip() for p in tm.group(1).split("-")]
            # parts[0]=姓名, parts[1]=职称, parts[2]=系所
            if len(parts) >= 2 and parts[1]:
                t.title = parts[1].strip()
            if len(parts) >= 3 and parts[2] and "查询" not in parts[2]:
                if not t.institute or t.institute == "中国人民大学信息学院":
                    t.institute = parts[2].strip()

        # ── 个人简介 (self_intro) ────────────────────────────────────────────
        bio_m = re.search(
            r'<div\s+class="self_intro"[^>]*>(.*?)</div>',
            h, re.S
        )
        if bio_m:
            t.bio = _clean(bio_m.group(1))

        # ── 联系方式 (contact) ───────────────────────────────────────────────
        # <div class="contact"> 里可能有 <p>电话 ：...<p>电子邮箱：chong@ruc.edu.cn
        contact_m = re.search(r'<div\s+class="contact">(.*?)</div>', h, re.S)
        if contact_m:
            contact_text = contact_m.group(1)
            email_m = re.search(
                r'[Ee]mail[：:\s]*([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})',
                contact_text
            )
            if email_m:
                t.email = email_m.group(1).strip()
            # 如果 contact 文本里找不到，尝试直接在整页找邮箱
            if not t.email:
                email_m2 = re.search(
                    r'[A-Za-z0-9._%+\-]+@(?:ruc\.edu\.cn|[A-Za-z0-9.\-]+\.[A-Za-z]{2,})',
                    h
                )
                if email_m2:
                    candidate = email_m2.group(0)
                    # 排除通用邮箱
                    if candidate not in ("rucinfo@ruc.edu.cn",):
                        t.email = candidate

        # ── 个人主页 ────────────────────────────────────────────────────────
        # 简介里常写"个人主页：bi.ruc.edu.cn/xxx" 或 <a href="http://...">
        if t.bio:
            hp_m = re.search(
                r'个人主页[：:]\s*(https?://\S+|[a-zA-Z0-9.\-/]+\.[a-zA-Z]{2,}/\S*)',
                t.bio
            )
            if hp_m:
                hp = hp_m.group(1).strip().rstrip("。，,.")
                if not hp.startswith("http"):
                    hp = "http://" + hp
                t.homepage = hp

        # 备选：在整页 HTML 里找 <a href="http://..."> 指向非本校外链
        # 排除已知无关域名
        _NOISE_DOMAINS = ("info.ruc.edu.cn", "bdimg", "baidu", "beian.gov", "beian.miit",
                          "share.baidu", "share.js", "jquery")
        if not t.homepage:
            for ahref in re.findall(r'href="(https?://[^"]+)"', h):
                if not any(nd in ahref for nd in _NOISE_DOMAINS):
                    t.homepage = ahref
                    break

        # ── 论文列表（科研成果段落）────────────────────────────────────────
        # 找所有 pro_info 块，抽取"科研成果"段的列表项
        papers: list[str] = []
        pro_blocks = re.findall(
            r'<div\s+class="pro_info"[^>]*>(.*?)</div>\s*</div>',
            h, re.S
        )
        in_papers_section = False
        for blk in pro_blocks:
            section_name_m = re.search(r'<div\s+class="name">\s*([^<]*?)\s*</div>', blk)
            if not section_name_m:
                continue
            section_name = _clean(section_name_m.group(1))
            para_m = re.search(r'<div\s+class="para">(.*?)</div>', blk, re.S)
            if not para_m:
                continue
            para_html = para_m.group(1)

            if any(kw in section_name for kw in ("科研成果", "论文", "发表", "著作")):
                in_papers_section = True
                # 每个 <br> 或 <p> 分隔一条
                items = re.split(r'<br\s*/?>|</p>|<p[^>]*>', para_html)
                for item in items:
                    txt = _clean(item)
                    if txt and len(txt) > 10:
                        papers.append(txt)
            else:
                in_papers_section = False

        t.papers_listed = papers[:50]  # 限制上限

        return t


# ── 辅助：从 URL 推断所属系所 ───────────────────────────────────────────────────

_DEPT_MAP: dict[str, str] = {
    "jsjkxyjsx": "计算机科学与技术系",
    "jjxxglx": "经济信息管理系",
    "dsjkxygcjys": "大数据科学与工程教研室",
    "xxjsjcjys": "信息技术基础教研室",
}


def _dept_from_url(url: str) -> str:
    for key, dept in _DEPT_MAP.items():
        if key in url:
            return dept
    return "中国人民大学信息学院"
