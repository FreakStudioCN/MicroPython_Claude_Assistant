# coding: utf-8
"""
gen_voice_assets.py — PC端批量生成闹钟语音PCM文件
用法: python gen_voice_assets.py
依赖: pip install websockets
"""
import asyncio, json, struct, uuid, wave, winsound, tempfile, os
import websockets

for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_k, None)

def _resample_pcm(data: bytes, from_rate: int, to_rate: int) -> bytes:
    """简单整数比率降采样（16bit mono）。仅支持 from_rate 为 to_rate 整数倍。"""
    if from_rate == to_rate:
        return data
    step = from_rate // to_rate
    samples = memoryview(data).cast('h')  # int16
    out = bytearray()
    for i in range(0, len(samples), step):
        s = samples[i]
        out += struct.pack('<h', s)
    return bytes(out)

APP_ID       = "5314645736"
ACCESS_TOKEN = "zwYWsUt4CGk5Cvp-2FYAFBl3X-Fh1Wmg"
ASSETS_DIR   = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "device", "assets")
WS_URL       = "wss://openspeech.bytedance.com/api/v1/tts/ws_binary"

# ── 音频格式 ──────────────────────────────────────────────────────
FORMAT_PCM      = "pcm"
FORMAT_WAV      = "wav"
FORMAT_MP3      = "mp3"
FORMAT_OGG_OPUS = "ogg_opus"

# ── 情感/风格 ─────────────────────────────────────────────────────
EMOTION_HAPPY       = "happy"
EMOTION_SAD         = "sad"
EMOTION_ANGRY       = "angry"
EMOTION_SCARE       = "scare"
EMOTION_HATE        = "hate"
EMOTION_SURPRISE    = "surprise"
EMOTION_TEAR        = "tear"           # 哭腔
EMOTION_NOVEL       = "novel_dialog"   # 平和
EMOTION_NARRATOR    = "narrator"       # 旁白-舒缓
EMOTION_NARRATOR_IM = "narrator_immersive"
EMOTION_PLEASED     = "pleased"        # 愉悦
EMOTION_SORRY       = "sorry"          # 抱歉
EMOTION_ANNOYED     = "annoyed"        # 嗔怪
EMOTION_COMFORT     = "comfort"        # 安慰鼓励
EMOTION_LOVEY       = "lovey-dovey"    # 撒娇
EMOTION_TSUNDERE    = "tsundere"       # 傲娇
EMOTION_CHARMING    = "charming"       # 娇媚
EMOTION_CONNIVING   = "conniving"      # 绿茶
EMOTION_STORY       = "storytelling"   # 讲故事
EMOTION_RADIO       = "radio"          # 情感电台
EMOTION_YOGA        = "yoga"
EMOTION_CUSTOMER    = "customer_service"
EMOTION_PROFESSIONAL= "professional"
EMOTION_SERIOUS     = "serious"
EMOTION_ASSISTANT   = "assistant"
EMOTION_CHAT        = "chat"           # 自然对话
EMOTION_ADVERTISING = "advertising"

# ── 语种 ──────────────────────────────────────────────────────────
LANG_ZH   = "cn"
LANG_EN   = "en"
LANG_JA   = "ja"
LANG_TH   = "thth"
LANG_VI   = "vivn"
LANG_ID   = "id"
LANG_PT   = "ptbr"
LANG_ES   = "esmx"
# 方言
LANG_DONGBEI = "zh_dongbei"
LANG_YUEYU   = "zh_yueyu"
LANG_SHANGHAI= "zh_shanghai"
LANG_XIAN    = "zh_xian"
LANG_CHENGDU = "zh_chengdu"
LANG_TAIPU   = "zh_taipu"
LANG_GUANGXI = "zh_guangxi"

# ── 采样率 ────────────────────────────────────────────────────────
RATE_8K  = 8000
RATE_16K = 16000
RATE_24K = 24000
RATE_44K = 44100

