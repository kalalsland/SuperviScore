# -*- coding: utf-8 -*-
"""同济大学计算机科学与技术学院 解析器。

数据来源（已实测）：
- 列表页 UTF-8 编码，单页，无分页
- <div class="text-list szlist"> 内含多个 <h3> 职称分类，
  每类下 <ul><li><a href="../info/1061/XXXX.htm" title="姓名"> 格式
- 详情页正文在 <div id="vsb_content"> / <div class="v_news_content">
  - 邮箱通常以"联系邮箱：xxx"出现
  - 个人主页以超链接出现（如有）
"""
from __future__ import annotations
import re
import html as htmllib

from schools.base import SchoolParser
from core.models import TeacherStub, Teacher
from core.utils import http_get, log, polite_sleep  # noqa: F401
import config

BASE_URL = "https://cs.tongji.edu.cn"
LIST_URL = "https://cs.tongji.edu.cn/szdw/jsml_azc_.htm"


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
    # href 形如 ../info/1061/3376.htm，以列表页所在目录为基准
    # 列表页在 /szdw/，../info/... → /info/...
    href_clean = re.sub(r"^\.\./", "/", href)
    if href_clean.startswith("/"):
        return BASE_URL + href_clean
    return BASE_URL + "/" + href_clean


class TongjiCsParser(SchoolParser):
    name = "tongji_cs"
    display_name = "同济大学计算机学院"
    output_dir_name = "同济计算机导师联系"

    # ------------------------------------------------------------------ #
    #  列表抓取
    # ------------------------------------------------------------------ #

    def fetch_teacher_list(self) -> list[TeacherStub]:
        try:
            resp = http_get(LIST_URL, timeout=config.HTTP_TIMEOUT)
            resp.encoding = "utf-8"
            h = resp.text
        except Exception as e:
            log(f"[tongji_cs] 列表页获取失败: {e}")
            return []

        stubs = self._parse_list_html(h)
        log(f"[tongji_cs] 列表抓取完毕，共 {len(stubs)} 人")
        return stubs

    def _parse_list_html(self, h: str) -> list[TeacherStub]:
        """解析列表页 HTML，返回 TeacherStub 列表。

        HTML 结构：
          <div class="text-list szlist">
            <h3>教授（研究员）</h3>
            <ul>
              <li><a href="../info/1061/3376.htm" target="_blank" title="宾燚">宾燚</a></li>
              ...
            </ul>
            <h3>副教授（副研究员）</h3>
            ...
          </div>
        """
        stubs: list[TeacherStub] = []
        seen: set[str] = set()

        # 截取 szlist 容器内容，避免导航区域干扰
        block_m = re.search(r'class="text-list szlist">(.*?)(?:</div>\s*</div>\s*</div>|$)',
                            h, re.S)
        if not block_m:
            log("[tongji_cs] 未找到 szlist 容器，尝试全页解析")
            block = h
        else:
            block = block_m.group(1)

        # 按 <h3> 分段，提取职称分类
        # 先把 <h3>xxx</h3> 和 <ul>...</ul> 逐段配对
        segments = re.split(r'(<h3>[^<]*</h3>)', block)
        current_category = "未知"
        for seg in segments:
            h3_m = re.match(r'<h3>(.*?)</h3>', seg, re.S)
            if h3_m:
                current_category = _clean(h3_m.group(1))
                continue

            # 在当前段落里找所有 <li><a ...>
            for li_m in re.finditer(
                r'<li>\s*<a\s+href=["\']([^"\']+)["\'][^>]*title=["\']([^"\']+)["\']',
                seg
            ):
                raw_href = li_m.group(1).strip()
                name = li_m.group(2).strip()

                if not name or not raw_href:
                    continue

                detail_url = _make_abs(raw_href)
                if not detail_url or detail_url in seen:
                    continue
                seen.add(detail_url)

                stub = TeacherStub(
                    name=name,
                    detail_url=detail_url,
                    institute=f"同济大学·计算机学院·{current_category}",
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
            log(f"[tongji_cs] 个人页获取失败 {stub.name}: {e}")
            return t

        self._parse_detail_html(h, t)
        return t

    def _parse_detail_html(self, h: str, t: Teacher) -> None:
        """从详情页 HTML 填充 Teacher 字段（原地修改 t）。"""
        # 去除 script/style 干扰
        h2 = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S)

        # 定位正文区：vsb_content 或 v_news_content
        body = ""
        for pattern in [
            r'id=["\']vsb_content["\'][^>]*>(.*?)(?:</form>|</div>\s*<div id="div_vote)',
            r'class=["\']v_news_content["\'][^>]*>(.*?)</div>',
            r'class=["\']art-body["\'][^>]*>(.*?)</div>\s*<div id="div_vote',
        ]:
            m = re.search(pattern, h2, re.S)
            if m:
                body = m.group(1)
                break
        if not body:
            body = h2

        # ---- 邮箱 ----
        # 常见格式：联系邮箱：xxx@yyy.zzz 或 E-mail: xxx
        email_m = re.search(
            r"(?:联系邮箱|[Ee][-\s]?[Mm]ail)[：:]\s*([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})",
            body
        )
        if email_m:
            t.email = email_m.group(1).strip()
        else:
            # 通用兜底：页面内任意邮箱
            email_m2 = re.search(
                r"\b([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b",
                body
            )
            if email_m2:
                candidate = email_m2.group(1)
                # 排除图片/文件路径伪邮箱
                if not re.search(r"\.(png|jpg|gif|css|js)$", candidate, re.I):
                    t.email = candidate

        # ---- 个人主页 ----
        # 优先找"个人主页"/"个人网页"附近的超链接
        homepage_m = re.search(
            r"个\s*人\s*(?:主|网)\s*页[^<]{0,20}<a[^>]+href=['\"]([^'\"]+)['\"]",
            body, re.S
        )
        if homepage_m:
            t.homepage = homepage_m.group(1).strip()
        else:
            # 找明显的外部主页链接（排除学校域名内的相对路径）
            for href_m in re.finditer(r'href=["\']([^"\']+)["\']', body):
                href = href_m.group(1)
                if re.match(r'https?://', href) and "tongji.edu.cn" not in href:
                    # 排除明显非主页的链接（微博、微信公众号、论文等）
                    if not re.search(r'weibo|wechat|mp\.weixin|doi\.org|arxiv', href, re.I):
                        t.homepage = href
                        break

        # ---- 职称 ----
        title_m = re.search(
            r"(讲席教授|特聘教授|预聘助理教授|助理教授|教授|副教授"
            r"|研究员|副研究员|讲师|助理研究员|博士后)",
            body
        )
        if title_m:
            t.title = title_m.group(1)
        else:
            # 从 institute 推断
            inst = t.institute or ""
            if "教授" in inst:
                t.title = "教授"
            elif "副教授" in inst:
                t.title = "副教授"
            elif "讲师" in inst:
                t.title = "讲师"

        # ---- Bio ----
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
                r"|SIGMOD|VLDB|OSDI|SOSP|IEEE|ACM|Trans|Journal|Conference|arXiv|TPAMI"
                r"|IJCAI|NAACL|MM|INTERSPEECH|ICASSP|USENIX)\b",
                text, re.I
            ):
                papers.append(text[:300])
        t.papers_listed = papers[:30]
