# Python env   : MicroPython v1.23.0
# -*- coding: utf-8 -*-
# @Time    : 2026/04/16
# @Author  : leeqingsui
# @File    : uopenai.py
# @Description : OpenAI-compatible async client for MicroPython, depends on aiohttps
# @License : MIT

__version__  = "1.0.0"
__author__   = "leeqingsui"
__license__  = "MIT"
__platform__ = "MicroPython v1.23"

# ======================================== 导入相关模块 =========================================

import json
import os
import ubinascii
import aiohttps

# ======================================== 全局变量 ============================================

# 默认 OpenAI API base URL
_DEFAULT_BASE_URL = "https://api.openai.com/v1"

# ======================================== 自定义类 ============================================

class _ChatCompletionChoice:
    """
    chat.completions 响应中单个 choice 对象。

    Attributes:
        index         (int): choice 序号。
        message       (_Message): 消息对象，含 role 和 content。
        finish_reason (str): 结束原因，如 "stop"。

    ==========================================

    Single choice object in a chat.completions response.

    Attributes:
        index         (int): Choice index.
        message       (_Message): Message object with role and content.
        finish_reason (str): Finish reason, e.g. "stop".
    """

    def __init__(self, data: dict) -> None:
        """
        从 dict 初始化 choice 对象。

        Args:
            data (dict): API 返回的单个 choice 字典。

        ==========================================

        Initialize from a dict returned by the API.

        Args:
            data (dict): Single choice dict from API response.
        """
        if not isinstance(data, dict):
            raise TypeError("data must be dict, got {}".format(type(data).__name__))

        self.index         = data.get("index", 0)
        self.finish_reason = data.get("finish_reason", "")
        msg                = data.get("message", {})
        self.message       = _Message(msg.get("role", ""), msg.get("content") or "")


class _Message:
    """
    消息对象，包含 role 和 content。

    Attributes:
        role    (str): 消息角色，如 "assistant"。
        content (str): 消息内容文本。

    ==========================================

    Message object containing role and content.

    Attributes:
        role    (str): Message role, e.g. "assistant".
        content (str): Message content text.
    """

    def __init__(self, role: str, content: str) -> None:
        """
        初始化消息对象。

        Args:
            role    (str): 消息角色。
            content (str): 消息内容。

        ==========================================

        Initialize message object.

        Args:
            role    (str): Message role.
            content (str): Message content.
        """
        if not isinstance(role, str):
            raise TypeError("role must be str, got {}".format(type(role).__name__))
        if not isinstance(content, str):
            raise TypeError("content must be str, got {}".format(type(content).__name__))

        self.role    = role
        self.content = content


class _ChatCompletionResponse:
    """
    chat.completions 完整响应对象。

    Attributes:
        id      (str): 响应 ID。
        model   (str): 使用的模型名称。
        choices (list): _ChatCompletionChoice 列表。
        usage   (dict): token 用量统计。

    ==========================================

    Full chat.completions response object.

    Attributes:
        id      (str): Response ID.
        model   (str): Model name used.
        choices (list): List of _ChatCompletionChoice.
        usage   (dict): Token usage statistics.
    """

    def __init__(self, data: dict) -> None:
        """
        从 API 返回的 dict 初始化响应对象。

        Args:
            data (dict): API 返回的完整响应字典。

        ==========================================

        Initialize from full API response dict.

        Args:
            data (dict): Full API response dict.
        """
        if not isinstance(data, dict):
            raise TypeError("data must be dict, got {}".format(type(data).__name__))

        self.id      = data.get("id", "")
        self.model   = data.get("model", "")
        self.usage   = data.get("usage", {})
        self.choices = [_ChatCompletionChoice(c) for c in data.get("choices", [])]