# ── 音色短别名 → voice_type ───────────────────────────────────────
VOICES = {
    # ── 通用 BV 系列 ──────────────────────────────────────────────
    "bv701":            "BV701_streaming",
    "bv701_v2":         "BV701_V2_streaming",
    "bv700":            "BV700_streaming",
    "bv700_v2":         "BV700_V2_streaming",
    "bv001":            "BV001_streaming",
    "bv002":            "BV002_streaming",
    "bv004":            "BV004_streaming",
    "bv007":            "BV007_streaming",
    "bv008":            "BV008_streaming",
    "bv009":            "BV009_streaming",
    "bv100":            "BV100_streaming",
    "bv102":            "BV102_streaming",
    "bv104":            "BV104_streaming",
    "bv107":            "BV107_streaming",
    "bv113":            "BV113_streaming",
    "bv115":            "BV115_streaming",
    "bv119":            "BV119_streaming",
    "bv120":            "BV120_streaming",
    "bv123":            "BV123_streaming",
    "bv405":            "BV405_streaming",
    "bv406":            "BV406_streaming",
    "bv407":            "BV407_streaming",
    "bv421":            "BV421_streaming",   # 天才少女，8国语言
    "bv702":            "BV702_streaming",   # Stefan，多语种
    "bv704":            "BV704_streaming",   # 方言灿灿
    # ── 多情感男声（emo_v2_mars）────────────────────────────────
    "aojiaobazong":     "zh_male_aojiaobazong_emo_v2_mars_bigtts",
    "yourougongzi":     "zh_male_yourougongzi_emo_v2_mars_bigtts",
    "ruyayichen":       "zh_male_ruyayichen_emo_v2_mars_bigtts",
    "junlang":          "zh_male_junlangnanyou_emo_v2_mars_bigtts",
    "yangguang_emo":    "zh_male_yangguangqingnian_emo_v2_mars_bigtts",
    "beijingxiaoye_emo":"zh_male_beijingxiaoye_emo_v2_mars_bigtts",
    "guangzhoudege":    "zh_male_guangzhoudege_emo_mars_bigtts",
    "jingqiangkanye_emo":"zh_male_jingqiangkanye_emo_mars_bigtts",
    # ── 多情感女声（emo_v2_mars）────────────────────────────────
    "tianxin":          "zh_female_tianxinxiaomei_emo_v2_mars_bigtts",
    "gaolengyujie_emo": "zh_female_gaolengyujie_emo_v2_mars_bigtts",
    "roumeinvyou":      "zh_female_roumeinvyou_emo_v2_mars_bigtts",
    "meilinvyou_emo":   "zh_female_meilinvyou_emo_v2_mars_bigtts",
    "shuangkuai_emo":   "zh_female_shuangkuaisisi_emo_v2_mars_bigtts",
    "linjuayi_emo":     "zh_female_linjuayi_emo_v2_mars_bigtts",
    "yangguang_emo2":   "zh_male_yangguangqingnian_emo_v2_mars_bigtts",
    # ── moon 系列（通用/角色/方言）──────────────────────────────
    "wanqudashu":       "zh_female_wanqudashu_moon_bigtts",
    "daimengchuanmei":  "zh_female_daimengchuanmei_moon_bigtts",
    "guangzhoudege_m":  "zh_male_guozhoudege_moon_bigtts",
    "beijingxiaoye_m":  "zh_male_beijingxiaoye_moon_bigtts",
    "shaonianzixin":    "zh_male_shaonianzixin_moon_bigtts",
    "meilinvyou_m":     "zh_female_meilinvyou_moon_bigtts",
    "shenyeboke_m":     "zh_male_shenyeboke_moon_bigtts",
    "sajiaonvyou":      "zh_female_sajiaonvyou_moon_bigtts",
    "yuanqinvyou":      "zh_female_yuanqinvyou_moon_bigtts",
    "haoyuxiaoge":      "zh_male_haoyuxiaoge_moon_bigtts",
    "guangxiyuanzhou":  "zh_male_guangxiyuanzhou_moon_bigtts",
    "meituojieer":      "zh_female_meituojieer_moon_bigtts",
    "yuzhouzixuan":     "zh_male_yuzhouzixuan_moon_bigtts",
    "linjianvhai":      "zh_female_linjianvhai_moon_bigtts",
    "gaolengyujie_m":   "zh_female_gaolengyujie_moon_bigtts",
    "yuanboxiaoshu":    "zh_male_yuanboxiaoshu_moon_bigtts",
    "yangguang_m":      "zh_male_yangguangqingnian_moon_bigtts",
    "aojiaobazong_m":   "zh_male_aojiaobazong_moon_bigtts",
    "jingqiangkanye_m": "zh_male_jingqiangkanye_moon_bigtts",
    "shuangkuai_m":     "zh_female_shuangkuaisisi_moon_bigtts",
    "wennuanahu":       "zh_male_wennuanahu_moon_bigtts",
    "wanwanxiaohe":     "zh_female_wanwanxiaohe_moon_bigtts",
    "wenrouxiaoya":     "zh_female_wenrouxiaoya_moon_bigtts",
    "tianmeixiaoyuan":  "zh_female_tianmeixiaoyuan_moon_bigtts",
    "qingchezizi":      "zh_female_qingchezizi_moon_bigtts",
    "dongfanghaoran":   "zh_male_dongfanghaoran_moon_bigtts",
    "jieshuoxiaoming":  "zh_male_jieshuoxiaoming_moon_bigtts",
    "kailangjiejie":    "zh_female_kailangjiejie_moon_bigtts",
    "linjiananhai":     "zh_male_linjiananhai_moon_bigtts",
    "tianmeiyueyue":    "zh_female_tianmeiyueyue_moon_bigtts",
    "xinlingjitang":    "zh_female_xinlingjitang_moon_bigtts",
    "dongfanghaoran":   "zh_male_dongfanghaoran_moon_bigtts",
    # ── mars 系列 ────────────────────────────────────────────────
    "cancan_mars":      "zh_female_cancan_mars_bigtts",
    "tiancaitongsheng": "zh_male_tiancaitongsheng_mars_bigtts",
    "naiqimengwa":      "zh_male_naiqimengwa_mars_bigtts",
    "sunwukong":        "zh_male_sunwukong_mars_bigtts",
    "xionger":          "zh_male_xionger_mars_bigtts",
    "peiqi":            "zh_female_peiqi_mars_bigtts",
    "zhixingnvsheng":   "zh_female_zhixingnvsheng_mars_bigtts",
    "qingxinnvsheng":   "zh_female_qingxinnvsheng_mars_bigtts",
    "changtianyi":      "zh_male_changtianyi_mars_bigtts",
    "popo":             "zh_female_popo_mars_bigtts",
    "wuzetian":         "zh_female_wuzetian_mars_bigtts",
    "linjia":           "zh_female_linjia_mars_bigtts",
    "shaoergushi":      "zh_female_shaoergushi_mars_bigtts",
    "silang":           "zh_male_silang_mars_bigtts",
    "gujie":            "zh_female_gujie_mars_bigtts",
    "yingtaowanzi":     "zh_female_yingtaowanzi_mars_bigtts",
    "jieshuonansheng":  "zh_male_jieshuonansheng_mars_bigtts",
    "jitangmeimei":     "zh_female_jitangmeimei_mars_bigtts",
    "chunhui":          "zh_male_chunhui_mars_bigtts",
    "qingshuangnanda":  "zh_male_qingshuangnanda_mars_bigtts",
    "tiexinnvsheng":    "zh_female_tiexinnvsheng_mars_bigtts",
    "qiaopinvsheng":    "zh_female_qiaopinvsheng_mars_bigtts",
    "mengyatou":        "zh_female_mengyatou_mars_bigtts",
    "ruyaqingnian":     "zh_male_ruyaqingnian_mars_bigtts",
    "baqiqingshu":      "zh_male_baqiqingshu_mars_bigtts",
    "qingcang_mars":    "zh_male_qingcang_mars_bigtts",
    "yangguang_mars":   "zh_male_yangguangqingnian_mars_bigtts",
    "gufengshaoyu":     "zh_female_gufengshaoyu_mars_bigtts",
    "wenroushunv":      "zh_female_wenroushunv_mars_bigtts",
    # ── ICL 角色扮演系列 ─────────────────────────────────────────
    "zhixingwenwan":    "ICL_zh_female_zhixingwenwan_tob",
    "lvchaxiaoge":      "ICL_zh_male_lvchaxiaoge_tob",
    "jiaoruoluoli":     "ICL_zh_female_jiaoruoluoli_tob",
    "lengdanshuli":     "ICL_zh_male_lengdanshuli_tob",
    "nuanxintitie":     "ICL_zh_male_nuanxintitie_tob",
    "hanhoudunshi":     "ICL_zh_male_hanhoudunshi_tob",
    "wenrouwenya":      "ICL_zh_female_wenrouwenya_tob",
    "aiqilingren":      "ICL_zh_male_aiqilingren_tob",
    "kailangqingkuai":  "ICL_zh_male_kailangqingkuai_tob",
    "huopodiaoman":     "ICL_zh_female_huopodiaoman_tob",
    "guzhibingjiao":    "ICL_zh_male_guzhibingjiao_tob",
    "huoposhuanglang":  "ICL_zh_male_huoposhuanglang_tob",
    "sajiaonianren":    "ICL_zh_male_sajiaonianren_tob",
    "aomanjiaosheng":   "ICL_zh_female_aomanjiaosheng_tob",
    "xiaosasuixing":    "ICL_zh_male_xiaosasuixing_tob",
    "fuheigongzi":      "ICL_zh_male_fuheigongzi_tob",
    "guiyishenmi":      "ICL_zh_male_guiyishenmi_tob",
    "ruyacaijun":       "ICL_zh_male_ruyacaijun_tob",
    "bingjiaobailian":  "ICL_zh_male_bingjiaobailian_tob",
    "zhengzhiqingnian": "ICL_zh_male_zhengzhiqingnian_tob",
    "shuaizhenxiaohuo": "ICL_zh_male_shuaizhenxiaohuo_tob",
    "jiaohannvwang":    "ICL_zh_female_jiaohannvwang_tob",
    "bingjiaomengmei":  "ICL_zh_female_bingjiaomengmei_tob",
    "qingsenaigou":     "ICL_zh_male_qingsenaigou_tob",
    "chunzhenxuedi":    "ICL_zh_male_chunzhenxuedi_tob",
    "nuanxinxuejie":    "ICL_zh_female_nuanxinxuejie_tob",
    "keainvsheng":      "ICL_zh_female_keainvsheng_tob",
    "bingruoshaonv":    "ICL_zh_female_bingruoshaonv_tob",
    "huoponvhai":       "ICL_zh_female_huoponvhai_tob",
    "heainainai":       "ICL_zh_female_heainainai_tob",
    "linjuayi_icl":     "ICL_zh_female_linjuayi_tob",
    # ── ICL 新增角色 ─────────────────────────────────────────────
    "chengshujiejie":   "ICL_zh_female_chengshujiejie_tob",
    "bingjiaojiejie":   "ICL_zh_female_bingjiaojiejie_tob",
    "youroubangzhu":    "ICL_zh_male_youroubangzhu_tob",
    "yourougongzi_icl": "ICL_zh_male_yourougongzi_tob",
    "wumeiyujie":       "ICL_zh_female_wumeiyujie_tob",
    "tiaopigongzhu":    "ICL_zh_female_tiaopigongzhu_tob",
    "aojiaonvyou":      "ICL_zh_female_aojiaonvyou_tob",
    "tiexinnanyou":     "ICL_zh_male_tiexinnanyou_tob",
    "shaonianjiangjun": "ICL_zh_male_shaonianjiangjun_tob",
    "tiexinnvyou":      "ICL_zh_female_tiexinnvyou_tob",
    "bingjiaogege":     "ICL_zh_male_bingjiaogege_tob",
    "xuebanantongzhuo": "ICL_zh_male_xuebanantongzhuo_tob",
    "youmoshushu":      "ICL_zh_male_youmoshushu_tob",
    "xingganyujie":     "ICL_zh_female_xingganyujie_tob",
    "jiaxiaozi":        "ICL_zh_female_jiaxiaozi_tob",
    "lengjunshangsi":   "ICL_zh_male_lengjunshangsi_tob",
    "wenrounantongzhuo":"ICL_zh_male_wenrounantongzhuo_tob",
    "bingjiaodidi":     "ICL_zh_male_bingjiaodidi_tob",
    "youmodaye":        "ICL_zh_male_youmodaye_tob",
    "aomanshaoye":      "ICL_zh_male_aomanshaoye_tob",
    "shenmifashi":      "ICL_zh_male_shenmifashi_tob",
    "ruyagongzi":       "ICL_zh_male_flc_v1_tob",
    "lengkugege":       "ICL_zh_male_lengkugege_v1_tob",
    "younidashuo":      "ICL_zh_male_you_tob",
    "xiaozangxiaoge":   "ICL_zh_male_ms_tob",
    "jilingxiaohu":     "ICL_zh_male_shenmi_v1_tob",
    # ── mars 新增 ────────────────────────────────────────────────
    "fanjuanqingnian":  "zh_male_fanjuanqingnian_mars_bigtts",
    "dongmanhaimian":   "zh_male_dongmanhaimian_mars_bigtts",
    "wenrouxiaoge":     "zh_male_wenrouxiaoge_mars_bigtts",
    "lanxiaoyang":      "zh_male_lanxiaoyang_mars_bigtts",
    # ── 英语新增 ─────────────────────────────────────────────────
    "en_jackson_mars":  "en_male_jackson_mars_bigtts",
    "en_amanda":        "en_female_amanda_mars_bigtts",
    # ── 多语种新增 ───────────────────────────────────────────────
    "multi_masao":      "multi_male_xudong_conversation_wvae_bigtts",
    # ── 英语 ─────────────────────────────────────────────────────
    "en_ariana":        "BV503_streaming",
    "en_jackson":       "BV504_streaming",
    "en_anna_bv":       "BV040_streaming",
    "en_ava":           "BV511_streaming",
    "en_anna_mars":     "en_female_anna_mars_bigtts",
    "en_adam":          "en_male_adam_mars_bigtts",
    "en_sarah":         "en_female_sarah_mars_bigtts",
    "en_dryw":          "en_male_dryw_mars_bigtts",
    "en_smith":         "en_male_smith_mars_bigtts",
    # ── 日语/多语种 ───────────────────────────────────────────────
    "ja_genki":         "BV520_streaming",
    "ja_moe":           "BV521_streaming",
    "ja_male":          "BV524_streaming",
    "multi_haruko":     "multi_female_shuangkuaisisi_moon_bigtts",
    "multi_kazune":     "multi_male_jingqiangkanye_moon_bigtts",
    "multi_akemi":      "multi_female_gaolengyujie_moon_bigtts",
    "multi_hiroshi":    "multi_male_wanqudashu_moon_bigtts",
}

