import asyncio
from dataclasses import dataclass, field, KW_ONLY
from fastapi import FastAPI, HTTPException, Response
import msgpack
from pydantic import BaseModel
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
    items: List[str] = field(default_factory=list, init=False) # TODO: replace with sqlite

    def __post_init__(self):
        self.app.post(f"/{self.name}/items/")(self.add_item)
        self.app.get(f"/{self.name}/items/{{index}}")(self.get_item)

    async def add_item(self, item: Item):
        self.items.append(item.value)
        return {"message": f"Item added to {self.name}", 
                "item": item.value, 
                "list_length": len(self.items)}

    async def get_item(self, index: int):
        if -len(self.items) <= index < len(self.items):
            payload = {"item": self.items[index], 
                       "index": index, 
                       "list_name": self.name}
            packed = msgpack.packb(payload, use_bin_type=True)
            return Response(content=packed, media_type="application/msgpack")
        raise HTTPException(status_code=404, 
                            detail=f"Index out of range for {self.name}, length: {len(self.items)}")

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

    def add_endpoint(self, name: str, init_items: List[str] = []):
        if name in self.endpoints:
            raise ValueError(f"Endpoint {name} already exists")
        endpoint = ListManagerEndpoint(name=name, app=self.app)
        endpoint.items.extend(init_items)
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