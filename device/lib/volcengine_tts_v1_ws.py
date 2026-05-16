# Python env   : MicroPython v1.23.0
# -*- coding: utf-8 -*-
# @Time    : 2026/05/14
# @Author  : AI Assistant
# @File    : volcengine_tts_v1_ws.py
# @Description : 火山引擎 TTS V1 WebSocket 客户端，支持流式播放
# @License : MIT

# ======================================== 导入相关模块 =========================================

import json
import struct
import asyncio
from async_websocketclient import AsyncWebsocketClient

# ======================================== 全局变量 ============================================

__version__ = "1.0.0"
__author__ = "AI Assistant"
__license__ = "MIT"
__platform__ = "MicroPython v1.23"

# ======================================== 功能函数 ============================================

def _gen_uuid():
    """
    生成简单的 UUID 字符串（MicroPython 兼容）。

    Returns:
        str: 32 字符十六进制 UUID。
    """
    try:
        import os
        import binascii
        return binascii.hexlify(os.urandom(16)).decode()
    except Exception:
        import uuid
        return str(uuid.uuid4()).replace("-", "")


# ======================================== 自定义类 ============================================


class VolcengineTTSV1WS:
    """
    火山引擎 TTS V1 WebSocket 客户端，支持边合成边播放。

    用法:
        tts = VolcengineTTSV1WS(
            app_id="your_app_id",
            access_token="your_token",
            voice_type=VolcengineTTSV1WS.VOICE_BV701_STREAMING
        )
        # 流式播放（推荐，低延迟）
        await tts.synthesize_and_play("你好", audio_out, amp_sd)
        # 或保存到文件
        size = await tts.synthesize("你好", output_path="out.pcm")
        # 临时修改参数
        await tts.synthesize("欢迎", voice_type=tts.VOICE_XIAOGE, speed=1.2)
    """

    # ========== 音频格式常量 ==========
    FORMAT_PCM = "pcm"
    FORMAT_WAV = "wav"
    FORMAT_MP3 = "mp3"
    FORMAT_OGG_OPUS = "ogg_opus"

    # ========== 常用音色常量 ==========
    # 通用场景
    VOICE_BV701_STREAMING = "BV701_streaming"
    VOICE_BV700_STREAMING = "BV700_streaming"

    # ===== 中文角色扮演 =====
    # 寡言小哥
    VOICE_XIAOGE = "ICL_zh_male_xiaoge_v1_tob"
    # 清朗温润
    VOICE_RENYUWANGZI = "ICL_zh_male_renyuwangzi_v1_tob"
    # 潇洒随性
    VOICE_XIAOSHA = "ICL_zh_male_xiaosha_v1_tob"
    # 清冷矜贵
    VOICE_LIYISHENG = "ICL_zh_male_liyisheng_v1_tob"
    # 沉稳优雅
    VOICE_CHENGWEN = "ICL_zh_male_qinglen_v1_tob"
    # 温柔内敛
    VOICE_WENROU = "ICL_zh_male_xingjiwangzi_v1_tob"
    # 低沉缱绻
    VOICE_DICHEN = "ICL_zh_male_sigeshiye_v1_tob"
    # 清冷高雅
    VOICE_LIUMENGDIE = "ICL_zh_female_liumengdie_v1_tob"
    # 甜美娇俏
    VOICE_LINXUEYING = "ICL_zh_female_linxueying_v1_tob"
    # 柔骨魂师
    VOICE_ROUGUHUNSHI = "ICL_zh_female_rouguhunshi_v1_tob"
    # 甜美活泼
    VOICE_TIANMEI = "ICL_zh_female_tianmei_v1_tob"
    # 成熟温柔
    VOICE_CHENGSHU = "ICL_zh_female_chengshu_v1_tob"
    # 贴心闺蜜
    VOICE_GUIMI = "ICL_zh_female_xnx_v1_tob"
    # 温柔白月光
    VOICE_BAIYUEGUANG = "ICL_zh_female_yry_v1_tob"
    # 妩媚可人
    VOICE_GANLI = "ICL_zh_female_ganli_v1_tob"
    # 邪魅御姐
    VOICE_XIANGLIANGYA = "ICL_zh_female_xiangliangya_v1_tob"
    # 倾心少女
    VOICE_QIULING = "ICL_zh_female_qiuling_v1_tob"
    # 纯澈女生
    VOICE_FEICUI = "ICL_zh_female_feicui_v1_tob"
    # 初恋女友
    VOICE_YUXIN = "ICL_zh_female_yuxin_v1_tob"
    # 邪魅女王
    VOICE_BINGJIAO = "ICL_zh_female_bingjiao3_tob"
    # 性感魅惑
    VOICE_LUOQING = "ICL_zh_female_luoqing_v1_tob"

    # ===== 中文通用场景 =====
    # 高冷沉稳
    VOICE_GAOLENG = "zh_male_bv139_audiobook_ummv3_bigtts"
    # 亲切女声
    VOICE_QINQIE = "zh_female_qinqienvsheng_moon_bigtts"
    # 魅力苏菲
    VOICE_SOPHIE = "zh_female_sophie_conversation_wvae_bigtts"
    # 文静毛毛
    VOICE_MAOMAO = "zh_female_maomao_conversation_wvae_bigtts"
    # 贴心妹妹
    VOICE_YILIN = "ICL_zh_female_yilin_tob"
    # 元气甜妹
    VOICE_WUXI = "ICL_zh_female_wuxi_tob"
    # 知心姐姐
    VOICE_WENYINV = "ICL_zh_female_wenyinvsheng_v1_tob"

    # ===== 中文多情感 =====
    # 深夜播客
    VOICE_SHENYEBOKE = "zh_male_shenyeboke_emo_v2_mars_bigtts"
    # 甜心小美（多情感）
    VOICE_TIANXIN = "zh_female_tianxinxiaomei_emo_v2_mars_bigtts"
    # 高冷御姐（多情感）
    VOICE_GAOLENGYUJIE = "zh_female_gaolengyujie_emo_v2_mars_bigtts"
    # 傲娇霸总（多情感）
    VOICE_AOJIAOBAZONG = "zh_male_aojiaobazong_emo_v2_mars_bigtts"
    # 优柔公子（多情感）
    VOICE_YOUROUGONGZI = "zh_male_yourougongzi_emo_v2_mars_bigtts"
    # 儒雅男友（多情感）
    VOICE_RUYAYICHEN = "zh_male_ruyayichen_emo_v2_mars_bigtts"
    # 俊朗男友（多情感）
    VOICE_JUNLANG = "zh_male_junlangnanyou_emo_v2_mars_bigtts"
    # 双节棍小哥（台湾口音）
    VOICE_ZHOUJIELUN = "zh_male_zhoujielun_emo_v2_mars_bigtts"

    # ===== 中文趣味口音 =====
    # 粤语小溏
    VOICE_YUEYUNV = "zh_female_yueyunv_mars_bigtts"

    # ===== 新增中文角色扮演 =====
    # 纯真少女
    VOICE_CHUNZHEN = "ICL_zh_female_chunzhenshaonv_e588402fb8ad_tob"
    # 奶气小生
    VOICE_XIAONAIGOU = "ICL_zh_male_xiaonaigou_edf58cf28b8b_tob"
    # 精灵向导
    VOICE_JINGLING = "ICL_zh_female_jinglingxiangdao_1beb294a9e3e_tob"
    # 闷油瓶小哥
    VOICE_MENYOUPING = "ICL_zh_male_menyoupingxiaoge_ffed9fc2fee7_tob"
    # 内敛才俊
    VOICE_NEILIAN = "ICL_zh_male_neiliancaijun_e991be511569_tob"

    # ===== 新增中文通用 =====
    # 甜美桃子
    VOICE_TIANMEITAOZI = "zh_female_tianmeitaozi_mars_bigtts"
    # 暖阳女声
    VOICE_KEFUNV = "zh_female_kefunvsheng_mars_bigtts"
    # vv活泼女声
    VOICE_VV = "zh_female_vv_jupiter_bigtts"
    # xiaohe台湾口音
    VOICE_XIAOHE = "zh_female_xiaohe_jupiter_bigtts"
    # 娇喘女声
    VOICE_JIAOCHUAN = "zh_female_jiaochuan_mars_bigtts"

    # ===== 新增中文多情感 =====
    # 广州德哥（多情感）
    VOICE_GUANGZHOUDEGE = "zh_male_guangzhoudege_emo_mars_bigtts"
    # 京腔侃爷（多情感）
    VOICE_JINGQIANGKANYE = "zh_male_jingqiangkanye_emo_mars_bigtts"
    # 邻居阿姨（多情感）
    VOICE_LINJUAYI = "zh_female_linjuayi_emo_v2_mars_bigtts"
    # 北京小爷（多情感）
    VOICE_BEIJINGXIAOYE = "zh_male_beijingxiaoye_emo_v2_mars_bigtts"
    # 柔美女友（多情感）
    VOICE_ROUMEINVYOU = "zh_female_roumeinvyou_emo_v2_mars_bigtts"
    # 阳光青年（多情感）
    VOICE_YANGGUANG = "zh_male_yangguangqingnian_emo_v2_mars_bigtts"
    # 魅力女友（多情感）
    VOICE_MEILINVYOU = "zh_female_meilinvyou_emo_v2_mars_bigtts"
    # 爽快思思（多情感）
    VOICE_SHUANGKUAI = "zh_female_shuangkuaisisi_emo_v2_mars_bigtts"

    # ===== 英语（美式）=====
    # Energetic Male II
    VOICE_EN_JAMAL = "en_male_campaign_jamal_moon_bigtts"
    # Gotham Hero
    VOICE_EN_CHRIS = "en_male_chris_moon_bigtts"
    # Flirty Female
    VOICE_EN_DARCIE = "en_female_product_darcie_moon_bigtts"
    # Peaceful Female
    VOICE_EN_EMOTIONAL = "en_female_emotional_moon_bigtts"
    # Bruce
    VOICE_EN_BRUCE = "en_male_bruce_moon_bigtts"
    # Michael
    VOICE_EN_MICHAEL = "en_male_michael_moon_bigtts"
    # Nara
    VOICE_EN_NARA = "en_female_nara_moon_bigtts"
    # Candice（多情感）
    VOICE_EN_CANDICE = "en_female_candice_emo_v2_mars_bigtts"
    # Glen（多情感）
    VOICE_EN_GLEN = "en_male_glen_emo_v2_mars_bigtts"
    # Sylus（多情感）
    VOICE_EN_SYLUS = "en_male_sylus_emo_v2_mars_bigtts"
    # Serena（多情感）
    VOICE_EN_SERENA = "en_female_skye_emo_v2_mars_bigtts"

    # ===== 英语（英式）=====
    # Delicate Girl
    VOICE_EN_DAISY = "en_female_daisy_moon_bigtts"
    # Dave
    VOICE_EN_DAVE = "en_male_dave_moon_bigtts"
    # Hades
    VOICE_EN_HADES = "en_male_hades_moon_bigtts"
    # Onez
    VOICE_EN_ONEZ = "en_female_onez_moon_bigtts"
    # Corey（多情感）
    VOICE_EN_COREY = "en_male_corey_emo_v2_mars_bigtts"
    # Emily
    VOICE_EN_EMILY = "en_female_emily_mars_bigtts"

    # ===== 日语 =====
    # ひかる（光）
    VOICE_JA_HIKARU = "multi_zh_male_youyoujunzi_moon_bigtts"
    # さとみ（智美）
    VOICE_JA_SATOMI = "multi_female_sophie_conversation_wvae_bigtts"
    # つき（月）
    VOICE_JA_TSUKI = "multi_female_maomao_conversation_wvae_bigtts"

    # ===== 西班牙语 =====
    # Diana
    VOICE_ES_DIANA = "multi_female_maomao_conversation_wvae_bigtts"
    # Sofía
    VOICE_ES_SOFIA = "multi_female_sophie_conversation_wvae_bigtts"

    # ========== 协议常量 (私有) ==========
    _WS_URL = "wss://openspeech.bytedance.com/api/v1/tts/ws_binary"

    _PROTOCOL_VERSION = 0b0001
    _HEADER_SIZE = 0b0001
    _MSG_TYPE_FULL_CLIENT = 0b0001
    _MSG_TYPE_AUDIO_ONLY = 0b1011
    _MSG_TYPE_ERROR = 0b1111
    _SERIAL_JSON = 0b0001
    _COMPRESS_NONE = 0b0000

    # ========== 默认值常量 ==========
    DEFAULT_SAMPLE_RATE = 16000
    DEFAULT_VOLUME = 1.0
    DEFAULT_SPEED = 1.0
    DEFAULT_PITCH = 1.0
    DEFAULT_LANGUAGE = "zh"

    def __init__(
        self,
        # 必需参数
        app_id: str,
        access_token: str,

        # 默认配置（可选，后续可在synthesize时覆盖）
        voice_type: str = None,
        format: str = FORMAT_PCM,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        volume: float = DEFAULT_VOLUME,
        speed: float = DEFAULT_SPEED,
        pitch: float = DEFAULT_PITCH,
        language: str = DEFAULT_LANGUAGE,
        style: str = None,
        enable_subtitle: int = 0,
        debug: bool = False,
    ):
        """
        初始化 V1 WebSocket TTS 客户端。

        Args:
            app_id:           火山引擎 App ID（必需）。
            access_token:     火山引擎 Access Token（必需）。
            voice_type:       默认音色，如 VOICE_BV701_STREAMING。
            format:           默认音频格式，pcm/wav/mp3/ogg_opus。
            sample_rate:      默认采样率，8000~48000。
            volume:           默认音量，0.1~3.0。
            speed:            默认语速，0.2~3.0。
            pitch:            默认音调，0.1~3.0。
            language:         默认语言，zh/en/ja。
            style:            默认情感，如 happy/sad/angry。
            enable_subtitle:  默认字幕级别，0/1/2/3。
        """
        if app_id is None or not isinstance(app_id, str):
            raise ValueError("app_id must be a non-None str")
        if access_token is None or not isinstance(access_token, str):
            raise ValueError("access_token must be a non-None str")
        self._app_id = app_id
        self._access_token = access_token

        # 保存默认值
        self._default_voice_type = voice_type or self.VOICE_BV701_STREAMING
        self._default_format = format
        self._default_sample_rate = sample_rate
        self._default_volume = volume
        self._default_speed = speed
        self._default_pitch = pitch
        self._default_language = language
        self._default_style = style
        self._default_enable_subtitle = enable_subtitle
        self._debug = debug

    def _log(self, msg: str) -> None:
        if self._debug:
            print("[V1WS]", msg)

    def _build_header(self) -> bytes:
        """
        构造 V1 协议 4 字节头部（固定）。

        Returns:
            bytes: 4 字节头部 0x11 0x10 0x00 0x00。
        """
        # Byte 0: version(4bit)=0b0001 + header_size(4bit)=0b0001
        byte0 = (self._PROTOCOL_VERSION << 4) | self._HEADER_SIZE
        # Byte 1: message_type(4bit)=0b0001 + flags(4bit)=0b0000
        byte1 = (self._MSG_TYPE_FULL_CLIENT << 4) | 0x00
        # Byte 2: serialization(4bit)=0b0001 + compression(4bit)=0b0000
        byte2 = (self._SERIAL_JSON << 4) | self._COMPRESS_NONE
        # Byte 3: reserved
        byte3 = 0x00
        return bytes([byte0, byte1, byte2, byte3])

    def _build_frame(self, payload: dict) -> bytes:
        """
        构造完整的 V1 协议帧。

        Args:
            payload: JSON 载荷字典。

        Returns:
            bytes: 完整二进制帧 [4B header][4B payload_size][JSON payload]。
        """
        header = self._build_header()
        payload_bytes = json.dumps(payload).encode('utf-8')
        payload_size = struct.pack('>I', len(payload_bytes))
        return header + payload_size + payload_bytes

    def _parse_response(self, data: bytes) -> tuple:
        """
        解析 V1 协议响应帧。

        Args:
            data: 接收到的二进制帧。

        Returns:
            tuple: (msg_type, sequence, audio_data_or_error)
                   - msg_type: 消息类型
                   - sequence: 序列号（audio-only时有效，<0表示最后一帧）
                   - audio_data_or_error: 音频数据(bytes)或错误信息(dict)
        """
        if len(data) < 4:
            return None, None, None

        # 解析头部
        msg_type = (data[1] >> 4) & 0x0f
        flags = data[1] & 0x0f

        # Audio-only server response (0b1011)
        if msg_type == self._MSG_TYPE_AUDIO_ONLY:
            # flags 低2位表示 sequence 状态
            # 0b00: 无 sequence
            # 0b01: sequence > 0
            # 0b10/0b11: sequence < 0 (最后一帧)
            has_sequence = (flags & 0b0001) != 0
            is_last = (flags & 0b0010) != 0

            offset = 4
            sequence = 0

            if has_sequence:
                # 读取 4 字节 sequence number (big-endian signed int)
                sequence = struct.unpack('>i', data[offset:offset+4])[0]
                offset += 4

            # 剩余数据是音频
            audio_data = data[offset:]
            return msg_type, sequence, audio_data

        # Error message (0b1111)
        elif msg_type == self._MSG_TYPE_ERROR:
            # V1错误响应: [4B header][4B unknown][4B payload_size][JSON]
            # JSON从byte 12开始
            try:
                json_start = data.find(b'{')
                if json_start > 0:
                    error_info = json.loads(data[json_start:].decode('utf-8'))
                    return msg_type, None, error_info
            except Exception:
                pass
            return msg_type, None, {"error": "Failed to parse", "raw": data[:50]}

        # 未知消息类型
        return msg_type, None, None

    async def synthesize(
        self,
        text: str,
        output_path: str = None,
        # 可选覆盖参数
        voice_type: str = None,
        volume: float = None,
        speed: float = None,
        pitch: float = None,
        style: str = None,
        format: str = None,
        sample_rate: int = None,
        language: str = None,
        enable_subtitle: int = None,
    ):
        """
        合成语音到文件或内存。

        Args:
            text:             待合成文本。
            output_path:      保存路径；为 None 时返回 bytes。
            voice_type:       临时指定音色（不传则用默认）。
            volume:           临时指定音量（不传则用默认）。
            speed:            临时指定语速（不传则用默认）。
            pitch:            临时指定音调（不传则用默认）。
            style:            临时指定情感（不传则用默认）。
            format:           临时指定格式（不传则用默认）。
            sample_rate:      临时指定采样率（不传则用默认）。
            language:         临时指定语言（不传则用默认）。
            enable_subtitle:  临时指定字幕级别（不传则用默认）。

        Returns:
            int:   output_path 不为 None 时，返回写入字节数。
            bytes: output_path 为 None 时，返回完整音频数据。
        """
        # 使用传入值或默认值
        actual_voice = voice_type or self._default_voice_type
        actual_volume = volume if volume is not None else self._default_volume
        actual_speed = speed if speed is not None else self._default_speed
        actual_pitch = pitch if pitch is not None else self._default_pitch
        actual_style = style or self._default_style
        actual_format = format or self._default_format
        actual_sample_rate = sample_rate or self._default_sample_rate
        actual_language = language or self._default_language
        actual_subtitle = enable_subtitle if enable_subtitle is not None else self._default_enable_subtitle

        # 构建请求 payload
        reqid = _gen_uuid()
        payload = {
            "app": {
                "appid": self._app_id,
                "token": "access_token",
                "cluster": "volcano_tts"
            },
            "user": {
                "uid": "user_" + reqid[:8]
            },
            "audio": {
                "voice_type": actual_voice,
                "encoding": actual_format,
                "speed_ratio": actual_speed,
                "volume_ratio": actual_volume,
                "pitch_ratio": actual_pitch,
                "sample_rate": actual_sample_rate,
                "language": actual_language,
            },
            "request": {
                "reqid": reqid,
                "text": text,
                "text_type": "plain",
                "operation": "submit",
            }
        }
        if actual_style:
            payload["audio"]["emotion"] = actual_style
        if actual_subtitle > 0:
            payload["request"]["with_frontend"] = 1
            payload["request"]["frontend_type"] = "unitTson"

        # 连接 WebSocket
        ws = AsyncWebsocketClient(ms_delay_for_read=5)
        headers = [("Authorization", "Bearer; {}".format(self._access_token))]

        try:
            await ws.handshake(self._WS_URL, headers=headers, cert_reqs=0)
        except Exception as e:
            self._log("Handshake failed: " + str(e))
            return 0 if output_path else b""

        # 发送请求
        frame = self._build_frame(payload)
        await ws.send(frame)

        # 接收音频数据
        audio_chunks = []
        total_bytes = 0
        f = open(output_path, "wb") if output_path else None

        try:
            while await ws.open():
                try:
                    data = await ws.recv()
                except Exception as e:
                    self._log("recv() error: " + str(e))
                    break

                if data is None:
                    break

                msg_type, sequence, content = self._parse_response(data)

                if msg_type == self._MSG_TYPE_ERROR:
                    self._log("Error: " + str(content))
                    break

                if msg_type == self._MSG_TYPE_AUDIO_ONLY:
                    if content:
                        total_bytes += len(content)
                        if f:
                            f.write(content)
                        else:
                            audio_chunks.append(content)

                    # sequence < 0 表示最后一帧
                    if sequence < 0:
                        break
        finally:
            if f:
                f.close()
            await ws.close()

        return total_bytes if output_path else b"".join(audio_chunks)

    async def synthesize_and_play(
        self,
        text: str,
        audio_out,
        amp_sd,
        rate: int = 16000,
        # 可选覆盖参数
        voice_type: str = None,
        volume: float = None,
        speed: float = None,
        pitch: float = None,
        style: str = None,
        sample_rate: int = None,
        language: str = None,
    ):
        """
        流式合成并播放，边接收边写入 I2S，降低首字节延迟。

        Args:
            text:         待合成文本。
            audio_out:    已初始化的 I2S TX 实例。
            amp_sd:       功放 SD 引脚，播放前置高，播完后置低。
            rate:         采样率，用于计算尾部等待时长。
            voice_type:   临时指定音色（不传则用默认）。
            volume:       临时指定音量（不传则用默认）。
            speed:        临时指定语速（不传则用默认）。
            pitch:        临时指定音调（不传则用默认）。
            style:        临时指定情感（不传则用默认）。
            sample_rate:  临时指定采样率（不传则用默认）。

        Returns:
            int: 实际写入 I2S 的总字节数；失败返回 0。
        """
        # 使用传入值或默认值
        actual_voice = voice_type or self._default_voice_type
        actual_volume = volume if volume is not None else self._default_volume
        actual_speed = speed if speed is not None else self._default_speed
        actual_pitch = pitch if pitch is not None else self._default_pitch
        actual_style = style or self._default_style
        actual_sample_rate = sample_rate or self._default_sample_rate
        actual_language = language or self._default_language

        # 构建请求 payload
        reqid = _gen_uuid()
        payload = {
            "app": {
                "appid": self._app_id,
                "token": "access_token",
                "cluster": "volcano_tts"
            },
            "user": {
                "uid": "user_" + reqid[:8]
            },
            "audio": {
                "voice_type": actual_voice,
                "encoding": "pcm",  # 播放时固定使用 pcm
                "speed_ratio": actual_speed,
                "volume_ratio": actual_volume,
                "pitch_ratio": actual_pitch,
                "sample_rate": actual_sample_rate,
                "language": actual_language,
            },
            "request": {
                "reqid": reqid,
                "text": text,
                "text_type": "plain",
                "operation": "submit",
            }
        }
        if actual_style:
            payload["audio"]["emotion"] = actual_style

        # 连接 WebSocket
        ws = AsyncWebsocketClient(ms_delay_for_read=5)
        headers = [("Authorization", "Bearer; {}".format(self._access_token))]

        self._log("Connecting...")
        try:
            await ws.handshake(self._WS_URL, headers=headers, cert_reqs=0)
        except Exception as e:
            self._log("Handshake failed: " + str(e))
            return 0

        # 发送请求
        frame = self._build_frame(payload)
        await ws.send(frame)

        # 流式接收并播放
        amp_sd.value(1)
        total_bytes = 0
        swriter = asyncio.StreamWriter(audio_out)
        self._log("Streaming audio...")

        try:
            while await ws.open():
                data = await ws.recv()
                if data is None:
                    break

                msg_type, sequence, content = self._parse_response(data)

                if msg_type == self._MSG_TYPE_ERROR:
                    self._log("Error: " + str(content))
                    break

                if msg_type == self._MSG_TYPE_AUDIO_ONLY:
                    if content:
                        swriter.write(content)
                        await swriter.drain()
                        total_bytes += len(content)

                    # sequence < 0 表示最后一帧
                    if sequence < 0:
                        break
        finally:
            await ws.close()

        # 等待 I2S 缓冲区播完
        ibuf_ms = total_bytes * 1000 // (rate * 2)
        await asyncio.sleep_ms(ibuf_ms + 200)
        amp_sd.value(0)
        await asyncio.sleep_ms(300)
        self._log("Done, {} bytes".format(total_bytes))
        return total_bytes


# ======================================== 初始化配置 ===========================================

# ========================================  主程序  ===========================================