VOICE_LABELS = {
    # BV 系列
    "bv701":"BV701 通用女","bv701_v2":"BV701 V2","bv700":"BV700 通用男","bv700_v2":"BV700 V2",
    "bv001":"BV001","bv002":"BV002","bv004":"BV004","bv007":"BV007","bv008":"BV008","bv009":"BV009",
    "bv100":"BV100","bv102":"BV102","bv104":"BV104","bv107":"BV107","bv113":"BV113","bv115":"BV115",
    "bv119":"BV119","bv120":"BV120","bv123":"BV123",
    "bv405":"BV405","bv406":"BV406","bv407":"BV407",
    "bv421":"天才少女(多语)","bv702":"Stefan(多语)","bv704":"方言灿灿",
    # 多情感男声
    "aojiaobazong":"傲娇霸总","yourougongzi":"优柔公子","ruyayichen":"儒雅一尘",
    "junlang":"俊朗男友","yangguang_emo":"阳光青年·情感","beijingxiaoye_emo":"北京小爷·情感",
    "guangzhoudege":"广州德哥","jingqiangkanye_emo":"京腔侃爷·情感",
    # 多情感女声
    "tianxin":"甜心小美","gaolengyujie_emo":"高冷御姐·情感","roumeinvyou":"柔美女友",
    "meilinvyou_emo":"魅力女友·情感","shuangkuai_emo":"爽快思思·情感",
    "linjuayi_emo":"邻居阿姨·情感","yangguang_emo2":"阳光青年·情感2",
    # moon 系列
    "wanqudashu":"万趣大叔","daimengchuanmei":"呆萌传媒","guangzhoudege_m":"广州德哥·moon",
    "beijingxiaoye_m":"北京小爷·moon","shaonianzixin":"少年自信","meilinvyou_m":"魅力女友·moon",
    "shenyeboke_m":"深夜播客","sajiaonvyou":"撒娇女友","yuanqinvyou":"元气女友",
    "haoyuxiaoge":"好友小哥","guangxiyuanzhou":"广西远舟","meituojieer":"美托杰尔",
    "yuzhouzixuan":"宇宙紫轩","linjianvhai":"林间女孩","gaolengyujie_m":"高冷御姐·moon",
    "yuanboxiaoshu":"渊博小叔","yangguang_m":"阳光青年·moon","aojiaobazong_m":"傲娇霸总·moon",
    "jingqiangkanye_m":"京腔侃爷·moon","shuangkuai_m":"爽快思思·moon","wennuanahu":"温暖阿虎",
    "wanwanxiaohe":"婉婉小荷","wenrouxiaoya":"温柔小雅","tianmeixiaoyuan":"甜美小苑",
    "qingchezizi":"清澈紫紫","dongfanghaoran":"东方浩然","jieshuoxiaoming":"解说小明",
    "kailangjiejie":"开朗姐姐","linjiananhai":"林间男孩","tianmeiyueyue":"甜美月月",
    "xinlingjitang":"心灵鸡汤",
    # mars 系列
    "cancan_mars":"灿灿·mars","tiancaitongsheng":"天才童声","naiqimengwa":"奶气萌娃",
    "sunwukong":"孙悟空","xionger":"熊二","peiqi":"佩奇","zhixingnvsheng":"知性女声",
    "qingxinnvsheng":"清新女声","changtianyi":"畅天翼","popo":"婆婆","wuzetian":"武则天",
    "linjia":"林佳","shaoergushi":"少儿故事","silang":"四郎","gujie":"古姐",
    "yingtaowanzi":"樱桃丸子","jieshuonansheng":"解说男声","jitangmeimei":"鸡汤妹妹",
    "chunhui":"春晖","qingshuangnanda":"清爽男大","tiexinnvsheng":"铁心女声",
    "qiaopinvsheng":"俏皮女声","mengyatou":"萌丫头","ruyaqingnian":"儒雅青年",
    "baqiqingshu":"霸气情书","qingcang_mars":"青苍·mars","yangguang_mars":"阳光青年·mars",
    "gufengshaoyu":"古风少羽","wenroushunv":"温柔淑女",
    # ICL 角色扮演
    "zhixingwenwan":"知性温婉","lvchaxiaoge":"绿茶小哥","jiaoruoluoli":"娇弱萝莉",
    "lengdanshuli":"冷淡疏离","nuanxintitie":"暖心体贴","hanhoudunshi":"憨厚敦实",
    "wenrouwenya":"温柔文雅","aiqilingren":"爱奇邻人","kailangqingkuai":"开朗轻快",
    "huopodiaoman":"活泼刁蛮","guzhibingjiao":"固执冰娇","huoposhuanglang":"活泼爽朗",
    "sajiaonianren":"撒娇黏人","aomanjiaosheng":"傲慢娇声","xiaosasuixing":"潇洒随性",
    "fuheigongzi":"腹黑公子","guiyishenmi":"诡异神秘","ruyacaijun":"儒雅才俊",
    "bingjiaobailian":"冰娇白莲","zhengzhiqingnian":"正直青年","shuaizhenxiaohuo":"帅真小伙",
    "jiaohannvwang":"娇悍女王","bingjiaomengmei":"冰娇萌妹","qingsenaigou":"清涩奶狗",
    "chunzhenxuedi":"纯真学弟","nuanxinxuejie":"暖心学姐","keainvsheng":"可爱女声",
    "bingruoshaonv":"病弱少女","huoponvhai":"活泼女孩","heainainai":"和蔼奶奶",
    "linjuayi_icl":"邻居阿姨·ICL",
    # ICL 新增
    "chengshujiejie":"成熟姐姐","bingjiaojiejie":"病娇姐姐","youroubangzhu":"优柔帮主",
    "yourougongzi_icl":"优柔公子·ICL","wumeiyujie":"妩媚御姐","tiaopigongzhu":"调皮公主",
    "aojiaonvyou":"傲娇女友","tiexinnanyou":"贴心男友","shaonianjiangjun":"少年将军",
    "tiexinnvyou":"贴心女友","bingjiaogege":"病娇哥哥","xuebanantongzhuo":"学霸男同桌",
    "youmoshushu":"幽默叔叔","xingganyujie":"性感御姐","jiaxiaozi":"假小子",
    "lengjunshangsi":"冷峻上司","wenrounantongzhuo":"温柔男同桌","bingjiaodidi":"病娇弟弟",
    "youmodaye":"幽默大爷","aomanshaoye":"傲慢少爷","shenmifashi":"神秘法师",
    "ruyagongzi":"儒雅公子","lengkugege":"冷酷哥哥","younidashuo":"油腻大叔",
    "xiaozangxiaoge":"嚣张小哥","jilingxiaohu":"机灵小伙",
    # mars 新增
    "fanjuanqingnian":"反卷青年","dongmanhaimian":"亮嗓萌仔",
    "wenrouxiaoge":"温柔小哥","lanxiaoyang":"懒音绵宝",
    # 英语新增
    "en_jackson_mars":"Jackson Mars(英)","en_amanda":"Amanda(英)",
    # 多语种新增
    "multi_masao":"正男/Daníel(多语)",
    # 英语
    "en_ariana":"Ariana(英)","en_jackson":"Jackson(英)","en_anna_bv":"Anna BV(英)",
    "en_ava":"Ava(英)","en_anna_mars":"Anna Mars(英)","en_adam":"Adam(英)",
    "en_sarah":"Sarah(英)","en_dryw":"Dryw(英)","en_smith":"Smith(英)",
    # 日语/多语种
    "ja_genki":"元気(日)","ja_moe":"萌え(日)","ja_male":"男声(日)",
    "multi_haruko":"春子(多语)","multi_kazune":"和音(多语)",
    "multi_akemi":"明美(多语)","multi_hiroshi":"浩(多语)",
}

