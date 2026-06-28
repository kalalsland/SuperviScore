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


# --- 走 VPN 代理的独立会话：仅 Scholar/GitHub 用（DBLP/arXiv 仍直连）---
# 国内直连 scholar.google.com / api.github.com 常被墙（SSL EOF）；这两个站点
# 改走系统/VPN 代理。代理地址优先取 config.SCHOLAR_PROXY，其次系统代理。
_proxy_session = None


def _detect_system_proxy():
    """从环境变量或 Windows 注册表读系统代理，返回 'http://host:port' 或 ''。"""
    for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        v = _os.environ.get(k)
        if v:
            return v if "://" in v else f"http://{v}"
    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Internet Settings")
            enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if enable:
                server, _ = winreg.QueryValueEx(key, "ProxyServer")
                if server and "=" not in server:      # 简单 host:port（非 per-protocol）
                    return f"http://{server}"
        except Exception:
            pass
    return ""


def _get_proxy_session():
    global _proxy_session
    if _proxy_session is not None:
        return _proxy_session
    import config
    proxy = getattr(config, "SCHOLAR_PROXY", "") or _detect_system_proxy()
    s = requests.Session()
    s.headers.update(_session.headers)
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
        log(f"[proxy] Scholar/GitHub 走代理 {proxy}")
    else:
        log("[proxy] 未发现可用代理，Scholar/GitHub 直连（国内可能不可达）")
    _proxy_session = s
    return s


def http_get_proxied(url, timeout=30, retries=2, backoff=3.0, **kw):
    """经 VPN 代理的 GET（Scholar/GitHub 专用）。"""
    sess = _get_proxy_session()
    kw.setdefault("cookies", {"CONSENT": "YES+cb"})   # 跳过 Google 同意页
    last = None
    for attempt in range(retries):
        try:
            resp = sess.get(url, timeout=timeout, **kw)
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


def http_post_proxied(url, data=None, timeout=30, retries=2, backoff=3.0, **kw):
    """经 VPN 代理的 POST（DuckDuckGo HTML 搜索用）。"""
    sess = _get_proxy_session()
    last = None
    for attempt in range(retries):
        try:
            resp = sess.post(url, data=data, timeout=timeout, **kw)
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
