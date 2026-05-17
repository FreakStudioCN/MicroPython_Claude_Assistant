import sys
sys.modules.pop('volcengine_tts_v1_ws', None)
import asyncio
import network
import time
from machine import I2S, Pin
from volcengine_tts_v1_ws import VolcengineTTSV1WS

WIFI_SSID = "CU_kM7v"
WIFI_PASS = "a7tmyakw"

def _connect_wifi():
    sta = network.WLAN(network.STA_IF)
    if not sta.isconnected():
        sta.active(True)
        sta.connect(WIFI_SSID, WIFI_PASS)
        for _ in range(20):
            if sta.isconnected():
                break
            time.sleep(0.5)
    print("[WiFi] connected:", sta.ifconfig()[0])

async def test():
    _connect_wifi()

    tts = VolcengineTTSV1WS(
        app_id="5314645736",
        access_token="zwYWsUt4CGk5Cvp-2FYAFBl3X-Fh1Wmg",
        voice_type=VolcengineTTSV1WS.VOICE_BV701_STREAMING,
        volume=0.5,
    )

    amp_sd    = Pin(17, Pin.OUT, value=0)
    audio_out = I2S(
        1,
        sck=Pin(14), ws=Pin(15), sd=Pin(16),
        mode=I2S.TX, bits=16, format=I2S.MONO,
        rate=16000, ibuf=40000,
    )

    V = VolcengineTTSV1WS

    tests = [
        ("纯真少女 流式",      "嘿嘿，我是纯真少女，今天心情超好！",                          "zh", True,  {"voice_type": V.VOICE_CHUNZHEN}),
        ("奶气小生 流式",      "哇，这个好好玩哦，我也想要！",                                "zh", True,  {"voice_type": V.VOICE_XIAONAIGOU}),
        ("精灵向导 流式",      "欢迎来到魔法世界，我是你的精灵向导。",                          "zh", True,  {"voice_type": V.VOICE_JINGLING}),
        ("闷油瓶小哥 流式",    "嗯……还行吧，就这样。",                                        "zh", True,  {"voice_type": V.VOICE_MENYOUPING}),
        ("内敛才俊 流式",      "这件事，我有一些不同的看法。",                                  "zh", True,  {"voice_type": V.VOICE_NEILIAN}),
        ("甜美桃子 流式",      "桃子熟了，甜甜的，快来尝一口吧！",                              "zh", True,  {"voice_type": V.VOICE_TIANMEITAOZI}),
        ("暖阳女声 流式",      "你好，有什么我可以帮助你的吗？",                                "zh", True,  {"voice_type": V.VOICE_KEFUNV}),
        ("vv活泼女声 流式",    "哈哈，今天也是元气满满的一天！",                                "zh", True,  {"voice_type": V.VOICE_VV}),
        ("xiaohe台湾口音 流式","哇，這個真的超級好吃耶，你要不要試試看？",                      "zh", True,  {"voice_type": V.VOICE_XIAOHE}),
        ("广州德哥 开心 流式", "哇，今日真系好开心啊，饮茶先！",                                "zh", True,  {"voice_type": V.VOICE_GUANGZHOUDEGE, "style": "开心"}),
        ("广州德哥 愤怒 流式", "你搞咩啊！搞到我好嬲！",                                       "zh", True,  {"voice_type": V.VOICE_GUANGZHOUDEGE, "style": "愤怒"}),
        ("京腔侃爷 开心 流式", "嘿，今儿个真高兴，咱哥儿几个好好乐呵乐呵！",                    "zh", True,  {"voice_type": V.VOICE_JINGQIANGKANYE, "style": "开心"}),
        ("邻居阿姨 关心 流式", "孩子，吃了吗？来阿姨这儿，给你做好吃的。",                      "zh", True,  {"voice_type": V.VOICE_LINJUAYI, "style": "开心"}),
        ("北京小爷 傲娇 流式", "哟，这不是您嘛，稀客稀客，快请进！",                            "zh", True,  {"voice_type": V.VOICE_BEIJINGXIAOYE, "style": "开心"}),
        ("柔美女友 撒娇 流式", "人家不嘛，你就陪我嘛……",                                      "zh", True,  {"voice_type": V.VOICE_ROUMEINVYOU, "style": "撒娇"}),
        ("阳光青年 开心 流式", "加油！今天也是充满活力的一天！",                                "zh", True,  {"voice_type": V.VOICE_YANGGUANG, "style": "开心"}),
        ("魅力女友 撒娇 流式", "你怎么才来呀，人家等你好久了……",                               "zh", True,  {"voice_type": V.VOICE_MEILINVYOU, "style": "撒娇"}),
        ("爽快思思 开心 流式", "没问题！这事儿包在我身上，妥妥的！",                            "zh", True,  {"voice_type": V.VOICE_SHUANGKUAI, "style": "开心"}),
        ("甜美娇俏 流式",      "我是甜美娇俏的声音，欢迎来到语音合成测试。",                    "zh", True,  {"voice_type": V.VOICE_LINXUEYING}),
        ("成熟温柔 流式",      "岁月沉淀，温柔如初，这是成熟温柔的声音。",                      "zh", True,  {"voice_type": V.VOICE_CHENGSHU}),
        ("甜心小美 多情感 流式","今天天气真好，心情超级棒！",                                   "zh", True,  {"voice_type": V.VOICE_TIANXIN}),
        ("高冷御姐 多情感 流式","哼，你以为你是谁？",                                           "zh", True,  {"voice_type": V.VOICE_GAOLENGYUJIE}),
        ("粤语小溏 流式",      "你好，我系粤语小溏，欢迎嚟到广东话测试。",                      "zh", True,  {"voice_type": V.VOICE_YUEYUNV}),
        ("傲娇霸总 多情感 流式","本总裁的时间很宝贵，说重点。",                                 "zh", True,  {"voice_type": V.VOICE_AOJIAOBAZONG}),
        ("优柔公子 多情感 流式","唉，这件事情嘛，我也不知道该怎么说……",                        "zh", True,  {"voice_type": V.VOICE_YOUROUGONGZI}),
        ("语速 0.7 慢速",      "这是慢速语音合成测试，语速零点七倍。",                          "zh", False, {"voice_type": V.VOICE_SOPHIE, "speed": 0.7}),
        ("语速 1.5 快速",      "这是快速语音合成测试，语速一点五倍。",                          "zh", False, {"voice_type": V.VOICE_SOPHIE, "speed": 1.5}),
        ("语调 0.7 低沉",      "这是低音调测试，语调零点七倍。",                                "zh", False, {"voice_type": V.VOICE_QINQIE, "pitch": 0.7}),
        ("语调 1.4 高亢",      "这是高音调测试，语调一点四倍！",                                "zh", False, {"voice_type": V.VOICE_QINQIE, "pitch": 1.4}),
        ("Serena 美式英语 流式","Hello! I'm Serena, an American English voice. Nice to meet you!", "en", True, {"voice_type": V.VOICE_EN_SERENA}),
        ("Glen 美式英语 流式",  "Hey there! This is Glen speaking. How's it going today?",         "en", True, {"voice_type": V.VOICE_EN_GLEN}),
        ("Emily 英式英语 流式", "Good day! I'm Emily, a British English voice. Lovely to speak with you.", "en", True, {"voice_type": V.VOICE_EN_EMILY}),
        ("Corey 英式英语",      "Brilliant! This is Corey with a British accent. Quite splendid, isn't it?", "en", False, {"voice_type": V.VOICE_EN_COREY}),
        ("ひかる 日语 流式",    "こんにちは！私はひかるです。音声合成のテストへようこそ。",       "ja", True,  {"voice_type": V.VOICE_JA_HIKARU}),
    ]

    for i, (desc, text, lang, streaming, kwargs) in enumerate(tests):
        print("\n=== 测试{}: {} ===".format(i + 1, desc))
        path = "t{}.pcm".format(i + 1)
        try:
            if streaming:
                size = await tts.synthesize_and_play(
                    text, audio_out, amp_sd, language=lang, **kwargs
                )
                print("播放 {} 字节".format(size))
            size = await tts.synthesize(text, output_path=path, language=lang, **kwargs)
            print("保存 {} -> {} 字节".format(path, size))
        except Exception as e:
            print("ERROR:", e)
            import sys as _s; _s.print_exception(e)

    audio_out.deinit()
    import os
    print("\n=== 完成，PCM 文件:", [f for f in os.listdir("/") if f.endswith(".pcm")])

asyncio.run(test())
