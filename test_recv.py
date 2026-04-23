import bluetooth
import aioble
import asyncio
from config import BLE_NAME, NUS_SERVICE, NUS_RX, NUS_TX

_svc = aioble.Service(bluetooth.UUID(NUS_SERVICE))
_rx = aioble.Characteristic(_svc, bluetooth.UUID(NUS_RX), write=True, write_no_response=True, capture=True)
_tx = aioble.Characteristic(_svc, bluetooth.UUID(NUS_TX), notify=True)
aioble.register_services(_svc)

async def main():
    buf = b""
    while True:
        print("[test] advertising...")
        conn = await aioble.advertise(250_000, name=BLE_NAME, services=[bluetooth.UUID(NUS_SERVICE)])
        print(f"[test] connected: {conn}")
        buf = b""
        while conn.is_connected():
            try:
                _, data = await asyncio.wait_for(_rx.written(timeout_ms=None), timeout=60)
                print(f"[test] chunk ({len(data)}B): {data}")
                buf += data
                if b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    print(f"[test] LINE: {line.decode().strip()}")
            except asyncio.TimeoutError:
                print("[test] timeout, reconnecting")
                break

asyncio.run(main())
