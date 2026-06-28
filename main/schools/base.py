# -*- coding: utf-8 -*-
"""学校解析器抽象基类。

换学校 = 新增一个子类实现这两个方法，core/ 内核完全不动。
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from core.models import TeacherStub, Teacher


class SchoolParser(ABC):
    # 子类必须覆盖以下三个类属性
    name: str = ""              # 模块标识，如 "sjtu_cs"，与 config.SCHOOL 对应
    display_name: str = ""      # 展示名，如 "上海交通大学计算机学院"
    output_dir_name: str = ""   # 结果输出子目录名，如 "交大导师联系"

    @abstractmethod
    def fetch_teacher_list(self) -> list[TeacherStub]:
        """抓教师名录 → 返回 TeacherStub 列表（姓名 + 个人页 URL + 研究所）。"""
        raise NotImplementedError

    @abstractmethod
    def fetch_teacher_detail(self, stub: TeacherStub) -> Teacher:
        """抓个人页 → 返回填好职称/简介/官网论文/主页/邮箱的 Teacher。"""
        raise NotImplementedError


def get_parser(school_name: str) -> SchoolParser:
    """工厂：按 config.SCHOOL 返回对应解析器实例。"""
    if school_name == "sjtu_cs":
        from schools.sjtu_cs import SjtuCsParser
        return SjtuCsParser()
    # 下次复旦：
    # if school_name == "fudan_cs":
    #     from schools.fudan_cs import FudanCsParser
    #     return FudanCsParser()
    raise ValueError(f"未知学校解析器: {school_name}（请在 schools/ 下实现并在 get_parser 注册）")