class _Completions:
    """
    client.chat.completions 命名空间，提供 create() 方法。

    Attributes:
        _client (OpenAI): 父级 OpenAI 客户端引用。

    ==========================================

    client.chat.completions namespace providing the create() method.

    Attributes:
        _client (OpenAI): Reference to parent OpenAI client.
    """

    def __init__(self, client: object) -> None:
        """
        初始化，保存父级客户端引用。

        Args:
            client: OpenAI 客户端实例。

        ==========================================

        Initialize with reference to parent client.

        Args:
            client: OpenAI client instance.
        """
        if client is None:
            raise ValueError("client cannot be None")

        # 保存父级客户端引用
        self._client = client

    async def create(self, model: str, messages: list,
                     stream: bool = False, timeout_ms: int = 30000,
                     **kwargs) -> _ChatCompletionResponse:
        """
        发起 chat/completions 请求。

        非流式（stream=False）：返回 _ChatCompletionResponse 对象。
        流式（stream=True）：返回 aiohttps.Response，调用方用 iter_lines() 逐行读取 SSE。

        Args:
            model    (str): 模型名称，如 "gpt-4o"、"deepseek-chat"。
            messages (list): 消息列表，每项为 {"role": ..., "content": ...}。
            stream   (bool): 是否启用流式输出，默认 False。
            **kwargs: 其他 OpenAI 参数，如 temperature、max_tokens 等。

        Returns:
            _ChatCompletionResponse | aiohttps.Response

        Raises:
            ValueError: model 或 messages 为空时抛出。
            TypeError:  参数类型不符合要求时抛出。

        ==========================================

        Make a chat/completions request.

        Non-streaming (stream=False): returns _ChatCompletionResponse.
        Streaming (stream=True): returns aiohttps.Response; caller uses iter_lines() for SSE.

        Args:
            model    (str): Model name, e.g. "gpt-4o", "deepseek-chat".
            messages (list): Message list, each item {"role": ..., "content": ...}.
            stream   (bool): Enable streaming output, default False.
            **kwargs: Other OpenAI params, e.g. temperature, max_tokens.

        Returns:
            _ChatCompletionResponse | aiohttps.Response

        Raises:
            ValueError: model or messages is empty.
            TypeError:  Parameter type mismatch.
        """
        if model is None or not isinstance(model, str) or len(model) == 0:
            raise ValueError("model must be a non-empty string")
        if not isinstance(messages, list) or len(messages) == 0:
            raise ValueError("messages must be a non-empty list")

        payload = {"model": model, "messages": messages, "stream": stream}
        payload.update(kwargs)

        url  = self._client._base_url + "/chat/completions"
        hdrs = {"Authorization": "Bearer " + self._client._api_key,
                "Content-Type": "application/json"}

        resp = await aiohttps.post(url, headers=hdrs, data=json.dumps(payload),
                                   timeout_ms=timeout_ms)

        if stream:
            # 流式：直接返回 Response，调用方用 iter_lines() 读 SSE
            return resp

        # 非流式：全量读取 JSON
        data = await resp.json()
        return _ChatCompletionResponse(data)


class _Chat:
    """
    client.chat 命名空间。

    Attributes:
        completions (_Completions): chat.completions 子命名空间。

    ==========================================

    client.chat namespace.

    Attributes:
        completions (_Completions): chat.completions sub-namespace.
    """

    def __init__(self, client: object) -> None:
        """
        初始化，创建 completions 子命名空间。

        Args:
            client: OpenAI 客户端实例。

        ==========================================

        Initialize and create completions sub-namespace.

        Args:
            client: OpenAI client instance.
        """
        if client is None:
            raise ValueError("client cannot be None")

        # 创建 completions 子命名空间
        self.completions = _Completions(client)


