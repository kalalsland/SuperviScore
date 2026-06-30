# -*- coding: utf-8 -*-
"""东南大学计算机科学与工程学院 解析器。

数据来源（已实测）：
- 列表：https://cse.seu.edu.cn/49355/list.htm
  单页，包含正高/副高/中级三类教师，每类一张或多张 <table class="table-name"> 表格。
  每个 <td> 内有 <a href="https://cs.seu.edu.cn/USERNAME/main.htm" target="_blank">姓名</a>。
- 个人页（cs.seu.edu.cn 子域名）：<div class="infoszdw"> 内
    <div class="carrer_con"> 含职称/院系/研究方向/邮箱等字段，
    <div class="con jj"> 含个人简介正文。
"""
from __future__ import annotations
import re
import html as htmllib

from schools.base import SchoolParser
from core.models import TeacherStub, Teacher
from core.utils import http_get, log, polite_sleep
import config

BASE_URL = "https://cse.seu.edu.cn"
LIST_URL = "https://cse.seu.edu.cn/49355/list.htm"


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t\r\n\xa0　]+", " ", s)
    return s.strip()


class SeuCseParser(SchoolParser):
    name = "seu_cse"
    display_name = "东南大学计算机科学与工程学院"
    output_dir_name = "东南大学计算机导师联系"

    def fetch_teacher_list(self) -> list[TeacherStub]:
        """列表页是单页（无分页），所有教师均在 49355/list.htm 中。"""
        stubs: list[TeacherStub] = []
        seen: set[str] = set()
        try:
            resp = http_get(LIST_URL, timeout=config.HTTP_TIMEOUT)
            resp.encoding = "utf-8"
            h = resp.text
        except Exception as e:
            log(f"[seu_cse] 列表页抓取失败: {e}")
            return stubs

        new_stubs = self._parse_list_html(h, seen)
        log(f"[seu_cse] 列表页共解析 {len(new_stubs)} 人")
        stubs.extend(new_stubs)
        return stubs

    def _parse_list_html(self, h: str, seen: set) -> list[TeacherStub]:
        """解析列表 HTML，从 table-name 表格中提取教师姓名和主页链接。

        HTML 结构：
          <table class="table-name" ...>
            <tbody>
              <tr>
                <td>...<a href="https://cs.seu.edu.cn/USERNAME/main.htm" target="_blank" ...>姓名</a>...</td>
                ...
              </tr>
            </tbody>
          </table>

        教师链接均以 https:// 开头（绝对 URL），无需补全域名。
        """
        stubs: list[TeacherStub] = []

        # 找到所有 table-name 表格块
        table_blocks = re.findall(
            r'class="table-name"[^>]*>.*?</table>',
            h, re.DOTALL
        )

        for block in table_blocks:
            # 在表格中找所有 <a href="..."> 链接（过滤掉纯 nav 链接）
            for m in re.finditer(
                r'<a\s[^>]*href="(https?://[^"]+)"[^>]*target="_blank"[^>]*>(.*?)</a>',
                block, re.DOTALL | re.IGNORECASE
            ):
                url = m.group(1).strip()
                raw_name = m.group(2)

                # 跳过非个人主页链接（含 list.htm、main.htm 的才是个人页）
                if "main.htm" not in url and "page.htm" not in url:
                    continue

                name = _clean(raw_name)
                if not name or len(name) < 2:
                    continue
                if url in seen:
                    continue
                seen.add(url)
                stubs.append(TeacherStub(name=name, detail_url=url,
                                         institute="东南大学计算机科学与工程学院"))
        return stubs

    def fetch_teacher_detail(self, stub: TeacherStub) -> Teacher:
        """抓取 cs.seu.edu.cn 个人页，提取职称/邮箱/研究方向/个人简介。

        个人页结构（class="infoszdw"）：
          <div class="title">姓名</div>
          <div class="text"><b>职称：</b><span>正高</span></div>
          <div class="text none"><b>所在院系：</b>XXX系</div>
          <div class="text"><b>研究方向：</b><span>...</span></div>
          <div class="text"><b>电话：</b><span>...</span></div>
          <div class="text"><b>邮箱：</b><span>xxx@seu.edu.cn</span></div>
          ...
          <div class="tit on">个人简介<em class="on"></em></div>
          <div class="con jj" style="display: block;">... 正文 ...</div>
        """
        t = Teacher(name=stub.name, detail_url=stub.detail_url,
                    institute=stub.institute)
        try:
            resp = http_get(stub.detail_url, timeout=config.HTTP_TIMEOUT)
            resp.encoding = "utf-8"
            h = resp.text
        except Exception as e:
            log(f"[seu_cse] 个人页失败 {stub.name}: {e}")
            return t

        # --- 职称 ---
        m_title = re.search(
            r'<b>\s*职称[：:]\s*</b>\s*<span>\s*(.*?)\s*</span>',
            h, re.DOTALL
        )
        if m_title:
            t.title = _clean(m_title.group(1))

        # --- 邮箱 ---
        m_email = re.search(
            r'<b>\s*邮箱[：:]\s*</b>\s*<span>\s*(.*?)\s*</span>',
            h, re.DOTALL
        )
        if m_email:
            raw_email = _clean(m_email.group(1))
            # 用正则提取合法邮箱地址（防止混入其他文字）
            em = re.search(r'[\w.\-+]+@[\w.\-]+\.\w+', raw_email)
            if em:
                t.email = em.group(0)

        # --- 研究方向（存入 bio 前缀）---
        m_research = re.search(
            r'<b>\s*研究方向[：:]\s*</b>\s*<span>\s*(.*?)\s*</span>',
            h, re.DOTALL
        )
        research_dir = ""
        if m_research:
            research_dir = _clean(m_research.group(1))

        # --- 个人简介（class="con jj"）---
        m_bio = re.search(
            r'class="con jj"[^>]*>(.*?)</div>',
            h, re.DOTALL
        )
        bio_text = ""
        if m_bio:
            bio_text = _clean(m_bio.group(1))
            if len(bio_text) > 2000:
                bio_text = bio_text[:2000]

        # 拼接简介
        if research_dir and bio_text:
            t.bio = f"研究方向：{research_dir}\n\n{bio_text}"
        elif research_dir:
            t.bio = f"研究方向：{research_dir}"
        else:
            t.bio = bio_text

        # --- 主页 URL（detail_url 本身即个人主页）---
        t.homepage = stub.detail_url

        return t
