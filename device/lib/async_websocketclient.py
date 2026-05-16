# Python env   : MicroPython v1.23.0
# -*- coding: utf-8 -*-
# @Time    : 2026/04/12
# @Author  : vovaman
# @File    : async_websocketclient.py
# @Description : Async WebSocket client for MicroPython
# @License : MIT
# 参考自：https://pypi.org/project/micropython-async-websocket-client/

# ======================================== 导入相关模块 =========================================

import socket
import asyncio as a
import binascii as b
import random as r
from collections import namedtuple
import re
import struct
import ssl

# ======================================== 全局变量 ============================================

# 兼容标准 Python（MicroPython 有 const，标准 Python 没有）
try:
    const
except NameError:
    const = lambda x: x

# Opcodes
OP_CONT = const(0x0)
OP_TEXT = const(0x1)
OP_BYTES = const(0x2)
OP_CLOSE = const(0x8)
OP_PING = const(0x9)
OP_PONG = const(0xa)

# Close codes
CLOSE_OK = const(1000)
CLOSE_GOING_AWAY = const(1001)
CLOSE_PROTOCOL_ERROR = const(1002)
CLOSE_DATA_NOT_SUPPORTED = const(1003)
CLOSE_BAD_DATA = const(1007)
CLOSE_POLICY_VIOLATION = const(1008)
CLOSE_TOO_BIG = const(1009)
CLOSE_MISSING_EXTN = const(1010)
CLOSE_BAD_CONDITION = const(1011)

URL_RE = re.compile(r'(wss|ws)://([A-Za-z0-9-\.]+)(?:\:([0-9]+))?(/.+)?')
URI = namedtuple('URI', ('protocol', 'hostname', 'port', 'path'))

# ======================================== 功能函数 ============================================

# ======================================== 自定义类 ============================================

