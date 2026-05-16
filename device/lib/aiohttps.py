# Python env   : MicroPython v1.23.0
# -*- coding: utf-8 -*-
# @Time    : 2026/04/15
# @Author  : leeqingsui
# @File    : aiohttps.py
# @Description : Async HTTPS client for MicroPython, supports streaming upload/download
# @License : MIT

__version__  = "1.1.2"
__author__   = "leeqingsui"
__license__  = "MIT"
__platform__ = "MicroPython v1.23"

# ======================================== 导入相关模块 =========================================

import socket
import ssl
import asyncio
import json
import os
import time

# ======================================== 全局变量 ============================================

# 非阻塞轮询间隔（毫秒），与 async_websocketclient 保持一致
_POLL_MS = 5

# 流式读写的分块大小（字节）
_CHUNK_SIZE = 1024

# ======================================== 功能函数 ============================================

def _parse_url(url: str) -> tuple:
    """
    解析 http:// 或 https:// URL，使用纯字符串操作，无递归风险。
    与 xfyun_tts/_WsClient.urlparse 思路相同，避免 MicroPython ure 递归溢出。

    Args:
        url (str): 目标 URL，支持 http:// 和 https://。

    Returns:
        tuple: (scheme, host, port, path_and_query)
               scheme: "http" 或 "https"
               host: 主机名字符串
               port: 整数端口号
               path_and_query: 路径+查询字符串，以 "/" 开头

    Raises:
        ValueError: URL 格式不合法或协议不支持时抛出。

    ==========================================

    Parse an http:// or https:// URL using plain string ops (no regex, no recursion).
    Same approach as xfyun_tts/_WsClient.urlparse to avoid MicroPython ure recursion overflow.

    Args:
        url (str): Target URL, supports http:// and https://.

    Returns:
        tuple: (scheme, host, port, path_and_query)
               scheme: "http" or "https"
               host: hostname string
               port: integer port number
               path_and_query: path + query string, starts with "/"

    Raises:
        ValueError: Raised on malformed URL or unsupported scheme.
    """
    # 判断协议并截取后续部分
    if url.startswith("https://"):
        scheme, rest, default_port = "https", url[8:], 443
    elif url.startswith("http://"):
        scheme, rest, default_port = "http", url[7:], 80
    else:
        raise ValueError("Unsupported scheme, only http/https allowed: " + url[:16])

    # 分离主机部分与路径部分
    slash = rest.find("/")
    if slash == -1:
        # 没有路径，默认根路径
        hostpart, path_and_query = rest, "/"
    else:
        hostpart, path_and_query = rest[:slash], rest[slash:]

    # 分离主机名与端口
    colon = hostpart.find(":")
    if colon == -1:
        host, port = hostpart, default_port
    else:
        host, port = hostpart[:colon], int(hostpart[colon + 1:])

    return scheme, host, port, path_and_query


async def _readline(sock, deadline_ms: int) -> bytes:
    """
    以非阻塞方式从 socket 读取一行（以 \\n 结尾）。
    逻辑与 async_websocketclient.a_readline 完全一致。

    Args:
        sock: 非阻塞模式的 socket 或 ssl socket 对象。

    Returns:
        bytes: 读取到的一行数据（含末尾 \\r\\n）。

    ==========================================

    Read one line from a non-blocking socket (terminated by \\n).
    Same logic as async_websocketclient.a_readline.

    Args:
        sock: Non-blocking socket or ssl socket object.

    Returns:
        bytes: One line of data including trailing \\r\\n.
    """
    line = None
    while line is None:
        if time.ticks_diff(time.ticks_ms(), deadline_ms) > 0:
            raise OSError("timeout")
        line = sock.readline()
        await asyncio.sleep_ms(_POLL_MS)
    return line


