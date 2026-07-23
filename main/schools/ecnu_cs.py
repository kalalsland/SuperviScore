# -*- coding: utf-8 -*-
"""华东师范大学计算机学院 解析器。

数据来源（已实测）：
- 列表页：http://www.cs.ecnu.edu.cn/jzgml/list.htm
  结构：单页静态，<div class="team"><ul> 内含若干 <li> 卡片
  每个 <li> 属性：
    name="首字母"  data-name="职称分类"  data-field1="教授/博士生导师"
  卡片正文含：姓名链接（faculty.ecnu.edu.cn）+ intro 段落（职称/邮箱/$分隔）
  当前共 39 条记录；若后续出现分页也可正确翻页。
- 详情页：https://faculty.ecnu.edu.cn/...
  与 ecnu_sei 共用同一套华师教工页格式（复用 ecnu_sei._parse_faculty_detail）
"""
from __future__ import annotations
import re
import html as htmllib

from schools.base import SchoolParser
from schools.ecnu_sei import _parse_faculty_detail  # 华师通用详情解析器
from core.models import TeacherStub, Teacher
from core.utils import http_get, log, polite_sleep  # noqa: F401
import config

LIST_URL = "http://www.cs.ecnu.edu.cn/jzgml/list.htm"
BASE_URL = "http://www.cs.ecnu.edu.cn"
FACULTY_BASE = "https://faculty.ecnu.edu.cn"


def _clean(s: str) -> str:
    """去 HTML 标签、转义实体、折叠空白。"""
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t\r\n\xa0　]+", " ", s)
    return s.strip()


def _make_abs(href: str, base: str = FACULTY_BASE) -> str:
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return base + href
    return base + "/" + href


class EcnuCsParser(SchoolParser):
    name = "ecnu_cs"
    display_name = "华东师范大学计算机学院"
    output_dir_name = "华师计算机导师联系"

    # ------------------------------------------------------------------ #
    #  列表抓取
    # ------------------------------------------------------------------ #

    def fetch_teacher_list(self) -> list[TeacherStub]:
        stubs: list[TeacherStub] = []
        seen: set[str] = set()
        page = 1
        while True:
            url = LIST_URL if page == 1 else LIST_URL.replace("list.htm", f"list{page}.htm")
            try:
                resp = http_get(url, timeout=config.HTTP_TIMEOUT)
                resp.encoding = "utf-8"
                h = resp.text
            except Exception as e:
                log(f"[ecnu_cs] 第{page}页获取失败: {e}")
                break

            new_stubs = self._parse_list_html(h, seen)
            log(f"[ecnu_cs] 第{page}页 新增 {len(new_stubs)} 人")
            stubs.extend(new_stubs)

            if not new_stubs:
                break
            if not self._has_next_page(h, page):
                break
            page += 1
            if page > 20:
                break
            polite_sleep(0.5)

        log(f"[ecnu_cs] 列表抓取完毕，共 {len(stubs)} 人")
        return stubs

    def _parse_list_html(self, h: str, seen: set) -> list[TeacherStub]:
        """解析列表页 HTML 中的教师卡片。

        HTML 结构：
          <div class="team">
            <ul>
              <li name="X" data-name="职称分类" data-field1="教授/博士生导师">
                <div class="li-img ...">
                  <a href='faculty_url' title='姓名'>...</a>
                </div>
                <div class="li-text ...">
                  <a href='...' title='姓名'>姓名</a>
                  <div class="intro">
                    <p>职称：副教授$办公室：...$ 邮箱：xxx@cs.ecnu.edu.cn</p>
                  </div>
                </div>
              </li>
              ...
        """
        stubs: list[TeacherStub] = []

        # 定位 .team 容器
        team_m = re.search(r'class="team"[^>]*>(.*?)(?:</div>\s*</div>\s*</div>|<script)', h, re.S)
        if not team_m:
            return stubs
        team_html = team_m.group(1)

        # 每个 <li> 是一名教师
        li_pattern = re.compile(
            r'<li\s+name="([^"]*)"[^>]*data-name="([^"]*)"[^>]*data-field1="([^"]*)"[^>]*>(.*?)</li>',
            re.S
        )
        for li_m in li_pattern.finditer(team_html):
            letter = li_m.group(1)
            category = li_m.group(2)
            field1 = li_m.group(3)
            li_html = li_m.group(4)

            # 提取详情链接 + 姓名（title 属性最可靠）
            link_m = re.search(
                r"href=['\"]([^'\"]+)['\"][^>]*title=['\"]([^'\"]+)['\"]",
                li_html
            )
            if not link_m:
                continue
            raw_href = link_m.group(1).strip()
            name = _clean(link_m.group(2))
            if not name:
                continue

            detail_url = _make_abs(raw_href, FACULTY_BASE)
            if detail_url in seen:
                continue
            seen.add(detail_url)

            # intro 段落：格式 "职称：X$办公室：Y$邮箱：Z"（$ 作换行符）
            intro_m = re.search(r'<div\s+class="intro">(.*?)</div>', li_html, re.S)
            email_hint = ""
            title_hint = ""
            if intro_m:
                intro_text = _clean(intro_m.group(1))
                # 按 $ 分隔条目
                for segment in re.split(r'\$', intro_text):
                    segment = segment.strip()
                    if segment.startswith("邮箱："):
                        email_hint = segment[3:].strip()
                    elif segment.startswith("职称："):
                        title_hint = segment[3:].strip()

            stub = TeacherStub(
                name=name,
                detail_url=detail_url,
                institute=f"华师计算机学院·{category}",
            )
            stub._email_hint = email_hint    # type: ignore[attr-defined]
            stub._title_hint = title_hint    # type: ignore[attr-defined]
            stubs.append(stub)

        return stubs

    @staticmethod
    def _has_next_page(h: str, current_page: int) -> bool:
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
        t = Teacher(
            name=stub.name,
            detail_url=stub.detail_url,
            institute=stub.institute,
        )

        # 先用列表页的邮箱 / 职称填充（详情页优先覆盖）
        email_hint = getattr(stub, "_email_hint", "")
        if email_hint and "@" in email_hint:
            t.email = email_hint

        title_hint = getattr(stub, "_title_hint", "")
        if title_hint:
            t.title = title_hint

        try:
            resp = http_get(stub.detail_url, timeout=config.HTTP_TIMEOUT)
            resp.encoding = "utf-8"
            h = resp.text
        except Exception as e:
            log(f"[ecnu_cs] 个人页获取失败 {stub.name}: {e}")
            return t

        # 复用华师通用详情解析（与 ecnu_sei 格式一致）
        _parse_faculty_detail(h, t)
        return t