class _Transcriptions:
    """
    client.audio.transcriptions 命名空间。

    Notes:
        嵌入式设备 ASR 更适合 WebSocket 流式连接（边说边识别），
        REST 文件上传方式延迟高，暂不实现，留 TODO。

    Attributes:
        _client (OpenAI): 父级 OpenAI 客户端引用。

    ==========================================

    client.audio.transcriptions namespace.

    Notes:
        On embedded devices, streaming WebSocket ASR is preferred over
        REST file upload. Implementation deferred — see TODO.

    Attributes:
        _client (OpenAI): Reference to parent OpenAI client.
    """

    def __init__(self, client: object) -> None:
        """
        初始化，保存父级客户端引用。

        Args:
            client: OpenAI 客户端实例。

        ==========================================

        Initialize with reference to parent client.

        Args:
            client: OpenAI client instance.
        """
        if client is None:
            raise ValueError("client cannot be None")

        # 保存父级客户端引用
        self._client = client

    async def create(self, model: str, filepath: str,
                     language: str = None,
                     response_format: str = "json"):
        """
        发起 audio/transcriptions 请求（待实现）。

        Args:
            model           (str): 模型名称，如 "whisper-1"。
            filepath        (str): 本地音频文件路径。
            language        (str, optional): 语言代码，如 "zh"、"en"。
            response_format (str): 响应格式，默认 "json"。

        Returns:
            None: 待实现，当前返回 None。

        Raises:
            ValueError: model 或 filepath 为空时抛出。
            TypeError:  参数类型不符合要求时抛出。

        ==========================================

        Make an audio/transcriptions request (not yet implemented).

        Args:
            model           (str): Model name, e.g. "whisper-1".
            filepath        (str): Local audio file path.
            language        (str, optional): Language code, e.g. "zh", "en".
            response_format (str): Response format, default "json".

        Returns:
            None: Not yet implemented.

        Raises:
            ValueError: model or filepath is empty.
            TypeError:  Parameter type mismatch.
        """
        if model is None or not isinstance(model, str) or len(model) == 0:
            raise ValueError("model must be a non-empty string")
        if filepath is None or not isinstance(filepath, str) or len(filepath) == 0:
            raise ValueError("filepath must be a non-empty string")

        # TODO: 嵌入式 ASR 推荐使用 WebSocket 流式连接实现，参考 async_websocketclient
        # TODO: Embedded ASR should use WebSocket streaming; see async_websocketclient
        pass


class _Speech:
    """
    client.audio.speech 命名空间。

    Notes:
        嵌入式设备 TTS 更适合 WebSocket 流式连接（边生成边播放），
        REST 方式需等待完整音频生成后再下载，延迟高，暂不实现，留 TODO。

    Attributes:
        _client (OpenAI): 父级 OpenAI 客户端引用。

    ==========================================

    client.audio.speech namespace.

    Notes:
        On embedded devices, streaming WebSocket TTS is preferred over
        REST full-audio download. Implementation deferred — see TODO.

    Attributes:
        _client (OpenAI): Reference to parent OpenAI client.
    """

    def __init__(self, client: object) -> None:
        """
        初始化，保存父级客户端引用。

        Args:
            client: OpenAI 客户端实例。

        ==========================================

        Initialize with reference to parent client.

        Args:
            client: OpenAI client instance.
        """
        if client is None:
            raise ValueError("client cannot be None")

        # 保存父级客户端引用
        self._client = client

    async def create(self, model: str, input: str,
                     voice: str = "alloy",
                     response_format: str = "mp3",
                     speed: float = 1.0):
        """
        发起 audio/speech 请求（待实现）。

        Args:
            model           (str): 模型名称，如 "tts-1"。
            input           (str): 要转换为语音的文本。
            voice           (str): 音色，如 "alloy"、"echo"。
            response_format (str): 音频格式，默认 "mp3"。
            speed           (float): 语速，默认 1.0。

        Raises:
            ValueError: model 或 input 为空时抛出。
            TypeError:  参数类型不符合要求时抛出。

        ==========================================

        Make an audio/speech request (not yet implemented).

        Args:
            model           (str): Model name, e.g. "tts-1".
            input           (str): Text to convert to speech.
            voice           (str): Voice, e.g. "alloy", "echo".
            response_format (str): Audio format, default "mp3".
            speed           (float): Speech speed, default 1.0.

        Raises:
            ValueError: model or input is empty.
            TypeError:  Parameter type mismatch.
        """
        if model is None or not isinstance(model, str) or len(model) == 0:
            raise ValueError("model must be a non-empty string")
        if input is None or not isinstance(input, str) or len(input) == 0:
            raise ValueError("input must be a non-empty string")

        # TODO: 嵌入式 TTS 推荐使用 WebSocket 流式连接实现，参考 async_websocketclient
        # TODO: Embedded TTS should use WebSocket streaming; see async_websocketclient
        pass


