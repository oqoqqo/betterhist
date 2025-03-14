import aiosqlite
import asyncio
from dataclasses import dataclass, field, KW_ONLY
from fastapi import Depends, FastAPI, Header, HTTPException, Response
import msgpack
import os
from pydantic import BaseModel
import sqlite3
import tempfile
from typing import Any, Dict, List, Optional
import uvicorn

# Request body model
class Item(BaseModel):
    value: Any

# Authentication dependency
async def verify_auth(x_betterhist_auth: str = Header(None)):
    auth_token = os.environ.get("BETTERHIST_AUTH")
    if not auth_token or x_betterhist_auth != auth_token:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing authentication token"
        )
    return True

@dataclass
class ListManagerEndpoint:
    _: KW_ONLY
    name: str
    app: FastAPI
    db_conn: aiosqlite.Connection = field(init=False)

    async def startup(self):
        self.db_conn = await aiosqlite.connect(":memory:")
        await self.db_conn.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, value BLOB)")
        await self.db_conn.commit()
        self.app.post(f"/{self.name}/items/")(self.add_item)
        self.app.get(f"/{self.name}/items/{{index}}")(self.get_item)

    async def shutdown(self):
        if hasattr(self, 'db_conn'):
            await self.db_conn.close()

    async def add_item(self, item: Item, authenticated: bool = Depends(verify_auth)):
        serialized_value = msgpack.packb(item.value, use_bin_type=True)
        async with self.db_conn.cursor() as cursor:
            await cursor.execute("INSERT INTO items (value) VALUES (?)", (serialized_value,))
            await self.db_conn.commit()
            await cursor.execute("SELECT COUNT(*) FROM items")
            list_length = (await cursor.fetchone())[0]
        return {"message": f"Item added to {self.name}", 
                "value": item.value,
                "list_length": list_length}

    async def get_item(self, index: int, authenticated: bool = Depends(verify_auth)):
        async with self.db_conn.cursor() as cursor:
            if index >= 0:
                await cursor.execute("""
                    SELECT value
                    FROM items
                    ORDER BY id ASC
                    LIMIT 1 OFFSET ?
                """, (index,))
            else:
                await cursor.execute("""
                    SELECT value
                    FROM items
                    ORDER BY id DESC
                    LIMIT 1 OFFSET ?
                """, (-index - 1,))

            result = await cursor.fetchone()

        if result and result[0] is not None:
            payload = {
                "value": msgpack.unpackb(result[0], raw=False),
                "index": index,
                "list_name": self.name
            }
            packed = msgpack.packb(payload, use_bin_type=True)
            return Response(content=packed, media_type="application/msgpack")

        async with self.db_conn.cursor() as cursor:
            await cursor.execute("SELECT COUNT(*) FROM items")
            list_length = (await cursor.fetchone())[0]
            raise HTTPException(status_code=404, detail=f"Index out of range for {self.name}, length: {list_length}")

async def lifespan(app):
    try:
        yield
    except asyncio.CancelledError:
        pass

@dataclass
class ListManagerServer:
    _: KW_ONLY
    app: FastAPI = field(default_factory=lambda: FastAPI(title="ListManagerServer", lifespan=lifespan))
    endpoints: Dict[str, ListManagerEndpoint] = field(default_factory=dict, init=False)
    assigned_port: Optional[int] = field(default=None, init=False)
    server_task: Optional[asyncio.Task[None]] = field(default=None, init=False)

    async def add_endpoint(self, name: str, init_items: List[str] = []):
        if name in self.endpoints:
            raise ValueError(f"Endpoint {name} already exists")
        endpoint = ListManagerEndpoint(name=name, app=self.app)
        await endpoint.startup()
        for item in init_items:
            await endpoint.add_item(item)
        self.endpoints[name] = endpoint
        return endpoint

    async def start(self):
        class PortCaptureServer(uvicorn.Server):
            def __init__(obj, config, server_instance):
                super().__init__(config)
                obj.server_instance = server_instance
                obj.ready_event = asyncio.Event()

            async def startup(obj, sockets=None):
                await super().startup(sockets=sockets)
                obj.server_instance.assigned_port = obj.servers[0].sockets[0].getsockname()[1]
                obj.ready_event.set()

            async def shutdown(obj):
                await super().shutdown()
                obj.server_instance.server_task = None
                obj.server_instance.assigned_port = None

        config = uvicorn.Config(app=self.app, host="127.0.0.1", port=0, log_level="error")
        server = PortCaptureServer(config, self)
        self.server_task = asyncio.create_task(server.serve())
        await server.ready_event.wait()
        
    async def shutdown(self):
        if self.server_task and not self.server_task.done():
            self.server_task.cancel()
            try:
                await self.server_task
            except asyncio.CancelledError:
                pass
            finally:
                self.server_task = None
                self.assigned_port = None

        await asyncio.gather(*[endpoint.shutdown() for endpoint in self.endpoints.values()])
