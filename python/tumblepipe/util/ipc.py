from contextlib import suppress
from socket import socket
import asyncio

def free_port() -> int:
    with socket() as s:
        s.bind(('localhost', 0))
        return s.getsockname()[1]

class Server:
    def __init__(self, host, port, handler):
        self._host = host
        self._port = port
        self._handler = handler
        self._server = None
        self._process = None
    
    async def _on_message(self, reader, writer):
        message = await reader.read(1024**3)
        response = await self._handler(message.decode())
        writer.write(response.encode())
        await writer.drain()
        writer.close()
        await writer.wait_closed()
    
    async def _serve(self):
        async with self._server:
            await self._server.serve_forever()
    
    async def __aenter__(self):
        self._server = await asyncio.start_server(self._on_message, self._host, self._port)
        self._process = asyncio.create_task(self._serve())
    
    async def __aexit__(self, exc_type, exc, tb):
        self._process.cancel()
        with suppress(asyncio.CancelledError):
            await self._process
        self._server = None
        self._process = None

class Client:
    def __init__(self, host, port):
        self._host = host
        self._port = port
        self._reader = None
        self._writer = None

    async def __aenter__(self):
        self._reader, self._writer = await asyncio.open_connection(self._host, self._port)
        return self
    
    async def __aexit__(self, exc_type, exc, tb):
        self._writer.close()
        await self._writer.wait_closed()
        self._reader = None
        self._writer = None
    
    async def send(self, message):
        self._writer.write(message.encode())
        await self._writer.drain()
    
    async def receive(self):
        message = await self._reader.read(1024**3)
        return message.decode()