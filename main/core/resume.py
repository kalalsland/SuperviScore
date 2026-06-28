# -*- coding: utf-8 -*-
"""读取个人履历 PDF → 文本 → LLM 提炼用户画像（含研究经历、技能、方向偏好）。

画像缓存到磁盘，避免每次重跑都调一次 LLM。
"""
from __future__ import annotations
import os
import glob
from core.utils import log, Cache
from core import llm_client
import config


def _extract_pdf_text(path: str) -> str:
    """优先用 pymupdf(fitz)，回退 pypdf。"""
    try:
        import fitz
        doc = fitz.open(path)
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
        if text.strip():
            return text
    except Exception as e:
        log(f"[resume] fitz 抽取失败 {os.path.basename(path)}: {e}")
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception as e:
        log(f"[resume] pypdf 抽取失败 {os.path.basename(path)}: {e}")
        return ""


def load_resume_text(resume_dir: str = None) -> str:
    """读取目录下所有 PDF，拼成一份原始文本。"""
    resume_dir = resume_dir or config.RESUME_DIR
    if not os.path.isdir(resume_dir):
        log(f"[resume] 目录不存在: {resume_dir}")
        return ""
    pdfs = sorted(glob.glob(os.path.join(resume_dir, "*.pdf")))
    if not pdfs:
        log(f"[resume] 未找到 PDF: {resume_dir}")
        return ""
    chunks = []
    for p in pdfs:
        txt = _extract_pdf_text(p)
        log(f"[resume] 读取 {os.path.basename(p)}: {len(txt)} 字符")
        if txt.strip():
            chunks.append(f"=== 文件: {os.path.basename(p)} ===\n{txt}")
    return "\n\n".join(chunks)


PROFILE_SYS = """你是一位资深的研究生招生与科研匹配顾问。
请阅读申请人的个人履历，提炼一份用于"导师匹配"的结构化画像。
聚焦：研究方向与课题、用过的方法/技术栈、发表或项目成果、最想从事的方向、亮点与稀缺技能。
"""

PROFILE_USER_TMPL = """以下是申请人（直博申请者）的个人履历全文：

{resume_text}

请输出 JSON：
{{
  "name": "申请人姓名(若能识别)",
  "research_areas": ["最相关的研究方向标签, 3-6个"],
  "methods_skills": ["掌握的方法/技术/工具, 多个"],
  "achievements": ["代表性成果/项目/论文, 多个, 每条简短"],
  "preferred_directions": ["最希望读博从事的方向, 1-4个"],
  "highlights": "一段话总结申请人最强的卖点(用于套磁时打动导师)",
  "summary": "150字以内的整体画像"
}}"""


def build_user_profile(cache: Cache = None) -> dict:
    """返回用户画像 dict。带缓存。"""
    resume_text = load_resume_text()
    if not resume_text.strip():
        return {"name": "", "research_areas": [], "methods_skills": [],
                "achievements": [], "preferred_directions": [],
                "highlights": "", "summary": "(未读取到个人履历)",
                "raw_resume": ""}

    ckey = f"profile|{len(resume_text)}|{resume_text[:80]}"
    if cache:
        cached = cache.get("profile", ckey)
        if cached is not None:
            return cached

    log("[resume] 调用 LLM 提炼用户画像...")
    profile = llm_client.chat_json(
        PROFILE_SYS,
        PROFILE_USER_TMPL.format(resume_text=resume_text[:12000]),
        max_tokens=1500,
    ) or {}
    # 保留原文片段供 analyzer 引用套磁信
    profile["raw_resume"] = resume_text[:4000]
    if cache:
        cache.set("profile", ckey, profile)
    return profile
