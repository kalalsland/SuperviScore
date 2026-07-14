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
    if school_name == "fudan_ai":
        from schools.fudan_ai import FudanAiParser
        return FudanAiParser()
    if school_name == "fudan_sds":
        from schools.fudan_sds import FudanSdsParser
        return FudanSdsParser()
    if school_name == "sjtu_sais":
        from schools.sjtu_sais import SjtuSaisParser
        return SjtuSaisParser()
    if school_name == "sjtu_see":
        from schools.sjtu_see import SjtuSeeParser
        return SjtuSeeParser()
    if school_name == "sjtu_icisee":
        from schools.sjtu_icisee import SjtuIciseeParser
        return SjtuIciseeParser()
    if school_name == "ustc_cs":
        from schools.ustc_cs import UstcCsParser
        return UstcCsParser()
    if school_name == "ruc_info":
        from schools.ruc_info import RucInfoParser
        return RucInfoParser()
    if school_name == "fudan_bme":
        from schools.fudan_bme import FudanBmeParser
        return FudanBmeParser()
    if school_name == "sjtu_bme":
        from schools.sjtu_bme import SjtuBmeParser
        return SjtuBmeParser()
    if school_name == "seu_cse":
        from schools.seu_cse import SeuCseParser
        return SeuCseParser()
    if school_name == "seu_cyber":
        from schools.seu_cyber import SeuCyberParser
        return SeuCyberParser()
    if school_name == "shanghaitech_sist":
        from schools.shanghaitech_sist import ShanghaitechSistParser
        return ShanghaitechSistParser()
    if school_name == "ustc_auto":
        from schools.ustc_auto import UstcAutoParser
        return UstcAutoParser()
    if school_name == "ustc_aids":
        from schools.ustc_aids import UstcAidsParser
        return UstcAidsParser()
    if school_name == "ict_cas":
        from schools.ict_cas import IctCasParser
        return IctCasParser()
    raise ValueError(f"未知学校解析器: {school_name}（请在 schools/ 下实现并在 get_parser 注册）")