async def _read_exactly(sock, size: int) -> bytes:
    """
    以非阻塞方式从 socket 精确读取指定字节数。
    逻辑与 async_websocketclient.a_read 完全一致。

    Args:
        sock: 非阻塞模式的 socket 或 ssl socket 对象。
        size (int): 需要读取的总字节数。

    Returns:
        bytes: 精确 size 字节的数据。

    ==========================================

    Read exactly size bytes from a non-blocking socket.
    Same logic as async_websocketclient.a_read.

    Args:
        sock: Non-blocking socket or ssl socket object.
        size (int): Total bytes to read.

    Returns:
        bytes: Exactly size bytes of data.
    """
    if size == 0:
        return b""
    chunks = []
    remaining = size
    while remaining > 0:
        # 每次最多读 _CHUNK_SIZE 字节，避免单次分配过大
        chunk = sock.read(min(remaining, _CHUNK_SIZE))
        await asyncio.sleep_ms(_POLL_MS)
        if chunk is None:
            # 非阻塞无数据，继续等待
            continue
        if len(chunk) == 0:
            # 连接已关闭
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _build_request_line_and_headers(method: str, path_and_query: str,
                                     host: str, extra_headers: dict,
                                     body_len: int) -> bytes:
    """
    构造 HTTP/1.1 请求行和请求头字节串。

    Args:
        method         (str): HTTP 方法，如 "GET"、"POST"。
        path_and_query (str): 路径和查询字符串，如 "/v1/chat?x=1"。
        host           (str): 目标主机名。
        extra_headers  (dict): 调用方传入的额外请求头。
        body_len       (int): 请求体字节数，0 表示无请求体。

    Returns:
        bytes: 完整的请求行 + 请求头字节串（以空行 \\r\\n 结尾）。

    ==========================================

    Build HTTP/1.1 request line and headers as bytes.

    Args:
        method         (str): HTTP method, e.g. "GET", "POST".
        path_and_query (str): Path and query string, e.g. "/v1/chat?x=1".
        host           (str): Target hostname.
        extra_headers  (dict): Additional headers provided by caller.
        body_len       (int): Request body length in bytes; 0 means no body.

    Returns:
        bytes: Complete request line + headers ending with blank line \\r\\n.
    """
    lines = []
    # 请求行
    lines.append("{} {} HTTP/1.1".format(method, path_and_query))
    # 必要请求头
    lines.append("Host: {}".format(host))
    lines.append("Connection: close")
    if body_len > 0:
        lines.append("Content-Length: {}".format(body_len))
    # 调用方额外请求头
    if extra_headers:
        for k, v in extra_headers.items():
            lines.append("{}: {}".format(k, v))
    # 空行分隔头与体
    lines.append("")
    lines.append("")
    return "\r\n".join(lines).encode("utf-8")

# ======================================== 自定义类 ============================================

class _LineIter:
    """
    异步行迭代器，供 Response.iter_lines() 返回使用。
    逐行非阻塞读取 socket，连接关闭时结束迭代。

    ==========================================

    Async line iterator returned by Response.iter_lines().
    Reads socket line by line in non-blocking mode; ends when connection closes.
    """

    def __init__(self, resp) -> None:
        """
        初始化迭代器。

        Args:
            resp: Response 对象，为 None 时迭代器立即结束。

        ==========================================

        Initialize the iterator.

        Args:
            resp: Response object; if None the iterator ends immediately.
        """
        # 保存 Response 引用
        self._resp = resp

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._resp is None or self._resp._sock is None:
            raise StopAsyncIteration
        while True:
            line = self._resp._sock.readline()
            await asyncio.sleep_ms(_POLL_MS)
            if line is None:
                # 非阻塞无数据，继续等待
                continue
            if len(line) == 0:
                # 连接已关闭
                self._resp.close()
                raise StopAsyncIteration
            return line