# ── 状态文本 ──────────────────────────────────────────────────────
SAMPLES = {
    "done": [
        "好的主人，这个任务我已经顺利完成了，您可以查看一下结果。",
        "搞定了，整个流程跑下来没有任何问题，请您放心。",
        "任务已经按照您的要求处理完毕，随时可以继续下一步。",
        "完成了，主人辛苦了，有需要的话随时告诉我。",
        "这个任务我处理好了，结果已经准备就绪，请您查收。",
    ],
    "error": [
        "主人，执行过程中遇到了一个错误，需要您来看一下具体情况。",
        "不好意思，这里出了点问题，我暂时没办法继续，需要您介入处理一下。",
        "任务执行失败了，错误信息已经记录下来，请您确认一下再决定怎么处理。",
        "遇到了一个小麻烦，我自己解决不了，麻烦主人看一眼。",
        "这个步骤出错了，建议您检查一下相关配置，然后再重新尝试。",
    ],
    "pending": [
        "主人，这一步需要您来做个决定，我在这里等您的指示。",
        "当前操作需要您手动确认一下才能继续，请您看看是否可以执行。",
        "我已经准备好了，就等您发话，您批准之后我马上继续。",
        "有一个操作需要您审批，请您确认没问题之后我再往下走。",
        "流程暂停在这里了，需要您来审批一下，我随时待命。",
    ],
    "working": [
        "主人放心，我正在认真处理这个任务，进展很顺利。",
        "正在执行中，这个可能需要一点时间，请您稍等片刻。",
        "别担心，我还在跑，任务没有中断，请继续等待。",
        "任务还在进行中，主人可以先去休息一下，完成了我会通知您。",
        "我在全力处理这件事，请主人耐心等一下，快了。",
    ],
    "connect": [
        "连接成功，我已经上线了，随时可以为您处理任务。",
        "主人好，我已经准备就绪，有什么需要尽管说。",
    ],
    "disconnect": [
        "主人，连接已断开，我暂时无法接收新任务，请检查一下连接状态。",
        "与主人的连接中断了，我会持续等待重新连接，请稍候。",
    ],
    "idle": [
        "当前没有进行中的任务，我在待机状态，随时等待您的新指令。",
    ],
    "startup": [
        "主人你好，我是码克助手，负责监控 Claude 任务进度，随时提醒您查看终端。",
    ],
}

