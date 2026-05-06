try:
    import uasyncio as asyncio
except ImportError:
    import asyncio


class Queue:
    def __init__(self):
        self._items = []
        self._event = asyncio.Event()

    def put_nowait(self, item):
        self._items.append(item)
        self._event.set()

    async def get(self):
        while not self._items:
            self._event.clear()
            await self._event.wait()
        return self._items.pop(0)
