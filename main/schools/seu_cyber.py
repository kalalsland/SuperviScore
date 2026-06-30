# -*- coding: utf-8 -*-
"""东南大学网络空间安全学院 解析器。

数据来源（已实测）：
- 列表：https://cyber.seu.edu.cn/gxjs/list.htm
  单页，包含四个院系的教师列表（无分页）：
    网络安全系 / 智能安全系 / 信息安全系 / 密码科学与技术系
  每系一个 <div class="sz_box"> 块，内含 <ul class="sz_list">，
  每位教师对应：
    <li class="news nN clearfix">
      <a class="news_link" href="...">
        <div class="news_title">姓名</div>
      </a>
    </li>
  链接有绝对 URL（https://cyber.seu.edu.cn/...）和相对 URL（/xxx/list.htm）两种形式。

- 个人页（cyber.seu.edu.cn 子路径）：<div class="tearch_top clearfix"> 内
    <span class="title">姓名</span>
    <span class="career">职称</span>
    <div class="info_text">电话：...</div>
    <div class="info_text">个人主页：...</div>（有时放简介）
    <div class="info_text">邮箱：...</div>
  以及 <div class="tearch_bottom"> 内各 news_box 段落（教育背景/研究领域/研究概况等）。
"""
from __future__ import annotations
import re
import html as htmllib

from schools.base import SchoolParser
from core.models import TeacherStub, Teacher
from core.utils import http_get, log, polite_sleep
import config

BASE_URL = "https://cyber.seu.edu.cn"
LIST_URL = "https://cyber.seu.edu.cn/gxjs/list.htm"

def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t\r\n\xa0　]+", " ", s)
    return s.strip()


def _abs_url(href: str) -> str:
    """将相对路径补全为绝对 URL。"""
    href = href.strip()
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return BASE_URL + href
    return BASE_URL + "/" + href


