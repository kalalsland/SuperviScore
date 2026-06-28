# -*- coding: utf-8 -*-
"""通用工具：UTF-8 输出、HTTP 会话、磁盘缓存、日志。"""
from __future__ import annotations
import sys
import os
import io
import json
import time
import hashlib
import requests

# --- 强制 UTF-8 stdout/stderr（Windows 控制台默认 GBK 会让中文 print 崩溃）---
def force_utf8():
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name)
        try:
            stream.reconfigure(encoding="utf-8")          # py3.7+
        except Exception:
            try:
                setattr(sys, stream_name,
                        io.TextIOWrapper(stream.buffer, encoding="utf-8", errors="replace"))
            except Exception:
                pass


force_utf8()

_session = requests.Session()
# 忽略系统代理（Windows 注册表里常配有 clash/VPN 代理，会导致 DBLP/arXiv 连接被重置；
# 直连可达，与 curl 行为一致）。如确需走代理，设环境变量 TAOCI_USE_PROXY=1。
import os as _os
if _os.environ.get("TAOCI_USE_PROXY") != "1":
    _session.trust_env = False
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    # DBLP 会主动掐 keep-alive 连接导致 RemoteDisconnected，禁用连接复用更稳
    "Connection": "close",
})

# 需要重试的 HTTP 状态码（限流/暂时不可用）
_RETRY_STATUS = {429, 500, 502, 503, 504}


def _retry_wait(resp, attempt, backoff):
    """优先尊重 Retry-After，否则线性退避 + 轻微抖动。"""
    if resp is not None:
        ra = resp.headers.get("Retry-After")
        if ra:
            try:
                return min(60.0, float(ra))
            except ValueError:
                pass
    # attempt 从 0 起：backoff, 2*backoff, 3*backoff ... + 抖动
    jitter = 0.3 * ((attempt * 7) % 5)   # 0~1.2s，无需随机源
    return backoff * (attempt + 1) + jitter


def http_get(url, timeout=30, retries=4, backoff=4.0, **kw):
    last = None
    for attempt in range(retries):
        try:
            resp = _session.get(url, timeout=timeout, **kw)
            if resp.status_code in _RETRY_STATUS and attempt < retries - 1:
                time.sleep(_retry_wait(resp, attempt, backoff))
                continue
            return resp
        except Exception as e:
            last = e
            if attempt < retries - 1:
                time.sleep(_retry_wait(None, attempt, backoff))
    if last:
        raise last
    return resp


def http_post(url, data=None, timeout=30, retries=4, backoff=4.0, **kw):
    last = None
    for attempt in range(retries):
        try:
            resp = _session.post(url, data=data, timeout=timeout, **kw)
            if resp.status_code in _RETRY_STATUS and attempt < retries - 1:
                time.sleep(_retry_wait(resp, attempt, backoff))
                continue
            return resp
        except Exception as e:
            last = e
            if attempt < retries - 1:
                time.sleep(_retry_wait(None, attempt, backoff))
    if last:
        raise last
    return resp


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# 磁盘缓存：按 (namespace, key) 存 JSON / 文本，断点续跑、省 token
# ---------------------------------------------------------------------------
class Cache:
    def __init__(self, root, enabled=True):
        self.root = root
        self.enabled = enabled
        if enabled:
            os.makedirs(root, exist_ok=True)

    def _path(self, namespace, key):
        h = hashlib.md5(key.encode("utf-8")).hexdigest()[:16]
        d = os.path.join(self.root, namespace)
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, f"{h}.json")

    def get(self, namespace, key):
        if not self.enabled:
            return None
        p = self._path(namespace, key)
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
        return None

    def set(self, namespace, key, value):
        if not self.enabled:
            return
        p = self._path(namespace, key)
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(value, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log(f"[cache] 写入失败 {namespace}/{key}: {e}")


def polite_sleep(seconds):
    if seconds and seconds > 0:
        time.sleep(seconds)
