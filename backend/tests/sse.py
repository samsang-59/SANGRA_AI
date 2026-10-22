"""A streaming test client for the live stream. It calls the ASGI app directly and hands over the body
chunk by chunk as it arrives (httpx's in-process transport waits for the whole response, so it cannot read
a stream that only ends when the run ends). disconnect() behaves like a browser closing the tab."""

import asyncio
import json


def parse_sse(text: str) -> tuple[list[dict], int]:
    """Splits SSE text into events. Returns (events, number of ping comments)."""
    events, pings = [], 0
    for block in text.replace("\r\n", "\n").split("\n\n"):
        if not block.strip():
            continue
        fields: dict[str, str] = {}
        for line in block.split("\n"):
            if line.startswith(":"):
                pings += 1
                continue
            key, _, value = line.partition(": ")
            fields[key] = value
        if "data" in fields:
            data = json.loads(fields["data"])
            events.append({"id": int(fields["id"]), "event": fields["event"], "data": data})
    return events, pings


class AsgiStream:
    def __init__(self, app, path: str, headers: dict[str, str]):
        self.app = app
        self.path = path
        self.headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
        self.status: int | None = None
        self.response_headers: dict[str, str] = {}
        self.chunks: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.text = ""
        self.closed_by_server = False
        self._disconnected = asyncio.Event()
        self._started = asyncio.Event()
        self.task: asyncio.Task | None = None

    async def __aenter__(self) -> "AsgiStream":
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
                 "method": "GET", "scheme": "http", "path": self.path, "raw_path": self.path.encode(),
                 "query_string": b"", "root_path": "", "headers": self.headers, "client": ("127.0.0.1", 5000),
                 "server": ("test", 80)}
        sent_request = False

        async def receive():
            nonlocal sent_request
            if not sent_request:
                sent_request = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await self._disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                self.status = message["status"]
                self.response_headers = {k.decode(): v.decode() for k, v in message.get("headers", [])}
                self._started.set()
            elif message["type"] == "http.response.body":
                if message.get("body"):
                    await self.chunks.put(message["body"])
                if not message.get("more_body", False):
                    self.closed_by_server = True
                    await self.chunks.put(None)

        self.task = asyncio.create_task(self.app(scope, receive, send))
        await asyncio.wait_for(self._started.wait(), timeout=10)
        return self

    async def read_until_closed(self, timeout: float = 10.0) -> str:
        async def collect():
            while True:
                chunk = await self.chunks.get()
                if chunk is None:
                    return
                self.text += chunk.decode()

        await asyncio.wait_for(collect(), timeout=timeout)
        return self.text

    async def read_for(self, seconds: float) -> str:
        deadline = asyncio.get_running_loop().time() + seconds
        while (left := deadline - asyncio.get_running_loop().time()) > 0:
            try:
                chunk = await asyncio.wait_for(self.chunks.get(), timeout=left)
            except TimeoutError:
                break
            if chunk is None:
                break
            self.text += chunk.decode()
        return self.text

    async def read_events(self, count: int, timeout: float = 10.0) -> list[dict]:
        async def collect():
            while len(parse_sse(self.text)[0]) < count:
                chunk = await self.chunks.get()
                if chunk is None:
                    return
                self.text += chunk.decode()

        await asyncio.wait_for(collect(), timeout=timeout)
        return parse_sse(self.text)[0]

    async def disconnect(self) -> None:
        self._disconnected.set()

    async def __aexit__(self, *exc) -> None:
        self._disconnected.set()
        if self.task:
            try:
                await asyncio.wait_for(self.task, timeout=10)
            except (TimeoutError, asyncio.CancelledError):
                self.task.cancel()