# ── 开发者编辑这里 ────────────────────────────────────────────────
# (voice_short, pitch, speed, volume, emotion, sample_rate)
JOBS = [
    ("bv701", 1.2, 1.6, 1.5, None, 8000),
]


# ── WebSocket TTS ─────────────────────────────────────────────────
_HEADER = bytes([0x11, 0x10, 0x10, 0x00])  # v1, JSON, no compress

def _build_frame(payload: dict) -> bytes:
    data = json.dumps(payload, ensure_ascii=False).encode()
    return _HEADER + struct.pack(">I", len(data)) + data

def _parse_chunk(res: bytes):
    """返回 (audio_bytes, is_last)"""
    msg_type = (res[1] >> 4) & 0x0f
    flags    = res[1] & 0x0f
    if msg_type == 0xb:  # audio-only
        has_seq = bool(flags & 0x01)
        if not has_seq:
            return b"", False  # ACK，无音频
        offset = 4
        seq    = struct.unpack(">i", res[offset:offset + 4])[0]
        offset += 4  # skip sequence number
        offset += 4  # skip payload_size field
        return res[offset:], seq < 0
    if msg_type == 0xf:
        raise RuntimeError("TTS error: " + res[12:].decode(errors="replace"))
    return b"", False

async def synthesize(text, voice_type, pitch, speed, volume, emotion,
                     app_id=None, access_token=None, sample_rate=16000) -> bytes:
    app_id       = app_id or APP_ID
    access_token = access_token or ACCESS_TOKEN
    reqid = str(uuid.uuid4()).replace("-", "")
    payload = {
        "app":     {"appid": app_id, "token": "access_token", "cluster": "volcano_tts"},
        "user":    {"uid": "gen_" + reqid[:8]},
        "audio":   {
            "voice_type": voice_type, "encoding": "pcm",
            "speed_ratio": speed, "volume_ratio": volume, "pitch_ratio": pitch,
            "sample_rate": 16000,  # API 固定 16kHz，客户端再降采样
        },
        "request": {"reqid": reqid, "text": text, "text_type": "plain", "operation": "submit"},
    }
    if emotion:
        payload["audio"]["emotion"] = emotion

    headers = {"Authorization": f"Bearer; {access_token}"}
    chunks = []
    async with websockets.connect(WS_URL, additional_headers=headers, ping_interval=None) as ws:
        await ws.send(_build_frame(payload))
        while True:
            audio, done = _parse_chunk(await ws.recv())
            if audio:
                chunks.append(audio)
            if done:
                break
    pcm = b"".join(chunks)
    return _resample_pcm(pcm, 16000, sample_rate)


