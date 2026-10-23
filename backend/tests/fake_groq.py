"""A fake Groq server on 127.0.0.1: answers POST /openai/v1/chat/completions from a script, so the real Groq SDK
and our retry logic are tested against real HTTP errors.

Script steps: "ok" (a normal answer), "429", "500", "401", "hang" (never answers), "drop" (closes the connection)."""

import asyncio
import json


class FakeGroqServer:
    def __init__(self, script: list[str], content: str = '{"explanation": "x", "edits": []}'):
        self.script = list(script)
        self.content = content
        self.requests: list[dict] = []
        self.port = 0
        self._server = None
        self._closing = asyncio.Event()

    async def __aenter__(self):
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self._closing.set()  # lets "hang" connections finish, so closing the server does not wait for them
        self._server.close()
        await asyncio.wait_for(self._server.wait_closed(), timeout=10)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def _handle(self, reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, ConnectionError):
            writer.close()
            return
        lines = head.decode().split("\r\n")
        headers = {k.lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:] if l)}
        body = await reader.readexactly(int(headers.get("content-length", "0")))
        self.requests.append({"path": lines[0].split()[1], "headers": headers, "body": json.loads(body or b"{}")})
        step = self.script.pop(0) if self.script else "ok"
        if step == "drop":
            writer.close()
            return
        if step == "hang":
            await self._closing.wait()
            writer.close()
            return
        if step == "ok":
            status, payload = 200, {
                "id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": "openai/gpt-oss-120b",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": self.content}}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 2, "total_tokens": 42}}
        else:
            status = int(step)
            payload = {"error": {"message": f"fake error {status}", "type": "fake"}}
        data = json.dumps(payload).encode()
        reason = {200: "OK", 401: "Unauthorized", 429: "Too Many Requests", 500: "Internal Server Error"}[status]
        writer.write(f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\n"
                     f"Connection: close\r\n\r\n".encode() + data)
        await writer.drain()
        writer.close()