class _Generations:
    """
    client.images.generations 命名空间。

    Notes:
        待找到适合嵌入式设备的低分辨率文生图模型（OpenAI 兼容接口），
        暂不实现，留 TODO。

    Attributes:
        _client (OpenAI): 父级 OpenAI 客户端引用。

    ==========================================

    client.images.generations namespace.

    Notes:
        Pending a suitable low-resolution image generation model compatible
        with OpenAI API for embedded devices. Implementation deferred — see TODO.

    Attributes:
        _client (OpenAI): Reference to parent OpenAI client.
    """

    def __init__(self, client: object) -> None:
        """
        初始化，保存父级客户端引用。

        Args:
            client: OpenAI 客户端实例。

        ==========================================

        Initialize with reference to parent client.

        Args:
            client: OpenAI client instance.
        """
        if client is None:
            raise ValueError("client cannot be None")

        # 保存父级客户端引用
        self._client = client

    async def create(self, model: str, prompt: str,
                     size: str = "256x256", n: int = 1):
        """
        发起 images/generations 请求（待实现）。

        Args:
            model  (str): 模型名称，如 "dall-e-2"。
            prompt (str): 图片描述文本。
            size   (str): 图片尺寸，默认 "256x256"。
            n      (int): 生成数量，默认 1。

        Raises:
            ValueError: model 或 prompt 为空时抛出。
            TypeError:  参数类型不符合要求时抛出。

        ==========================================

        Make an images/generations request (not yet implemented).

        Args:
            model  (str): Model name, e.g. "dall-e-2".
            prompt (str): Image description text.
            size   (str): Image size, default "256x256".
            n      (int): Number of images, default 1.

        Raises:
            ValueError: model or prompt is empty.
            TypeError:  Parameter type mismatch.
        """
        if model is None or not isinstance(model, str) or len(model) == 0:
            raise ValueError("model must be a non-empty string")
        if prompt is None or not isinstance(prompt, str) or len(prompt) == 0:
            raise ValueError("prompt must be a non-empty string")

        # TODO: 待找到适合嵌入式的低分辨率 OpenAI 兼容文生图模型后实现
        # TODO: Implement once a suitable low-res OpenAI-compatible image gen model is found
        pass


class _Images:
    """
    client.images 命名空间。

    Attributes:
        generations (_Generations): images.generations 子命名空间。

    ==========================================

    client.images namespace.

    Attributes:
        generations (_Generations): images.generations sub-namespace.
    """

    def __init__(self, client: object) -> None:
        """
        初始化，创建 generations 子命名空间。

        Args:
            client: OpenAI 客户端实例。

        ==========================================

        Initialize and create generations sub-namespace.

        Args:
            client: OpenAI client instance.
        """
        if client is None:
            raise ValueError("client cannot be None")

        # 创建 generations 子命名空间
        self.generations = _Generations(client)


class _Audio:
    """
    client.audio 命名空间。

    Attributes:
        transcriptions (_Transcriptions): audio.transcriptions 子命名空间。
        speech         (_Speech):         audio.speech 子命名空间。

    ==========================================

    client.audio namespace.

    Attributes:
        transcriptions (_Transcriptions): audio.transcriptions sub-namespace.
        speech         (_Speech):         audio.speech sub-namespace.
    """

    def __init__(self, client: object) -> None:
        """
        初始化，创建 transcriptions 和 speech 子命名空间。

        Args:
            client: OpenAI 客户端实例。

        ==========================================

        Initialize and create transcriptions and speech sub-namespaces.

        Args:
            client: OpenAI client instance.
        """
        if client is None:
            raise ValueError("client cannot be None")

        # 创建 transcriptions 子命名空间
        self.transcriptions = _Transcriptions(client)
        # 创建 speech 子命名空间
        self.speech = _Speech(client)