# ── 播放 PCM ──────────────────────────────────────────────────────
def play_pcm(pcm: bytes, rate=16000):
    tmp = tempfile.mktemp(suffix=".wav")
    with wave.open(tmp, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(pcm)
    winsound.PlaySound(tmp, winsound.SND_FILENAME)
    os.unlink(tmp)


# ── 主流程 ────────────────────────────────────────────────────────
async def main(auto=False):
    os.makedirs(ASSETS_DIR, exist_ok=True)
    for voice_short, pitch, speed, volume, emotion, sample_rate in JOBS:
        voice_type = VOICES[voice_short]
        tag = f"{voice_short}-{pitch}-{speed}-{volume}-{sample_rate}"
        for state, texts in SAMPLES.items():
            for idx, text in enumerate(texts, 1):
                fname = f"{tag}-{state}-{idx:02d}.pcm"
                fpath = os.path.join(ASSETS_DIR, fname)
                if os.path.exists(fpath):
                    print(f"[skip] {fname}")
                    continue
                print(f"\n[gen]  {fname}")
                print(f"       {text}")
                try:
                    pcm = await synthesize(text, voice_type, pitch, speed, volume, emotion,
                                           sample_rate=sample_rate)
                except Exception as e:
                    print(f"  ERROR: {e}")
                    continue
                play_pcm(pcm, rate=sample_rate)
                if auto:
                    ans = "k"
                else:
                    ans = input("  [k]eep / [r]etry / [s]kip > ").strip().lower()
                    while ans == "r":
                        try:
                            pcm = await synthesize(text, voice_type, pitch, speed, volume, emotion)
                        except Exception as e:
                            print(f"  ERROR: {e}"); break
                        play_pcm(pcm)
                        ans = input("  [k]eep / [r]etry / [s]kip > ").strip().lower()
                if ans != "k":
                    print("  skipped")
                    continue
                with open(fpath, "wb") as f:
                    f.write(pcm)
                print(f"  saved {len(pcm):,} bytes")

    print("\n=== 完成 ===")
    print("PCM files:", [f for f in os.listdir(ASSETS_DIR) if f.endswith(".pcm")])


# ── GUI ───────────────────────────────────────────────────────────
import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, messagebox
import threading

_VOICE_KEYS     = list(VOICES.keys())
_VOICE_DISPLAYS = [VOICE_LABELS.get(k, k) for k in _VOICE_KEYS]
_VOICE_BY_DISP  = {d: k for k, d in zip(_VOICE_KEYS, _VOICE_DISPLAYS)}

_EMOTION_LABELS = [
    ("(无)",                None),
    ("开心 happy",          "happy"),
    ("悲伤 sad",            "sad"),
    ("愤怒 angry",          "angry"),
    ("恐惧 scare",          "scare"),
    ("厌恶 hate",           "hate"),
    ("惊讶 surprise",       "surprise"),
    ("哭腔 tear",           "tear"),
    ("平和 novel_dialog",   "novel_dialog"),
    ("旁白舒缓 narrator",   "narrator"),
    ("旁白沉浸",            "narrator_immersive"),
    ("愉悦 pleased",        "pleased"),
    ("抱歉 sorry",          "sorry"),
    ("嗔怪 annoyed",        "annoyed"),
    ("安慰鼓励 comfort",    "comfort"),
    ("撒娇 lovey-dovey",    "lovey-dovey"),
    ("傲娇 tsundere",       "tsundere"),
    ("娇媚 charming",       "charming"),
    ("绿茶 conniving",      "conniving"),
    ("讲故事 storytelling", "storytelling"),
    ("情感电台 radio",      "radio"),
    ("瑜伽 yoga",           "yoga"),
    ("客服 customer_service","customer_service"),
    ("专业 professional",   "professional"),
    ("严肃 serious",        "serious"),
    ("助手 assistant",      "assistant"),
    ("自然对话 chat",       "chat"),
    ("广告 advertising",    "advertising"),
]
_EMOTIONS       = [label for label, _ in _EMOTION_LABELS]
_EMOTION_BY_DISP = {label: val for label, val in _EMOTION_LABELS}

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("豆包语音PCM生成器")
        self.resizable(False, False)
        self._pcm = None
        self._pcm_rate = 16000
        self._build()

    def _build(self):
        pad = dict(padx=6, pady=3)

        # ── 凭证配置 ──────────────────────────────────────────────
        fc = ttk.LabelFrame(self, text="凭证配置")
        fc.pack(fill="x", padx=8, pady=4)

        def _open_console():
            import webbrowser
            webbrowser.open("https://console.volcengine.com/speech/service/10007")

        ttk.Button(fc, text="豆包语音控制台", command=_open_console).grid(
            row=0, column=0, columnspan=2, **pad, sticky="w")

        for row, (label, attr, default) in enumerate([
            ("App ID",       "_cfg_appid",  APP_ID),
            ("Access Token", "_cfg_token",  ACCESS_TOKEN),
            ("Secret Key",   "_cfg_secret", ""),
        ], start=1):
            ttk.Label(fc, text=label).grid(row=row, column=0, **pad, sticky="e")
            var = tk.StringVar(value=default)
            setattr(self, attr, var)
            show = "*" if label == "Secret Key" else None
            e = ttk.Entry(fc, textvariable=var, width=42, show=show)
            e.grid(row=row, column=1, **pad, sticky="w")

        self._verify_lbl = ttk.Label(fc, text="")
        self._verify_lbl.grid(row=4, column=1, **pad, sticky="w")
        ttk.Button(fc, text="验证", command=self._verify).grid(row=4, column=0, **pad)

        # ── 音色行 ────────────────────────────────────────────────
        f1 = ttk.LabelFrame(self, text="音色 / 参数")
        f1.pack(fill="x", padx=8, pady=4)

        ttk.Label(f1, text="音色").grid(row=0, column=0, **pad, sticky="e")
        self._voice_var = tk.StringVar(value=_VOICE_DISPLAYS[0])
        self._cb_voice = ttk.Combobox(f1, textvariable=self._voice_var,
                                values=_VOICE_DISPLAYS, width=28, state="readonly")
        self._cb_voice.grid(row=0, column=1, **pad, sticky="w")
        ttk.Button(f1, text="+ 添加", command=self._add_voice).grid(row=0, column=2, **pad)

        ttk.Label(f1, text="情感").grid(row=1, column=0, **pad, sticky="e")
        self._emo_var = tk.StringVar(value="(无)")
        ttk.Combobox(f1, textvariable=self._emo_var, values=_EMOTIONS,
                     width=20, state="readonly").grid(row=1, column=1, **pad, sticky="w")

        # 语速 / 语调 / 音量
        for col, (label, attr, lo, hi, default) in enumerate([
            ("语速", "_speed", 0.5, 2.0, 1.3),
            ("语调", "_pitch", 0.5, 2.0, 1.0),
            ("音量", "_vol",   0.1, 3.0, 1.0),
        ]):
            setattr(self, attr, tk.DoubleVar(value=default))
            ttk.Label(f1, text=label).grid(row=2, column=col*2, **pad, sticky="e")
            s = ttk.Scale(f1, from_=lo, to=hi, variable=getattr(self, attr),
                          orient="horizontal", length=120)
            s.grid(row=2, column=col*2+1, **pad)
            # value label
            lv = ttk.Label(f1, width=4)
            lv.grid(row=3, column=col*2+1, pady=0)
            def _upd(v, lbl=lv, var=getattr(self, attr)):
                lbl.config(text=f"{var.get():.2f}")
            getattr(self, attr).trace_add("write", lambda *a, fn=_upd, v=getattr(self, attr): fn(v))
            _upd(None)

        ttk.Label(f1, text="采样率").grid(row=4, column=0, **pad, sticky="e")
        self._rate_var = tk.IntVar(value=8000)
        ttk.Combobox(f1, textvariable=self._rate_var,
                     values=[8000, 16000, 24000], width=8,
                     state="readonly").grid(row=4, column=1, **pad, sticky="w")

        # ── 状态 / 文本 ───────────────────────────────────────────
        f2 = ttk.LabelFrame(self, text="状态 / 文本")
        f2.pack(fill="both", expand=True, padx=8, pady=4)

        ttk.Label(f2, text="状态").grid(row=0, column=0, **pad, sticky="e")
        self._state_var = tk.StringVar(value=list(SAMPLES.keys())[0])
        cb_state = ttk.Combobox(f2, textvariable=self._state_var,
                                values=list(SAMPLES.keys()), width=14, state="readonly")
        cb_state.grid(row=0, column=1, **pad, sticky="w")
        cb_state.bind("<<ComboboxSelected>>", self._on_state)

        self._listbox = tk.Listbox(f2, height=6, width=55, selectmode="single")
        self._listbox.grid(row=1, column=0, columnspan=3, **pad)
        self._listbox.bind("<<ListboxSelect>>", self._on_text_select)

        ttk.Label(f2, text="编辑文本").grid(row=2, column=0, **pad, sticky="ne")
        self._text_box = tk.Text(f2, height=3, width=55, wrap="word")
        self._text_box.grid(row=2, column=1, columnspan=2, **pad)

        self._on_state()

        # ── 按钮 / 状态栏 ─────────────────────────────────────────
        f3 = ttk.Frame(self)
        f3.pack(fill="x", padx=8, pady=4)

        self._btn_gen  = ttk.Button(f3, text="生成",  command=self._generate)
        self._btn_play = ttk.Button(f3, text="播放",  command=self._play, state="disabled")
        self._btn_save = ttk.Button(f3, text="保存",  command=self._save, state="disabled")
        for b in (self._btn_gen, self._btn_play, self._btn_save):
            b.pack(side="left", padx=4)

        self._status = ttk.Label(f3, text="就绪", foreground="gray")
        self._status.pack(side="left", padx=12)

    # ── helpers ───────────────────────────────────────────────────
    def _on_state(self, *_):
        state = self._state_var.get()
        self._listbox.delete(0, "end")
        for t in SAMPLES.get(state, []):
            self._listbox.insert("end", t)
        if self._listbox.size():
            self._listbox.selection_set(0)
            self._on_text_select()

    def _on_text_select(self, *_):
        sel = self._listbox.curselection()
        if sel:
            self._text_box.delete("1.0", "end")
            self._text_box.insert("end", self._listbox.get(sel[0]))

    def _add_voice(self):
        s = simpledialog.askstring("添加音色", "格式: 别名:voice_type\n例: myvoice:BV701_streaming")
        if not s or ":" not in s:
            return
        alias, vtype = s.split(":", 1)
        alias, vtype = alias.strip(), vtype.strip()
        if alias and vtype:
            VOICES[alias] = vtype
            _VOICE_KEYS.append(alias)
            _VOICE_DISPLAYS.append(alias)
            _VOICE_BY_DISP[alias] = alias
            self._cb_voice["values"] = _VOICE_DISPLAYS
            messagebox.showinfo("已添加", f"{alias} → {vtype}")

    def _verify(self):
        self._verify_lbl.config(text="验证中…", foreground="blue")
        app_id = self._cfg_appid.get().strip()
        token  = self._cfg_token.get().strip()
        def _run():
            try:
                asyncio.run(synthesize("好", "BV701_streaming", 1.0, 1.0, 1.0, None,
                                       app_id=app_id, access_token=token))
                self.after(0, lambda: self._verify_lbl.config(text="验证成功 ✓", foreground="green"))
            except Exception as e:
                self.after(0, lambda: self._verify_lbl.config(text=f"验证失败: {e}", foreground="red"))
        threading.Thread(target=_run, daemon=True).start()

    def _set_buttons(self, generating):
        state = "disabled" if generating else "normal"
        self._btn_gen.config(state=state)
        if not generating:
            self._btn_play.config(state="normal" if self._pcm else "disabled")
            self._btn_save.config(state="normal" if self._pcm else "disabled")
        else:
            self._btn_play.config(state="disabled")
            self._btn_save.config(state="disabled")

    def _generate(self):
        text = self._text_box.get("1.0", "end").strip()
        if not text:
            messagebox.showwarning("提示", "请先选择或输入文本")
            return
        voice_key = _VOICE_BY_DISP.get(self._voice_var.get(), self._voice_var.get())
        voice_type = VOICES[voice_key]
        emotion = _EMOTION_BY_DISP.get(self._emo_var.get())
        speed  = round(self._speed.get(), 2)
        pitch  = round(self._pitch.get(), 2)
        volume = round(self._vol.get(),   2)
        rate   = self._rate_var.get()

        self._set_buttons(True)
        self._status.config(text="生成中…", foreground="blue")

        def _run():
            try:
                pcm = asyncio.run(synthesize(text, voice_type, pitch, speed, volume, emotion,
                                             sample_rate=rate))
                self._pcm = pcm
                self._pcm_rate = rate
                self.after(0, lambda: self._status.config(
                    text=f"完成 {len(pcm):,} bytes", foreground="green"))
            except Exception as e:
                self._pcm = None
                self.after(0, lambda: self._status.config(
                    text=f"错误: {e}", foreground="red"))
            finally:
                self.after(0, lambda: self._set_buttons(False))

        threading.Thread(target=_run, daemon=True).start()

    def _play(self):
        if not self._pcm:
            return
        rate = getattr(self, "_pcm_rate", 16000)
        threading.Thread(target=play_pcm, args=(self._pcm, rate), daemon=True).start()

    def _save(self):
        if not self._pcm:
            return
        voice_key = _VOICE_BY_DISP.get(self._voice_var.get(), self._voice_var.get())
        state     = self._state_var.get()
        sel       = self._listbox.curselection()
        idx       = sel[0] + 1 if sel else 1
        speed     = round(self._speed.get(), 2)
        pitch     = round(self._pitch.get(), 2)
        volume    = round(self._vol.get(),   2)
        rate      = getattr(self, "_pcm_rate", 16000)
        default   = f"{voice_key}-{pitch}-{speed}-{volume}-{rate}-{state}-{idx:02d}.pcm"
        path = filedialog.asksaveasfilename(
            defaultextension=".pcm",
            initialdir=ASSETS_DIR,
            initialfile=default,
            filetypes=[("PCM files", "*.pcm"), ("All files", "*.*")],
        )
        if path:
            with open(path, "wb") as f:
                f.write(self._pcm)
            self._status.config(text=f"已保存 {os.path.basename(path)}", foreground="green")


# ── 入口 ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if "--gui" in sys.argv or len(sys.argv) == 1:
        App().mainloop()
    else:
        asyncio.run(main(auto="--auto" in sys.argv))

