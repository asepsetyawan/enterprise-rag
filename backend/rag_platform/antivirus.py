import asyncio
import struct

from .config import settings
from .storage import file_path


async def scan(relative):
    """ClamAV INSTREAM. Scanner errors fail ingestion rather than allowing publication."""
    cfg = settings()
    if not cfg.antivirus_host:
        if cfg.app_env == "production":
            raise RuntimeError("Antivirus required")
        return
    async with asyncio.timeout(120):
        reader, writer = await asyncio.open_connection(cfg.antivirus_host, cfg.antivirus_port)
        try:
            writer.write(b"zINSTREAM\0")
            with file_path(relative).open("rb") as source:
                while chunk := source.read(64 * 1024):
                    writer.write(struct.pack("!I", len(chunk)) + chunk)
                    await writer.drain()
            writer.write(struct.pack("!I", 0))
            await writer.drain()
            result = await reader.readuntil(b"\0")
            if result != b"stream: OK\0":
                raise ValueError("Document rejected by antivirus")
        finally:
            writer.close()
            await writer.wait_closed()