class Response:
    """
    HTTP 响应对象，支持全量读取文本/JSON 和流式写入文件两种模式。

    全量读取适合小响应（LLM 文字回答，几 KB）；
    流式写入适合大响应（TTS 音频、图片 base64，几十 KB 以上），内存峰值仅为单块大小。

    Attributes:
        status  (int): HTTP 状态码。
        headers (dict): 响应头字典（键统一转为小写）。
        _sock: 底层 socket 对象，读取完毕后由 Response 负责关闭。
        _body_read (bool): 标记响应体是否已经被读取过。

    Methods:
        text: property，全量读取响应体为 UTF-8 字符串。
        json(): 全量读取响应体并解析为 dict。
        save(filepath): 流式将响应体写入文件，不占用大块内存。
        close(): 关闭底层 socket。

    Notes:
        text / json() / save() 只能调用其中一个，调用后 socket 被关闭，重复调用返回空。

    ==========================================

    HTTP response object supporting both full-read (text/JSON) and streaming-save modes.

    Full-read is suitable for small responses (LLM text, a few KB).
    Streaming-save is suitable for large responses (TTS audio, images), peak RAM is one chunk.

    Attributes:
        status  (int): HTTP status code.
        headers (dict): Response headers dict (keys lowercased).
        _sock: Underlying socket; closed by Response after body is consumed.
        _body_read (bool): Whether the body has already been consumed.

    Methods:
        text: property, read full response body as UTF-8 string.
        json(): Read full response body and parse as dict.
        save(filepath): Stream response body to file without large memory allocation.
        close(): Close the underlying socket.

    Notes:
        Only one of text / json() / save() may be called; socket is closed afterward.
    """

    def __init__(self, status: int, headers: dict, sock: object, deadline_ms: int) -> None:
        """
        初始化响应对象，保存状态码、响应头和底层 socket。

        Args:
            status  (int): HTTP 状态码。
            headers (dict): 已解析的响应头字典。
            sock: 非阻塞 socket，指向响应体起始位置。

        ==========================================

        Initialize response with status code, headers and underlying socket.

        Args:
            status  (int): HTTP status code.
            headers (dict): Parsed response headers dict.
            sock: Non-blocking socket positioned at start of response body.
        """
        if not isinstance(status, int):
            raise TypeError("status must be int, got {}".format(type(status).__name__))
        if not isinstance(headers, dict):
            raise TypeError("headers must be dict, got {}".format(type(headers).__name__))

        self.status      = status
        self.headers     = headers
        self._sock       = sock
        self._deadline_ms = deadline_ms
        self._body_read  = False
        self._body_cache = None

    def close(self) -> None:
        """
        关闭底层 socket，释放连接资源。

        ==========================================

        Close the underlying socket and release connection resources.
        """
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    async def _read_body(self) -> bytes:
        """
        内部方法：按 Content-Length 或读到连接关闭的方式读取完整响应体。

        Returns:
            bytes: 完整响应体字节串。

        ==========================================

        Internal: read full response body by Content-Length or until connection close.

        Returns:
            bytes: Complete response body bytes.
        """
        if self._body_cache is not None:
            # 已缓存，直接返回
            return self._body_cache

        if self._body_read or self._sock is None:
            return b""

        # 标记已读，防止重复消费
        self._body_read = True

        content_length = int(self.headers.get("content-length", -1))

        chunks = []
        try:
            if content_length >= 0:
                remaining = content_length
                while remaining > 0:
                    if time.ticks_diff(time.ticks_ms(), self._deadline_ms) > 0:
                        raise OSError("timeout")
                    chunk = self._sock.read(min(remaining, _CHUNK_SIZE))
                    await asyncio.sleep_ms(_POLL_MS)
                    if chunk is None:
                        continue
                    if len(chunk) == 0:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
            else:
                while True:
                    if time.ticks_diff(time.ticks_ms(), self._deadline_ms) > 0:
                        raise OSError("timeout")
                    chunk = self._sock.read(_CHUNK_SIZE)
                    await asyncio.sleep_ms(_POLL_MS)
                    if chunk is None:
                        continue
                    if len(chunk) == 0:
                        break
                    chunks.append(chunk)
        finally:
            self.close()

        self._body_cache = b"".join(chunks)
        return self._body_cache

    @property
    async def text(self) -> str:
        """
        全量读取响应体并解码为 UTF-8 字符串。适合 LLM 文字回答等小响应。

        Returns:
            str: UTF-8 解码后的响应体字符串。

        ==========================================

        Read full response body and decode as UTF-8 string. Suitable for small responses.

        Returns:
            str: Response body decoded as UTF-8 string.
        """
        return (await self._read_body()).decode("utf-8")

    async def json(self) -> dict:
        """
        全量读取响应体并解析为 Python 字典。

        Returns:
            dict: JSON 解析后的响应体。

        ==========================================

        Read full response body and parse as Python dict.

        Returns:
            dict: Parsed JSON response body.
        """
        return json.loads(await self._read_body())

    def iter_lines(self):
        """
        返回异步行迭代器，适合 SSE（text/event-stream）场景。
        每次 __anext__ 返回一行 bytes，连接关闭时抛出 StopAsyncIteration。

        Returns:
            _LineIter: 异步迭代器对象。

        Notes:
            与 text / json() / save() 互斥，调用后 socket 被消费。

        ==========================================

        Return an async line iterator for SSE (text/event-stream) streaming.
        Each __anext__ returns one bytes line; raises StopAsyncIteration on close.

        Returns:
            _LineIter: Async iterator object.

        Notes:
            Mutually exclusive with text / json() / save().
        """
        if self._body_read or self._sock is None:
            return _LineIter(None)
        # 标记已读，防止重复消费
        self._body_read = True
        return _LineIter(self)

    async def save(self, filepath: str) -> int:
        """
        流式将响应体写入文件，内存峰值仅为单块大小（_CHUNK_SIZE 字节）。
        适合 TTS 音频、图片等大响应，避免 OOM。

        Args:
            filepath (str): 目标文件路径，文件以二进制写入模式打开。

        Returns:
            int: 实际写入的总字节数。

        Raises:
            ValueError: filepath 为 None 或空字符串时抛出。
            TypeError:  filepath 不是字符串时抛出。

        ==========================================

        Stream response body to file; peak RAM is one chunk (_CHUNK_SIZE bytes).
        Suitable for large responses (TTS audio, images) to avoid OOM.

        Args:
            filepath (str): Destination file path, opened in binary write mode.

        Returns:
            int: Total bytes written.

        Raises:
            ValueError: filepath is None or empty string.
            TypeError:  filepath is not a string.
        """
        if filepath is None:
            raise ValueError("filepath cannot be None")
        if not isinstance(filepath, str):
            raise TypeError("filepath must be str, got {}".format(type(filepath).__name__))
        if len(filepath) == 0:
            raise ValueError("filepath cannot be empty")

        if self._body_read or self._sock is None:
            return 0

        # 标记已读，防止重复消费
        self._body_read = True

        content_length = int(self.headers.get("content-length", -1))
        total = 0

        try:
            with open(filepath, "wb") as f:
                if content_length >= 0:
                    remaining = content_length
                    while remaining > 0:
                        if time.ticks_diff(time.ticks_ms(), self._deadline_ms) > 0:
                            raise OSError("timeout")
                        chunk = self._sock.read(min(remaining, _CHUNK_SIZE))
                        await asyncio.sleep_ms(_POLL_MS)
                        if chunk is None:
                            continue
                        if len(chunk) == 0:
                            break
                        f.write(chunk)
                        total += len(chunk)
                        remaining -= len(chunk)
                else:
                    while True:
                        if time.ticks_diff(time.ticks_ms(), self._deadline_ms) > 0:
                            raise OSError("timeout")
                        chunk = self._sock.read(_CHUNK_SIZE)
                        await asyncio.sleep_ms(_POLL_MS)
                        if chunk is None:
                            continue
                        if len(chunk) == 0:
                            break
                        f.write(chunk)
                        total += len(chunk)
        finally:
            self.close()

        return total