class AsyncWebsocketClient:
    """
    基于 asyncio 的 MicroPython 异步 WebSocket 客户端，支持 ws:// 和 wss:// 协议，
    通过非阻塞轮询方式实现异步读写。

    Attributes:
        _open (bool): 连接状态标志。
        delay_read (int): 非阻塞轮询间隔，单位毫秒。
        _lock_for_open (asyncio.Lock): 保护连接状态的互斥锁。
        sock: 底层 socket 或 ssl socket 对象。

    Methods:
        open(new_val): 查询或设置连接状态。
        close(): 关闭连接。
        urlparse(uri): 解析 ws/wss URL。
        a_readline(): 异步读取一行。
        a_read(size): 异步读取指定字节数。
        handshake(uri, ...): 建立 WebSocket 连接并完成握手。
        read_frame(max_size): 读取一个 WebSocket 帧。
        write_frame(opcode, data): 发送一个 WebSocket 帧。
        recv(): 接收消息（文本或二进制）。
        send(buf): 发送消息（文本或二进制）。

    Notes:
        - 所有 I/O 方法均为协程，需配合 asyncio.run() 使用。
        - wss:// 连接通过 ssl.wrap_socket() 实现，cert_reqs=0 时不验证证书。

    ==========================================

    Async WebSocket client for MicroPython based on asyncio.
    Supports ws:// and wss:// protocols with non-blocking polling I/O.

    Attributes:
        _open (bool): Connection state flag.
        delay_read (int): Non-blocking polling interval in milliseconds.
        _lock_for_open (asyncio.Lock): Mutex protecting connection state.
        sock: Underlying socket or ssl socket object.

    Methods:
        open(new_val): Query or set connection state.
        close(): Close the connection.
        urlparse(uri): Parse ws/wss URL.
        a_readline(): Async read one line.
        a_read(size): Async read specified number of bytes.
        handshake(uri, ...): Establish WebSocket connection and complete handshake.
        read_frame(max_size): Read one WebSocket frame.
        write_frame(opcode, data): Send one WebSocket frame.
        recv(): Receive a message (text or binary).
        send(buf): Send a message (text or binary).

    Notes:
        - All I/O methods are coroutines, must be used with asyncio.run().
        - wss:// uses ssl.wrap_socket(); cert_reqs=0 skips certificate verification.
    """

    def __init__(self, ms_delay_for_read: int = 5):
        """
        初始化 WebSocket 客户端，设置轮询延迟和内部状态。

        Args:
            ms_delay_for_read (int): 非阻塞轮询间隔（毫秒），默认 5ms。
                                     值越小响应越快，值越大 CPU 占用越低。

        ==========================================

        Initialize the WebSocket client with polling delay and internal state.

        Args:
            ms_delay_for_read (int): Non-blocking polling interval in ms, default 5.
                                     Smaller values improve responsiveness; larger values reduce CPU usage.
        """
        self._open = False
        self.delay_read = ms_delay_for_read
        self._lock_for_open = a.Lock()
        self.sock = None

    async def open(self, new_val: bool = None):
        """
        查询或设置连接状态，操作受互斥锁保护。

        Args:
            new_val (bool, optional): 若为 False 则关闭 socket 并清空；
                                      若为 True 则标记为已连接；
                                      若为 None 则仅查询当前状态。

        Returns:
            bool: 当前连接状态。

        ==========================================

        Query or set the connection state, protected by a mutex lock.

        Args:
            new_val (bool, optional): If False, close and clear socket;
                                      if True, mark as connected;
                                      if None, only query current state.

        Returns:
            bool: Current connection state.
        """
        await self._lock_for_open.acquire()
        if new_val is not None:
            if not new_val and self.sock:
                self.sock.close()
                self.sock = None
            self._open = new_val
        to_return = self._open
        self._lock_for_open.release()
        return to_return

    async def close(self):
        """
        关闭 WebSocket 连接。

        Returns:
            bool: 关闭后的连接状态（始终为 False）。

        ==========================================

        Close the WebSocket connection.

        Returns:
            bool: Connection state after closing (always False).
        """
        return await self.open(False)

    def urlparse(self, uri):
        """
        解析 ws:// 或 wss:// 格式的 URL。

        Args:
            uri (str): WebSocket URL，格式为 ws://host[:port][/path] 或 wss://...

        Returns:
            URI: 包含 protocol、hostname、port、path 的具名元组。

        Raises:
            ValueError: 协议不是 ws 或 wss 时抛出。

        ==========================================

        Parse a ws:// or wss:// URL.

        Args:
            uri (str): WebSocket URL in ws://host[:port][/path] or wss://... format.

        Returns:
            URI: Named tuple containing protocol, hostname, port, path.

        Raises:
            ValueError: Raised when scheme is not ws or wss.
        """
        match = URL_RE.match(uri)
        if match:
            protocol, host, port, path = match.group(1), match.group(2), match.group(3), match.group(4)

            if protocol not in ['ws', 'wss']:
                raise ValueError('Scheme {} is invalid'.format(protocol))

            if port is None:
                port = (80, 443)[protocol == 'wss']

            return URI(protocol, host, int(port), path)

    async def a_readline(self):
        """
        以非阻塞方式从 socket 读取一行（以 \\n 结尾）。

        Returns:
            bytes: 读取到的一行数据。

        Notes:
            每次 socket 未就绪时，等待 delay_read 毫秒再重试。

        ==========================================

        Read one line from socket in non-blocking mode (terminated by \\n).

        Returns:
            bytes: One line of data read from socket.

        Notes:
            Waits delay_read ms between retries when socket is not ready.
        """
        line = None
        while line is None:
            line = self.sock.readline()
            await a.sleep_ms(self.delay_read)

        return line

    async def a_read(self, size: int = None):
        """
        以非阻塞方式从 socket 精确读取指定字节数。

        Args:
            size (int, optional): 需要读取的字节数；为 0 时直接返回空字节串。

        Returns:
            bytes: 读取到的数据，所有分片拼接后返回。

        Notes:
            循环读取直到累计字节数达到 size，期间通过 sleep_ms 让出 CPU。

        ==========================================

        Read exactly the specified number of bytes from socket in non-blocking mode.

        Args:
            size (int, optional): Number of bytes to read; returns b'' immediately if 0.

        Returns:
            bytes: Data read, all chunks joined before returning.

        Notes:
            Loops until cumulative bytes reach size, yielding CPU via sleep_ms.
        """
        if size == 0:
            return b''
        chunks = []

        while True:
            b = self.sock.read(size)
            await a.sleep_ms(self.delay_read)

            # Continue reading if the socket returns None
            if b is None: continue

            # In some cases, the socket will return an empty bytes
            # after PING or PONG frames, we need to ignore them.
            if len(b) == 0: break

            chunks.append(b)
            size -= len(b)

            # After reading the first chunk, we can break if size is None or 0
            if size is None or size == 0: break

        # Join all the chunks and return them
        return b''.join(chunks)

    async def handshake(self, uri, headers=[], keyfile=None, certfile=None, cafile=None, cert_reqs=0):
        """
        建立 TCP 连接并完成 HTTP Upgrade 握手，将连接升级为 WebSocket 协议。

        Args:
            uri (str): WebSocket 服务器地址，支持 ws:// 和 wss://。
            headers (list): 额外的 HTTP 请求头，格式为 [(key, value), ...]。
            keyfile (str, optional): 客户端私钥文件路径（用于双向 TLS）。
            certfile (str, optional): 客户端证书文件路径（用于双向 TLS）。
            cafile (str, optional): CA 证书文件路径（用于服务端验证）。
            cert_reqs (int): TLS 证书验证模式，0=不验证，1=可选，2=必须。默认 0。

        Returns:
            bool: 握手成功后返回 True。

        Raises:
            Exception: HTTP 响应不是 101 Switching Protocols 时抛出。

        Notes:
            鉴权参数（如讯飞 API 的 authorization）可直接拼入 uri 的查询字符串中传递。

        ==========================================

        Establish TCP connection and complete HTTP Upgrade handshake to WebSocket protocol.

        Args:
            uri (str): WebSocket server address, supports ws:// and wss://.
            headers (list): Additional HTTP request headers as [(key, value), ...].
            keyfile (str, optional): Client private key file path (for mutual TLS).
            certfile (str, optional): Client certificate file path (for mutual TLS).
            cafile (str, optional): CA certificate file path (for server verification).
            cert_reqs (int): TLS certificate verification, 0=none, 1=optional, 2=required. Default 0.

        Returns:
            bool: True on successful handshake.

        Raises:
            Exception: Raised when HTTP response is not 101 Switching Protocols.

        Notes:
            Auth parameters (e.g. iFlytek API authorization) can be embedded in the uri query string.
        """
        if self.sock:
            self.close()

        self.sock = socket.socket()
        self.uri = self.urlparse(uri)
        ai = socket.getaddrinfo(self.uri.hostname, self.uri.port)
        addr = ai[0][4]

        self.sock.connect(addr)
        self.sock.setblocking(False)

        if self.uri.protocol == 'wss':
            cadata = None
            if not cafile is None:
                with open(cafile, 'rb') as f:
                    cadata = f.read()
            self.sock = ssl.wrap_socket(
                self.sock, server_side=False,
                key=keyfile, cert=certfile,
                cert_reqs=cert_reqs, # 0 - NONE, 1 - OPTIONAL, 2 - REQUIED
                cadata=cadata,
                server_hostname=self.uri.hostname
            )

        def send_header(header, *args):
            # CPython bytes-% requires bytes args; MicroPython accepts str too
            bargs = tuple(
                a if isinstance(a, (bytes, bytearray))
                else str(a).encode('utf-8')
                for a in args
            )
            self.sock.write(header % bargs + b'\r\n')

        # Sec-WebSocket-Key is 16 bytes of random base64 encoded
        key = b.b2a_base64(bytes(r.getrandbits(8)
                                        for _ in range(16)))[:-1]

        send_header(b'GET %s HTTP/1.1', self.uri.path or '/')
        send_header(b'Host: %s:%s', self.uri.hostname, self.uri.port)
        send_header(b'Connection: Upgrade')
        send_header(b'Upgrade: websocket')
        send_header(b'Sec-WebSocket-Key: %s', key)
        send_header(b'Sec-WebSocket-Version: 13')
        # bytes.format() 仅 MicroPython 支持，改用 %s 保持 CPython 兼容
        send_header(b'Origin: http://%s:%s', self.uri.hostname, self.uri.port)

        for key, value in headers:
            send_header(b'%s: %s', key, value)

        send_header(b'')

        line = await self.a_readline()
        header = (line)[:-2]
        if not header.startswith(b'HTTP/1.1 101 '):
            raise Exception(header)

        # We don't (currently) need these headers
        # FIXME: should we check the return key?
        while header:
            line = await self.a_readline()
            header = (line)[:-2]

        return await self.open(True)

    async def read_frame(self, max_size=None):
        """
        从 socket 读取并解析一个完整的 WebSocket 帧。

        Args:
            max_size: 预留参数，当前未使用。

        Returns:
            tuple: (fin: bool, opcode: int, data: bytes)
                   fin 表示是否为最后一帧，opcode 为帧类型，data 为帧载荷。

        Notes:
            当帧载荷过大导致 MemoryError 时，返回 (True, OP_CLOSE, None) 并关闭连接。

        ==========================================

        Read and parse one complete WebSocket frame from socket.

        Args:
            max_size: Reserved parameter, currently unused.

        Returns:
            tuple: (fin: bool, opcode: int, data: bytes)
                   fin indicates final frame, opcode is frame type, data is payload.

        Notes:
            Returns (True, OP_CLOSE, None) and closes connection on MemoryError.
        """
        # Frame header
        byte1, byte2 = struct.unpack('!BB', await self.a_read(2))

        # Byte 1: FIN(1) _(1) _(1) _(1) OPCODE(4)
        fin = bool(byte1 & 0x80)
        opcode = byte1 & 0x0f

        # Byte 2: MASK(1) LENGTH(7)
        mask = bool(byte2 & (1 << 7))
        length = byte2 & 0x7f

        if length == 126:  # Magic number, length header is 2 bytes
            length, = struct.unpack('!H', await self.a_read(2))
        elif length == 127:  # Magic number, length header is 8 bytes
            length, = struct.unpack('!Q', await self.a_read(8))

        if mask:  # Mask is 4 bytes
            mask_bits = await self.a_read(4)

        try:
            data = await self.a_read(length)
        except MemoryError:
            # We can't receive this many bytes, close the socket
            self.close(code=CLOSE_TOO_BIG)
            # await self._stream.drain()
            return True, OP_CLOSE, None

        if mask:
            data = bytes(b ^ mask_bits[i % 4]
                         for i, b in enumerate(data))

        return fin, opcode, data

    def write_frame(self, opcode, data=b''):
        """
        构造并发送一个带掩码的 WebSocket 帧（客户端发送必须掩码）。

        Args:
            opcode (int): 帧类型，如 OP_TEXT(0x1)、OP_BYTES(0x2)、OP_PONG(0xA)。
            data (bytes): 帧载荷数据，默认为空字节串。

        Notes:
            根据载荷长度自动选择 7-bit、16-bit 或 64-bit 长度编码。
            掩码密钥由 random.getrandbits(32) 生成。

        ==========================================

        Build and send one masked WebSocket frame (client frames must be masked per RFC 6455).

        Args:
            opcode (int): Frame type, e.g. OP_TEXT(0x1), OP_BYTES(0x2), OP_PONG(0xA).
            data (bytes): Frame payload, defaults to empty bytes.

        Notes:
            Automatically selects 7-bit, 16-bit, or 64-bit length encoding based on payload size.
            Mask key is generated by random.getrandbits(32).
        """
        fin = True
        mask = True  # messages sent by client are masked

        length = len(data)

        # Frame header
        # Byte 1: FIN(1) _(1) _(1) _(1) OPCODE(4)
        byte1 = 0x80 if fin else 0
        byte1 |= opcode

        # Byte 2: MASK(1) LENGTH(7)
        byte2 = 0x80 if mask else 0

        if length < 126:  # 126 is magic value to use 2-byte length header
            byte2 |= length
            self.sock.write(struct.pack('!BB', byte1, byte2))

        elif length < (1 << 16):  # Length fits in 2-bytes
            byte2 |= 126  # Magic code
            self.sock.write(struct.pack('!BBH', byte1, byte2, length))

        elif length < (1 << 64):
            byte2 |= 127  # Magic code
            self.sock.write(struct.pack('!BBQ', byte1, byte2, length))

        else:
            raise ValueError()

        if mask:  # Mask is 4 bytes
            mask_bits = struct.pack('!I', r.getrandbits(32))
            self.sock.write(mask_bits)
            data = bytes(b ^ mask_bits[i % 4]
                         for i, b in enumerate(data))

        self.sock.write(data)

    async def recv(self):
        """
        接收一条完整消息，自动处理 PING/PONG 控制帧。

        Returns:
            str: 文本帧内容（OP_TEXT），已解码为 UTF-8 字符串。
            bytes: 二进制帧内容（OP_BYTES）。
            None: 连接已关闭（OP_CLOSE）或发生异常。

        Raises:
            NotImplementedError: 收到分片帧（fin=False）或 OP_CONT 帧时抛出。
            ValueError: 收到未知 opcode 时抛出。

        Notes:
            收到 PING 帧时自动回复 PONG，随后继续等待数据帧。

        ==========================================

        Receive one complete message, automatically handles PING/PONG control frames.

        Returns:
            str: Text frame content (OP_TEXT), decoded as UTF-8 string.
            bytes: Binary frame content (OP_BYTES).
            None: Connection closed (OP_CLOSE) or exception occurred.

        Raises:
            NotImplementedError: Raised on fragmented frame (fin=False) or OP_CONT frame.
            ValueError: Raised on unknown opcode.

        Notes:
            Automatically replies PONG on PING frame, then continues waiting for data frame.
        """
        while await self.open():
            try:
                fin, opcode, data = await self.read_frame()
            # except (ValueError, EOFError) as ex:
            except Exception as ex:
                print('Exception in recv while reading frame:', ex)
                await self.open(False)
                return

            if not fin:
                raise NotImplementedError()

            if opcode == OP_TEXT:
                try:
                    return data.decode('utf-8')
                except UnicodeError:
                    # 如果解码失败，返回原始bytes
                    print("[WebSocket] Warning: OP_TEXT frame contains invalid UTF-8, returning raw bytes")
                    return data
            elif opcode == OP_BYTES:
                return data
            elif opcode == OP_CLOSE:
                await self.open(False)
                return
            elif opcode == OP_PONG:
                # Ignore this frame, keep waiting for a data frame
                continue
            elif opcode == OP_PING:
                try:
                    # We need to send a pong frame
                    self.write_frame(OP_PONG, data)

                    # And then continue to wait for a data frame
                    continue
                except Exception as ex:
                    print('Error sending pong frame:', ex)
                    # If sending the pong frame fails, close the connection
                    await self.open(False)
                    return
            elif opcode == OP_CONT:
                # This is a continuation of a previous frame
                raise NotImplementedError(opcode)
            else:
                raise ValueError(opcode)

    async def send(self, buf):
        """
        发送一条消息，根据类型自动选择文本帧或二进制帧。

        Args:
            buf (str | bytes): 要发送的内容；str 使用 OP_TEXT 帧，bytes 使用 OP_BYTES 帧。

        Raises:
            TypeError: buf 既不是 str 也不是 bytes 时抛出。

        Notes:
            连接未建立时静默返回，不抛出异常。
            str 类型的 buf 在发送前自动以 UTF-8 编码为字节串。

        ==========================================

        Send a message, automatically selects text or binary frame based on type.

        Args:
            buf (str | bytes): Content to send; str uses OP_TEXT frame, bytes uses OP_BYTES frame.

        Raises:
            TypeError: Raised when buf is neither str nor bytes.

        Notes:
            Returns silently without raising if connection is not established.
            str buf is automatically encoded to bytes using UTF-8 before sending.
        """
        if not await self.open():
            return
        if isinstance(buf, str):
            opcode = OP_TEXT
            buf = buf.encode('utf-8')
        elif isinstance(buf, bytes):
            opcode = OP_BYTES
        else:
            raise TypeError()
        self.write_frame(opcode, buf)

# ======================================== 初始化配置 ===========================================

# ========================================  主程序  ===========================================
