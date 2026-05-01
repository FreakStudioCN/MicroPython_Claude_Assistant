#!/usr/bin/env python3
# PC 端：扫描连接 ESP32，发一条测试消息，打印收到的回复
import asyncio, json, time
from bleak import BleakClient, BleakScanner

NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

def ts():
    return time.strftime("%H:%M:%S")

async def main():
    print(f"[{ts()}] scanning...")
    devices = await BleakScanner.discover(timeout=5.0)
    dev = next((d for d in devices if d.name and "Claude" in d.name), None)
    if not dev:
        print("device not found"); return

    print(f"[{ts()}] connecting to {dev.address}...")
    async with BleakClient(dev.address) as client:
        print(f"[{ts()}] connected")

        def on_notify(_, data):
            print(f"[{ts()}] ESP32 -> PC: {data.decode(errors='ignore').strip()}")

        await client.start_notify(NUS_TX, on_notify)
        await asyncio.sleep(1.0)  # 等 ESP32 进入 recv_line()

        msg = json.dumps({"waiting": 1, "msg": "test", "prompt": {"id": "t1", "tool": "Bash", "hint": "echo hello"}}) + "\n"
        data = msg.encode()
        print(f"[{ts()}] PC -> ESP32: {msg.strip()} ({len(data)} bytes)")
        for i in range(0, len(data), 20):
            await client.write_gatt_char(NUS_RX, data[i:i+20], response=False)
            await asyncio.sleep(0.02)

        print(f"[{ts()}] waiting 10s for ESP32 response...")
        await asyncio.sleep(10)

asyncio.run(main())