async def request(method: str, url: str,
                  headers: dict = None, data=None,
                  timeout_ms: int = 30000) -> Response:
    """
    发起一次异步 HTTPS/HTTP 请求，返回 Response 对象。

    data 参数支持三种类型：
    - str:  直接编码为 UTF-8 发送（适合 JSON 请求体）
    - bytes: 直接发送（适合小块二进制）
    - str（文件路径，以 "/" 或字母开头且文件存在）: 流式读取文件上传，
      先用 os.stat() 获取文件大小填入 Content-Length，再分块发送，
      内存峰值仅为单块大小。

    Args:
        method  (str): HTTP 方法，大写，如 "GET"、"POST"。
        url     (str): 目标 URL，支持 http:// 和 https://。
        headers (dict, optional): 额外请求头，如 Authorization、Content-Type。
        data    (str | bytes | None, optional): 请求体。
            - None: 无请求体（GET 等）
            - str 且对应文件存在: 流式上传文件
            - str 否则: 编码为 UTF-8 字节发送
            - bytes: 直接发送

    Returns:
        Response: 包含 status、headers 和待读 socket 的响应对象。

    Raises:
        ValueError: url/method 为空或格式不合法时抛出。
        TypeError:  参数类型不符合要求时抛出。
        OSError:    网络连接或 TLS 握手失败时抛出。
        Exception:  服务端返回非 2xx 状态码时不抛出，由调用方检查 resp.status。

    ==========================================

    Make an async HTTPS/HTTP request and return a Response object.

    data parameter supports three types:
    - str:  encoded as UTF-8 and sent directly (for JSON request body)
    - bytes: sent as-is (for small binary payloads)
    - str (file path, file exists): streamed from file with Content-Length from os.stat(),
      peak RAM is one chunk only.

    Args:
        method  (str): HTTP method in uppercase, e.g. "GET", "POST".
        url     (str): Target URL, supports http:// and https://.
        headers (dict, optional): Extra headers, e.g. Authorization, Content-Type.
        data    (str | bytes | None, optional): Request body.
            - None: no body (GET etc.)
            - str with existing file path: stream-upload file
            - str otherwise: encode as UTF-8 bytes and send
            - bytes: send as-is

    Returns:
        Response: Response object containing status, headers and unread socket.

    Raises:
        ValueError: url/method is empty or malformed.
        TypeError:  Parameter type mismatch.
        OSError:    Network connection or TLS handshake failure.
        Exception:  Non-2xx status does NOT raise; caller should check resp.status.
    """
    # 参数校验
    if method is None:
        raise ValueError("method cannot be None")
    if not isinstance(method, str) or len(method) == 0:
        raise ValueError("method must be a non-empty string")

    if url is None:
        raise ValueError("url cannot be None")
    if not isinstance(url, str) or len(url) == 0:
        raise ValueError("url must be a non-empty string")

    if headers is not None and not isinstance(headers, dict):
        raise TypeError("headers must be dict, got {}".format(type(headers).__name__))

    # 解析 URL
    scheme, host, port, path_and_query = _parse_url(url)

    # 判断请求体类型和大小
    is_file_upload = False
    body_bytes     = None
    body_len       = 0

    if data is not None:
        if isinstance(data, str):
            # 判断是否为文件路径：尝试 os.stat，成功则为文件上传
            try:
                stat = os.stat(data)
                # stat[6] 为文件大小
                body_len      = stat[6]
                is_file_upload = True
            except OSError:
                # 不是文件路径，当作普通字符串编码发送
                body_bytes = data.encode("utf-8")
                body_len   = len(body_bytes)
        elif isinstance(data, bytes):
            body_bytes = data
            body_len   = len(body_bytes)
        else:
            raise TypeError("data must be str, bytes or None, got {}".format(
                type(data).__name__))

    # 计算超时截止时间
    deadline_ms = time.ticks_add(time.ticks_ms(), timeout_ms)

    # 建立 TCP 连接
    sock = socket.socket()
    ai   = socket.getaddrinfo(host, port)
    addr = ai[0][4]
    sock.connect(addr)
    # 设为非阻塞，与 async_websocketclient 保持一致
    sock.setblocking(False)

    # HTTPS：用 ssl.wrap_socket 包装，cert_reqs=0 跳过证书验证
    if scheme == "https":
        sock = ssl.wrap_socket(
            sock,
            server_side      = False,
            cert_reqs        = 0,          # CERT_NONE，不验证服务端证书
            server_hostname  = host,       # SNI，让服务端知道访问的域名
        )

    # 发送请求行和请求头
    req_headers = _build_request_line_and_headers(
        method, path_and_query, host,
        headers or {}, body_len
    )
    sock.write(req_headers)

    # 发送请求体
    if is_file_upload:
        # 流式读文件上传，内存峰值仅为单块大小
        with open(data, "rb") as f:
            while True:
                chunk = f.read(_CHUNK_SIZE)
                if not chunk:
                    break
                sock.write(chunk)
                # 让出 CPU，避免长时间阻塞事件循环
                await asyncio.sleep_ms(_POLL_MS)
    elif body_bytes is not None:
        # 分块发送，避免大 payload 一次性写满非阻塞 socket 缓冲区
        offset = 0
        while offset < len(body_bytes):
            sock.write(body_bytes[offset:offset + _CHUNK_SIZE])
            offset += _CHUNK_SIZE
            await asyncio.sleep_ms(_POLL_MS)

    # 读取响应状态行
    status_line = await _readline(sock, deadline_ms)
    parts  = status_line.split(None, 2)
    status = int(parts[1])

    # 读取响应头直到空行
    resp_headers = {}
    while True:
        line = await _readline(sock, deadline_ms)
        if line == b"\r\n" or line == b"\n" or len(line) == 0:
            break
        if b":" in line:
            k, v = line.split(b":", 1)
            resp_headers[k.strip().lower().decode("utf-8")] = v.strip().decode("utf-8")

    return Response(status, resp_headers, sock, deadline_ms)


