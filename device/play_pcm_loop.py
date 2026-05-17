import os
import time
from machine import I2S, Pin

# ── 扬声器 MAX98357A（I2S，闹钟版）───────────────────────────
_LRC      = 9   # I2S WS
_BCLK     = 8   # I2S SCK
_DIN      = 7   # I2S SD
_GAIN_PIN = 6   # 增益控制（低电平=15dB）
_SD_PIN   = 5   # 高电平=工作，低电平=关断

_PCM_DIR     = '/assets'
_SAMPLE_RATE = 8000
_BUF_SIZE    = 4096
_GAP_MS      = 300   # 两条音频间隔


def _list_pcm():
    names = sorted(f for f in os.listdir(_PCM_DIR) if f.endswith('.pcm'))
    return [_PCM_DIR + '/' + n for n in names]


def _play(audio, path):
    buf = bytearray(_BUF_SIZE)
    mv  = memoryview(buf)
    print('▶', path)
    with open(path, 'rb') as f:
        while True:
            n = f.readinto(buf)
            if not n:
                break
            audio.write(mv[:n])


def main():
    Pin(_SD_PIN,   Pin.OUT, value=1)   # 使能放大器
    Pin(_GAIN_PIN, Pin.OUT, value=0)   # 15 dB 增益

    audio = I2S(
        1,
        sck=Pin(_BCLK),
        ws=Pin(_LRC),
        sd=Pin(_DIN),
        mode=I2S.TX,
        bits=16,
        format=I2S.MONO,
        rate=_SAMPLE_RATE,
        ibuf=_BUF_SIZE * 2,
    )

    files = _list_pcm()
    print(f'找到 {len(files)} 个 PCM 文件，开始轮播…')

    idx = 0
    try:
        while True:
            _play(audio, files[idx])
            idx = (idx + 1) % len(files)
            time.sleep_ms(_GAP_MS)
    except KeyboardInterrupt:
        print('已停止')
    finally:
        audio.deinit()
        Pin(_SD_PIN, Pin.OUT, value=0)  # 关断放大器


main()
