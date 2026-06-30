# -*- coding: utf-8 -*-
"""数据模型（内核通用，与具体学校无关）。"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TeacherStub:
    """教师列表项（轻量，来自名录页）。"""
    name: str                       # 姓名
    detail_url: str                 # 个人页 URL
    institute: str = ""             # 所在研究所/系所


@dataclass
class Paper:
    """一篇论文。"""
    title: str
    year: Optional[int] = None
    venue: str = ""                 # 会议/期刊（DBLP 给出）
    authors: list[str] = field(default_factory=list)
    abstract: str = ""              # arXiv 补充
    url: str = ""
    author_position: str = ""       # "first" | "last" | "middle" | "unknown"


@dataclass
class Teacher:
    """一位老师的完整画像。"""
    name: str
    detail_url: str
    institute: str = ""
    title: str = ""                 # 职称
    homepage: str = ""              # 个人主页外链
    email: str = ""
    bio: str = ""                   # 个人简介原文
    papers_listed: list[str] = field(default_factory=list)   # 官网列出的论文标题
    papers: list[Paper] = field(default_factory=list)        # 检索到的近作
    dblp_url: str = ""
    identity_confidence: float = 1.0   # 论文检索消歧置信度 0-1
    paper_source: str = ""          # 论文主源: scholar/github/dblp/arxiv/none

    # —— 学术影响力（Google Scholar / GitHub，best-effort）——
    citations: Optional[int] = None        # 总引用量（Scholar）
    h_index: Optional[int] = None          # h 指数（Scholar）
    scholar_url: str = ""                  # Google Scholar 主页
    github_url: str = ""                   # GitHub 主页 URL
    github_stars: Optional[int] = None     # 所有公开仓库 star 总计
    github_bio: str = ""                   # GitHub Profile 自我介绍
    github_website: str = ""              # GitHub Profile 里的个人网站
    github_pinned: list[str] = field(default_factory=list)  # GitHub Pinned 项目名
    representative_works: list[str] = field(default_factory=list)  # 代表作（高被引/高star）

    # —— 分析结果（analyzer 填充）——
    analysis: dict = field(default_factory=dict)
    # —— 打分结果（scorer 填充）——
    score: Optional["ScoreResult"] = None


@dataclass
class ScoreResult:
    final_score: float = 0.0
    is_phd_advisor_guess: bool = True
    match_score: float = 0.0           # 方向匹配分 0-100（来自 analyzer）
    breakdown: dict = field(default_factory=dict)   # 各项加减分明细
    flags: list[str] = field(default_factory=list)  # 标记/嫌疑/待核实