class OpenAI:
    """
    MicroPython 上的 OpenAI 兼容异步客户端，依赖 aiohttps 库。

    与 PC 端 openai SDK 保持最大接口兼容性：
    - client.chat.completions.create(model, messages, stream)
    - client.audio.transcriptions.create(model, filepath)
    - OpenAI.encode_image(filepath) -> base64 字符串

    MicroPython 限制说明：
    - 不支持 file=open(...) 传参，改用 filepath= 字符串
    - 不支持同步调用，所有 create() 均为 async

    Attributes:
        _api_key  (str): OpenAI API 密钥。
        _base_url (str): API base URL，默认 https://api.openai.com/v1。
        chat      (_Chat): chat 命名空间。
        audio     (_Audio): audio 命名空间（transcriptions / speech）。
        images    (_Images): images 命名空间（generations）。

    Methods:
        encode_image(filepath) -> str: 将图片文件编码为 base64 字符串。

    Notes:
        - 依赖 aiohttps.py，需提前上传到设备根目录。
        - TLS 握手不验证证书（cert_reqs=0），适合资源受限设备。

    ==========================================

    OpenAI-compatible async client for MicroPython, depends on aiohttps.

    Maintains maximum API compatibility with the PC openai SDK:
    - client.chat.completions.create(model, messages, stream)
    - client.audio.transcriptions.create(model, filepath)
    - OpenAI.encode_image(filepath) -> base64 string

    MicroPython limitations:
    - file=open(...) is not supported; use filepath= string instead
    - All create() methods are async only

    Attributes:
        _api_key  (str): OpenAI API key.
        _base_url (str): API base URL, default https://api.openai.com/v1.
        chat      (_Chat): chat namespace.
        audio     (_Audio): audio namespace.

    Methods:
        encode_image(filepath) -> str: Encode image file as base64 string.

    Notes:
        - Requires aiohttps.py uploaded to device root.
        - TLS does not verify server certificate (cert_reqs=0).
    """

    def __init__(self, api_key: str, base_url: str = _DEFAULT_BASE_URL) -> None:
        """
        初始化 OpenAI 客户端。

        Args:
            api_key  (str): OpenAI API 密钥（必填）。
            base_url (str): API base URL，默认 https://api.openai.com/v1，
                            可替换为 DeepSeek、Moonshot 等兼容接口地址。

        Raises:
            ValueError: api_key 为空时抛出。
            TypeError:  参数类型不符合要求时抛出。

        ==========================================

        Initialize the OpenAI client.

        Args:
            api_key  (str): OpenAI API key (required).
            base_url (str): API base URL, default https://api.openai.com/v1.
                            Can be replaced with DeepSeek, Moonshot, etc.

        Raises:
            ValueError: api_key is empty.
            TypeError:  Parameter type mismatch.
        """
        if api_key is None or not isinstance(api_key, str) or len(api_key) == 0:
            raise ValueError("api_key must be a non-empty string")
        if not isinstance(base_url, str) or len(base_url) == 0:
            raise ValueError("base_url must be a non-empty string")

        # 去除 base_url 末尾斜杠，保持一致性
        self._api_key  = api_key
        self._base_url = base_url.rstrip("/")

        # 初始化命名空间
        self.chat   = _Chat(self)
        self.audio  = _Audio(self)
        self.images = _Images(self)

    @staticmethod
    def encode_image(filepath: str) -> str:
        """
        将本地图片文件编码为 base64 字符串，用于视觉模型的 image_url 字段。

        注意：整个文件会被读入内存，仅适合小图片（< 50 KB）。
        大图片请先在 PC 端压缩后再传输。

        Args:
            filepath (str): 本地图片文件路径。

        Returns:
            str: base64 编码字符串（不含 data URI 前缀）。

        Raises:
            ValueError: filepath 为空时抛出。
            TypeError:  filepath 不是字符串时抛出。
            OSError:    文件不存在或无法读取时抛出。

        ==========================================

        Encode a local image file as a base64 string for vision model image_url field.

        Note: Entire file is read into RAM; only suitable for small images (< 50 KB).
        Compress large images on PC before transferring.

        Args:
            filepath (str): Local image file path.

        Returns:
            str: Base64-encoded string (without data URI prefix).

        Raises:
            ValueError: filepath is empty.
            TypeError:  filepath is not a string.
            OSError:    File not found or unreadable.
        """
        if filepath is None or not isinstance(filepath, str) or len(filepath) == 0:
            raise ValueError("filepath must be a non-empty string")

        with open(filepath, "rb") as f:
            raw = f.read()
        # ubinascii.b2a_base64 每次返回带换行符的 bytes，需去除
        return ubinascii.b2a_base64(raw).decode("utf-8").rstrip("\n")

# ======================================== 初始化配置 ===========================================

# ========================================  主程序  ===========================================
