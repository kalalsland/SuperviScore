# -*- coding: utf-8 -*-
"""上海交通大学生物医学工程学院 解析器。

数据来源（已实测）：
- 列表：GET https://bme.sjtu.edu.cn/Web/Faculty/115
        页面直接包含全部教师，无翻页。
        结构：<a href="/Web/FacultyDetail/{id}" class="pd" title="{姓名}">
- 个人页：GET https://bme.sjtu.edu.cn/Web/FacultyDetail/{id}
        姓名   <p class="ptitle">{姓名}</p>
        简介   <div class="jianjie"><p class="ptitle">...</p><p>{简介}</p></div>
        联系方式（含邮箱）位于 <div class="ti-bg">联系方式</div> 后的 <p> 段落
        论文    <div class="ti-bg">代表性论文专著</div> 后的 <div> 内容
        职称   从简介段落首句中提取（通常以 "教授" / "副教授" / "研究员" 等开头）
"""
from __future__ import annotations
import re
import html as htmllib

from schools.base import SchoolParser
from core.models import TeacherStub, Teacher
from core.utils import http_get, log, polite_sleep
import config

BASE_URL = "https://bme.sjtu.edu.cn"
LIST_URL = f"{BASE_URL}/Web/Faculty/115"


def _clean(s: str) -> str:
    """去 HTML 标签、解实体、折叠空白。"""
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = htmllib.unescape(s)
    s = re.sub(r"[ \t\r\n\xa0　]+", " ", s)
    return s.strip()


def _abs(url: str) -> str:
    """将相对 URL 转为绝对 URL。"""
    if not url:
        return url
    if url.startswith("http"):
        return url
    return BASE_URL + url if url.startswith("/") else BASE_URL + "/" + url


class SjtuBmeParser(SchoolParser):
    name = "sjtu_bme"
    display_name = "上海交通大学生物医学工程学院"
    output_dir_name = "交大生医工导师联系"

    # ------------------------------------------------------------------ 列表
    def fetch_teacher_list(self) -> list[TeacherStub]:
        stubs: list[TeacherStub] = []
        seen: set[str] = set()

        try:
            resp = http_get(LIST_URL, timeout=config.HTTP_TIMEOUT,
                            headers={"User-Agent": "Mozilla/5.0"})
            resp.encoding = "utf-8"
            html = resp.text
        except Exception as e:
            log(f"[sjtu_bme] 列表页抓取失败: {e}")
            return stubs

        # 匹配 <a href="/Web/FacultyDetail/..." class="pd" title="姓名">
        for m in re.finditer(
            r'<a\s+href="(/Web/FacultyDetail/\d+)"\s+class="pd"\s+title="([^"]+)"',
            html,
        ):
            url = _abs(m.group(1))
            name = _clean(m.group(2))
            if not name or url in seen:
                continue
            seen.add(url)
            stubs.append(TeacherStub(name=name, detail_url=url, institute=""))

        log(f"[sjtu_bme] 列表共 {len(stubs)} 位教师")
        return stubs

    # -------------------------------------------------------------- 个人详情
    def fetch_teacher_detail(self, stub: TeacherStub) -> Teacher:
        t = Teacher(name=stub.name, detail_url=stub.detail_url, institute=stub.institute)
        polite_sleep(0.3)

        try:
            resp = http_get(stub.detail_url, timeout=config.HTTP_TIMEOUT,
                            headers={"User-Agent": "Mozilla/5.0"})
            resp.encoding = "utf-8"
            h = resp.text
        except Exception as e:
            log(f"[sjtu_bme] 个人页抓取失败 {stub.name}: {e}")
            return t

        # ---- 姓名（以页面为准）
        m = re.search(r'<p class="ptitle">\s*([^<]+?)\s*</p>', h)
        if m:
            t.name = _clean(m.group(1)) or t.name

        # ---- 简介：<div class="jianjie"> 内第二个 <p>（第一个是 ptitle）
        m = re.search(r'<div class="jianjie">(.*?)</div>\s*<div class="clear">', h, re.S)
        if m:
            bio_block = m.group(1)
            # 去掉 ptitle 那个 p
            bio_block = re.sub(r'<p class="ptitle">[^<]*</p>', "", bio_block, count=1)
            t.bio = _clean(bio_block)

        # ---- 职称：从简介首行提取，常见格式：
        #   "教授，博士生导师。..."  /  "副教授，..."  /  "长聘教授，..."
        if t.bio:
            m_title = re.match(
                r'^([^，,。\n]{2,20}(?:教授|研究员|副研究员|讲师|助理教授|工程师))',
                t.bio,
            )
            if m_title:
                t.title = m_title.group(1).strip()

        # ---- 联系方式区块
        m = re.search(
            r'<div class="ti-bg">联系方式</div>(.*?)(?:<div class="ti-bg">|</div>\s*</div>\s*</div>)',
            h, re.S,
        )
        if m:
            contact_block = m.group(1)
            # 邮箱
            m_email = re.search(
                r'邮箱[地址]*[：:]\s*([\w.+\-]+@[\w.\-]+)',
                contact_block,
            )
            if not m_email:
                m_email = re.search(r'mailto:([\w.+\-]+@[\w.\-]+)', contact_block)
            if not m_email:
                m_email = re.search(r'([\w.+\-]+@[\w.\-]+)', contact_block)
            if m_email:
                t.email = m_email.group(1).strip()

        # 邮箱兜底：全文搜索
        if not t.email:
            m_e = re.search(r'([\w.+\-]+@(?:sjtu|shsmu|fudan|edu)\.[\w.]+)', h)
            if m_e:
                t.email = m_e.group(1).strip()

        # ---- 个人主页：页面内的外链 href（非 bme.sjtu.edu.cn、非 sjtu 内部导航）
        for m_hp in re.finditer(r'href="(https?://[^"]+)"', h):
            url_cand = m_hp.group(1)
            # 排除学院内部链接和常见无关域名
            if any(skip in url_cand for skip in [
                "bme.sjtu.edu.cn", "sjtu.edu.cn", "en.bme.sjtu.edu.cn",
                "i.sjtu.edu.cn", "javascript",
            ]):
                continue
            t.homepage = url_cand
            break

        # ---- 代表性论文
        m = re.search(
            r'<div class="ti-bg">代表性论文[专著]*</div>(.*?)(?:<div class="ti-bg">|</div>\s*</div>\s*</div>)',
            h, re.S,
        )
        if m:
            papers_block = _clean(m.group(1))
            # 按序号 "1." / "2." 等分割
            items = re.split(r'\b\d{1,2}\.\s+', papers_block)
            papers: list[str] = []
            for it in items:
                it = it.strip(" .")
                if len(it) > 10:
                    papers.append(it[:400])
            t.papers_listed = papers[:15]

        return t