async def get(url: str, headers: dict = None, timeout_ms: int = 30000) -> Response:
    """
    发起异步 GET 请求的便捷函数。

    Args:
        url     (str): 目标 URL。
        headers (dict, optional): 额外请求头。

    Returns:
        Response: HTTP 响应对象。

    ==========================================

    Convenience function for async GET request.

    Args:
        url     (str): Target URL.
        headers (dict, optional): Extra request headers.

    Returns:
        Response: HTTP response object.
    """
    return await request("GET", url, headers=headers, timeout_ms=timeout_ms)


async def post(url: str, headers: dict = None, data=None, timeout_ms: int = 30000) -> Response:
    """
    发起异步 POST 请求的便捷函数。

    Args:
        url     (str): 目标 URL。
        headers (dict, optional): 额外请求头。
        data    (str | bytes | None, optional): 请求体。

    Returns:
        Response: HTTP 响应对象。

    ==========================================

    Convenience function for async POST request.

    Args:
        url     (str): Target URL.
        headers (dict, optional): Extra request headers.
        data    (str | bytes | None, optional): Request body.

    Returns:
        Response: HTTP response object.
    """
    return await request("POST", url, headers=headers, data=data, timeout_ms=timeout_ms)

# ======================================== 初始化配置 ===========================================

# ========================================  主程序  ===========================================