class SeuCyberParser(SchoolParser):
    name = "seu_cyber"
    display_name = "东南大学网络空间安全学院"
    output_dir_name = "东南大学网安导师联系"

    def fetch_teacher_list(self) -> list[TeacherStub]:
        """列表页是单页，四个院系的教师全在 gxjs/list.htm 中。"""
        stubs: list[TeacherStub] = []
        seen: set[str] = set()
        try:
            resp = http_get(LIST_URL, timeout=config.HTTP_TIMEOUT)
            resp.encoding = "utf-8"
            h = resp.text
        except Exception as e:
            log(f"[seu_cyber] 列表页抓取失败: {e}")
            return stubs

        new_stubs = self._parse_list_html(h, seen)
        log(f"[seu_cyber] 列表页共解析 {len(new_stubs)} 人")
        stubs.extend(new_stubs)
        return stubs

    def _parse_list_html(self, h: str, seen: set) -> list[TeacherStub]:
        """解析列表 HTML，按院系块提取各系教师。

        HTML 结构：
          <div class="sz_box" ...>
            <div class="sz_tt">
              <div class="sz_tit">网络安全系</div>（按拼音排序）
            </div>
            <ul class="sz_list">
              <li class="news n1 clearfix">
                <a class="news_link" href="/ch/list.htm">
                  <div class="news_title">陈浩</div>
                </a>
              </li>
              ...
            </ul>
          </div>

        策略：并行提取 sz_tit（院系名）和 sz_list（教师列表），按顺序配对。
        sz_box 的嵌套 div 闭合不规则，直接按 sz_list 分块更可靠。
        """
        stubs: list[TeacherStub] = []

        # 提取所有院系名（按出现顺序）
        dept_names = re.findall(
            r'<div class="sz_tit">\s*(.*?)\s*</div>',
            h, re.DOTALL
        )

        # 提取所有 sz_list 块（每个对应一个院系）
        sz_list_blocks = re.findall(
            r'<ul class="sz_list">(.*?)</ul>',
            h, re.DOTALL
        )

        for idx, block in enumerate(sz_list_blocks):
            # 对应院系名
            if idx < len(dept_names):
                dept = _clean(dept_names[idx])
            else:
                dept = "网络空间安全学院"

            # 提取该系所有教师条目
            for m in re.finditer(
                r'<a\s+class="news_link"\s+href="([^"]+)"[^>]*>\s*'
                r'<div class="news_title">\s*(.*?)\s*</div>',
                block, re.DOTALL
            ):
                href = m.group(1)
                raw_name = m.group(2)
                name = _clean(raw_name)
                # 保留括号备注（如"博后"），方便调用方过滤
                if not name or len(name) < 2:
                    continue
                url = _abs_url(href)
                if url in seen:
                    continue
                seen.add(url)
                stubs.append(TeacherStub(
                    name=name,
                    detail_url=url,
                    institute=f"东南大学网安学院·{dept}"
                ))

        return stubs

    def fetch_teacher_detail(self, stub: TeacherStub) -> Teacher:
        """抓取个人页，提取职称/邮箱/个人主页/简介。

        个人页结构（class="tearch_top clearfix"）：
          <span class="title">姓名</span><span class="career">教授</span>
          <div class="info_text">电话：025-XXXXXXXX</div>
          <div class="info_text">办公室：...</div>
          <div class="info_text">个人主页：...</div>（有时放文本简介）
          <div class="info_text">邮箱：xxx@seu.edu.cn</div>

        以及 <div class="tearch_bottom"> 内各 news_box：
          <div class="news_title">研究领域</div>
          <div class="news_text">...</div>
          <div class="news_title">研究概况</div>
          <div class="news_text">...</div>
        """
        t = Teacher(name=stub.name, detail_url=stub.detail_url,
                    institute=stub.institute)
        try:
            resp = http_get(stub.detail_url, timeout=config.HTTP_TIMEOUT)
            resp.encoding = "utf-8"
            h = resp.text
        except Exception as e:
            log(f"[seu_cyber] 个人页失败 {stub.name}: {e}")
            return t

        # --- 职称 ---
        m_career = re.search(r'<span class="career">\s*(.*?)\s*</span>', h, re.DOTALL)
        if m_career:
            t.title = _clean(m_career.group(1))

        # --- 从 info_text div 列表中提取字段 ---
        # 结构：<div class="info_text">字段名：内容</div>
        info_texts = re.findall(
            r'<div class="info_text">\s*(.*?)\s*</div>',
            h, re.DOTALL
        )
        homepage_raw = ""
        for raw in info_texts:
            text = _clean(raw)
            if text.startswith("邮箱：") or text.startswith("邮箱:"):
                val = text.split("：", 1)[-1].split(":", 1)[-1].strip()
                em = re.search(r'[\w.\-+]+@[\w.\-]+\.\w+', val)
                if em:
                    t.email = em.group(0)
            elif text.startswith("个人主页：") or text.startswith("个人主页:"):
                homepage_raw = text.split("：", 1)[-1].split(":", 1)[-1].strip()
                # 判断是 URL 还是文本简介
                if re.match(r'https?://', homepage_raw):
                    t.homepage = homepage_raw

        # --- 从 tearch_bottom 的 news_box 提取研究领域/概况 ---
        bottom_m = re.search(
            r'<div class="tearch_bottom">(.*?)(?:</div>\s*</div>\s*<!--End|$)',
            h, re.DOTALL
        )
        bio_parts: list[str] = []
        if bottom_m:
            bottom_html = bottom_m.group(1)
            # 提取各段标题+内容
            for box_m in re.finditer(
                r'<div class="news_title">\s*(.*?)\s*</div>\s*'
                r'<div class="news_text">\s*(.*?)\s*</div>',
                bottom_html, re.DOTALL
            ):
                section_title = _clean(box_m.group(1))
                section_body = _clean(box_m.group(2))
                if section_body and section_title in (
                    "研究领域", "研究概况", "研究课题", "教育背景", "学术兼职"
                ):
                    bio_parts.append(f"【{section_title}】{section_body}")

        # 若个人主页字段里放的是文本简介（不是 URL），也纳入 bio
        if homepage_raw and not re.match(r'https?://', homepage_raw) and len(homepage_raw) > 20:
            bio_parts.insert(0, homepage_raw)

        t.bio = "\n\n".join(bio_parts)[:2000]

        # 个人页 URL 本身作为主页（若未从字段中提取到）
        if not t.homepage:
            t.homepage = stub.detail_url

        return t
