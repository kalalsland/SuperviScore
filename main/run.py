# -*- coding: utf-8 -*-
"""套磁导师推荐工具 —— 入口。

用法：
    python run.py            # 按 config.py 配置跑（LIMIT=0 即全量）
    python run.py 3          # 临时只跑前 3 位老师（冒烟测试，覆盖 config.LIMIT）

换学校：改 config.py 的 SCHOOL（及 SCHOOL_DBLP_AFFILIATION），并在 schools/ 下
实现对应解析器即可，core/ 不动。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.utils import force_utf8
force_utf8()

import config
from core import pipeline


def main():
    if len(sys.argv) > 1:
        try:
            config.LIMIT = int(sys.argv[1])
        except ValueError:
            pass
    pipeline.run()


if __name__ == "__main__":
    main()
