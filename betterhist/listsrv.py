import asyncio
from dataclasses import dataclass, field, KW_ONLY
from fastapi import FastAPI, HTTPException, Response
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

@dataclass
class ListManagerEndpoint:
    _: KW_ONLY
    name: str
    app: FastAPI
    db_conn: sqlite3.Connection = field(init=False)
    path: str = field(init=False)

    def __post_init__(self):
        fd, self.path = tempfile.mkstemp(suffix=f"_temp.db")
        try:
            self.db_conn = sqlite3.connect(f"file:{self.path}?mode=rwc", uri=True)
            self.db_conn.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, value BLOB)")
            self.db_conn.commit()
            self.app.post(f"/{self.name}/items/")(self.add_item)
            self.app.get(f"/{self.name}/items/{{index}}")(self.get_item)
        finally:
            os.close(fd)

    def __del__(self):
        if hasattr(self, 'db_conn'):
            self.db_conn.close()
        if hasattr(self, 'path'):
            try:
                os.unlink(self.path)
            except OSError:
                pass

    async def add_item(self, item: Item):
        serialized_value = msgpack.packb(item.value, use_bin_type=True)
        cursor = self.db_conn.cursor()
        cursor.execute("INSERT INTO items (value) VALUES (?)", (serialized_value,))
        self.db_conn.commit()
        list_length = cursor.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        return {"message": f"Item added to {self.name}", 
                "value": item.value,
                "list_length": list_length}

    async def get_item(self, index: int):
        cursor = self.db_conn.cursor()

        if index >= 0:
            cursor.execute("""
                WITH total AS (SELECT COUNT(*) as cnt FROM items)
                SELECT total.cnt, value
                FROM items, total
                ORDER BY id ASC
                LIMIT 1 OFFSET ?
            """, (index,))
        else:
            cursor.execute("""
                WITH total AS (SELECT COUNT(*) as cnt FROM items)
                SELECT total.cnt, value
                FROM items, total
                ORDER BY id DESC
                LIMIT 1 OFFSET ?
            """, (-index - 1,))

        result = cursor.fetchone()
        if result:
            list_length, serialized_value = result
            if serialized_value is not None and (0 <= index < list_length or index < 0):
                payload = {
                    "value": msgpack.unpackb(serialized_value, raw=False),
                    "index": index,
                    "list_name": self.name
                }
                packed = msgpack.packb(payload, use_bin_type=True)
                return Response(content=packed, media_type="application/msgpack")

        list_length = cursor.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        raise HTTPException(status_code=404, 
                           detail=f"Index out of range for {self.name}, length: {list_length}")

async def lifespan(app):
    try:
        yield
    except asyncio.CancelledError:
        pass  # Suppress the error on shutdown

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