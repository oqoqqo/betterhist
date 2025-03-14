import asyncio
from betterhist.listsrv import ListManagerServer, Snapshot
from betterhist.subshell import Subshell
from betterhist.termsplit import TermSplit
from betterhist.views import pyte_view
from functools import wraps
import os
import pyte
import requests
import signal
import typer
import uuid

### BEGIN monkeypatch pyte to ignore the private argument
original_select_graphic_rendition = pyte.screens.Screen.select_graphic_rendition

@wraps(original_select_graphic_rendition)
def patched_select_graphic_rendition(self, *args, **kwargs):
    if 'private' in kwargs:
        kwargs.pop('private')
    return original_select_graphic_rendition(self, *args, **kwargs)

pyte.screens.Screen.select_graphic_rendition = patched_select_graphic_rendition
### END monkeypatch pyte

app = typer.Typer(invoke_without_command=True)

@app.callback(invoke_without_command=True)
def default(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        return asyncio.run(subshell())

@app.command(
    context_settings={"ignore_unknown_options": True}
)
def get(index: int):
    if os.environ.get("BETTERHIST_SERVER", None) is None:
        raise ValueError("BETTERHIST_SERVER is not set, you need to run bh subshell first")
    auth_token = os.environ.get("BETTERHIST_AUTH")
    if auth_token is None:
        raise ValueError("BETTERHIST_AUTH is not set, you need to run bh subshell first")
    response = requests.get(f"{os.environ['BETTERHIST_SERVER']}/history/items/{index}", headers={"X-Betterhist-Auth": auth_token})
    response.raise_for_status()
    snapshot = Snapshot.model_validate(response.json()["snapshot"])
    markdown = f"""
```shell
{snapshot.user_view}
{snapshot.command_view}
```
    """.strip()
    print(markdown)
    return 0

@app.command()
async def subshell():
    server = os.environ.get("BETTERHIST_SERVER", None)
    if server is not None:
        return get(-1)
    else:
        loop = asyncio.get_running_loop()

        listsrv = ListManagerServer()
        await listsrv.start()
        os.environ["BETTERHIST_SERVER"] = f"http://127.0.0.1:{listsrv.assigned_port}"
        os.environ["BETTERHIST_AUTH"] = uuid.uuid4().hex
        await listsrv.add_endpoint("history")

        subshell = Subshell()
        termsplit = TermSplit(pid=subshell.pid, master_fd=subshell.master_fd)

        async def async_on_master_data(data: bytes) -> bool:
            return termsplit.on_master_data(data)

        async def async_on_stdin_data(data: bytes) -> bool:
            return termsplit.on_stdin_data(data)

        async def process_user_command_tuple(user_buffer, command_buffer):
            import time
            timestamp = time.time()
            columns, lines = os.get_terminal_size(subshell.stdin_fd)
            user_view, command_view = await loop.run_in_executor(None, lambda: pyte_view(user_buffer=user_buffer, command_buffer=command_buffer, columns=columns, lines=lines))
            snapshot = Snapshot(timestamp=timestamp, columns=columns, lines=lines, user_view=user_view, command_view=command_view)
            await listsrv.endpoints["history"].add_item(snapshot)

        loop.add_signal_handler(signal.SIGWINCH, subshell.on_resize)
        middle_task = asyncio.create_task(subshell.man_in_the_middle(on_master_data=async_on_master_data, on_stdin_data=async_on_stdin_data))
        dequeue_task = asyncio.create_task(termsplit.get())

        while not middle_task.done():
            await asyncio.wait([middle_task, dequeue_task], return_when=asyncio.FIRST_COMPLETED, timeout=0.1)
            if dequeue_task.done():
                user_buffer, command_buffer = dequeue_task.result()
                await process_user_command_tuple(user_buffer, command_buffer)
                dequeue_task = asyncio.create_task(termsplit.get())
            elif middle_task.done():
                while True:
                    try:
                        user_buffer, command_buffer = await asyncio.wait_for(termsplit.get(), timeout=0)
                        await process_user_command_tuple(user_buffer, command_buffer)
                    except asyncio.TimeoutError:
                        break
                status = middle_task.result()
            else:
                termsplit.on_idle()

        await listsrv.shutdown()
        try:
            await dequeue_task.cancel()
        except:
            pass

        return status

def main():
    app()

if __name__ == '__main__':
    main()