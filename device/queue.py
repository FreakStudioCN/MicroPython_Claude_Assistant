try:
    from ucollections import deque
except ImportError:
    from collections import deque

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio


class Queue:
    def __init__(self, maxlen=32):
        self._items = deque((), maxlen)
        self._event = asyncio.Event()

    def put_nowait(self, item):
        self._items.append(item)
        self._event.set()

    async def get(self):
        while not self._items:
            self._event.clear()
            await self._event.wait()
        return self._items.popleft()
