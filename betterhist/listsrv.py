import aiosqlite
import asyncio
from dataclasses import dataclass, field, KW_ONLY
from enum import Enum
from fastapi import Depends, FastAPI, Header, HTTPException, Response, Query
import json
import os
from pydantic import BaseModel
from typing import Dict, List, Optional
import uvicorn

class Snapshot(BaseModel):
    timestamp: float
    columns: int
    lines: int
    user_view: str
    command_view: str

async def verify_auth(x_betterhist_auth: str = Header(None)):
    auth_token = os.environ.get("BETTERHIST_AUTH")
    if not auth_token or x_betterhist_auth != auth_token:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing authentication token"
        )
    return True

class SearchLocation(str, Enum):
    USER_VIEW = "user_view"
    COMMAND_VIEW = "command_view"
    BOTH = "both"

@dataclass
class ListManagerEndpoint:
    _: KW_ONLY
    name: str
    app: FastAPI
    db_conn: aiosqlite.Connection = field(init=False)

    async def startup(self):
        self.db_conn = await aiosqlite.connect(":memory:")
        await self.db_conn.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, timestamp REAL, columns INTEGER, lines INTEGER, user_view TEXT, command_view TEXT)")
        await self.db_conn.commit()
        self.app.post(f"/{self.name}/items/")(self.add_item)
        self.app.get(f"/{self.name}/items/{{index}}")(self.get_item)
        self.app.get(f"/{self.name}/search/")(self.search_items)

    async def shutdown(self):
        if hasattr(self, 'db_conn'):
            await self.db_conn.close()

    async def add_item(self, snapshot: Snapshot, authenticated: bool = Depends(verify_auth)):
        async with self.db_conn.cursor() as cursor:
            await cursor.execute("INSERT INTO items (timestamp, columns, lines, user_view, command_view) VALUES (?, ?, ?, ?, ?)",
                (snapshot.timestamp, snapshot.columns, snapshot.lines, snapshot.user_view, snapshot.command_view))
            await self.db_conn.commit()
            await cursor.execute("SELECT COUNT(*) FROM items")
            list_length = (await cursor.fetchone())[0]
        return {"message": f"Item added to {self.name}", "list_length": list_length}

    async def get_item(self, index: int, authenticated: bool = Depends(verify_auth)):
        async with self.db_conn.cursor() as cursor:
            if index >= 0:
                await cursor.execute("""
                    SELECT timestamp, columns, lines, user_view, command_view
                    FROM items
                    ORDER BY id ASC
                    LIMIT 1 OFFSET ?
                """, (index,))
            else:
                await cursor.execute("""
                    SELECT timestamp, columns, lines, user_view, command_view
                    FROM items
                    ORDER BY id DESC
                    LIMIT 1 OFFSET ?
                """, (-index - 1,))

            result = await cursor.fetchone()

        if result and result[0] is not None:
            snapshot = Snapshot(
                timestamp=result[0],
                columns=result[1],
                lines=result[2],
                user_view=result[3],
                command_view=result[4]
            )
            return { "snapshot": snapshot.model_dump(), "index": index, "list_name": self.name }

        async with self.db_conn.cursor() as cursor:
            await cursor.execute("SELECT COUNT(*) FROM items")
            list_length = (await cursor.fetchone())[0]
            raise HTTPException(status_code=404, detail=f"Index out of range for {self.name}, length: {list_length}")

    async def search_items(self,
                           pattern: str = Query(..., description="Text pattern to search for"),
                           search_in: SearchLocation = Query(SearchLocation.BOTH, description="Where to search: 'user_view', 'command_view', or 'both'"),
                           limit: int = Query(10, description="Maximum number of results to return"),
                           authenticated: bool = Depends(verify_auth)):
        search_pattern = f"%{pattern}%"

        if search_in == SearchLocation.USER_VIEW:
            where = "user_view LIKE ?"
            params = (search_pattern,)
        elif search_in == SearchLocation.COMMAND_VIEW:
            where = "command_view LIKE ?"
            params = (search_pattern,)
        else:  # Default to both
            where = "(user_view LIKE ? OR command_view LIKE ?)"
            params = (search_pattern, search_pattern)

        query = f"""
            SELECT id, timestamp, columns, lines, user_view, command_view
            FROM items
            WHERE {where}
            ORDER BY id DESC
            LIMIT ?
        """
        params = (*params, limit)

        async with self.db_conn.cursor() as cursor:
            await cursor.execute(query, params)
            results = await cursor.fetchall()

        if not results:
            return {"message": "No matching items found", "results": []}

        # Format the results
        formatted_results = []
        for row in results:
            id_val, timestamp, columns, lines, user_view, command_view = row
            snapshot = Snapshot(
                timestamp=timestamp,
                columns=columns,
                lines=lines,
                user_view=user_view,
                command_view=command_view
            )
            formatted_results.append({
                "id": id_val,
                "snapshot": snapshot.model_dump()
            })

        return {
            "message": f"Found {len(formatted_results)} matching items",
            "list_name": self.name,
            "results": formatted_results
        }

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