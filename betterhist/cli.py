import asyncio
from betterhist.listsrv import ListManagerServer
from betterhist.subshell import Subshell
from betterhist.termsplit import TermSplit
from betterhist.views import pyte_view, markdown_format
import msgpack
import os
import requests
import signal
import typer

app = typer.Typer(invoke_without_command=True)

# TODO: make history server

@app.callback(invoke_without_command=True)
def default(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        return asyncio.run(subshell())

@app.command(
    context_settings={"ignore_unknown_options": True}
)
def get(index: int):
    response = requests.get(f"{os.environ['BETTERHIST_SERVER']}/history/items/{index}")
    response.raise_for_status()
    item = msgpack.unpackb(response.content, raw=False)["item"]
    user_view, command_view = pyte_view(user_buffer=item["user_buffer"], command_buffer=item["command_buffer"], columns=item["columns"], lines=item["lines"])
    print(markdown_format(user_view=user_view, command_view=command_view))
    return 0

@app.command()
async def subshell():
    server = os.environ.get("BETTERHIST_SERVER", None)
    if server is not None:
        return get(-1)
    else:
        listsrv = ListManagerServer()
        listsrv.add_endpoint("history")
        await listsrv.start()
        os.environ["BETTERHIST_SERVER"] = f"http://127.0.0.1:{listsrv.assigned_port}"

        subshell = Subshell()
        termsplit = TermSplit(pid=subshell.pid, master_fd=subshell.master_fd)

        async def async_on_master_data(data: bytes) -> bool:
            return termsplit.on_master_data(data)

        async def async_on_stdin_data(data: bytes) -> bool:
            return termsplit.on_stdin_data(data)

        def process_user_command_tuple(user_buffer, command_buffer):
            import time
            timestamp = time.time()
            columns, lines = os.get_terminal_size(subshell.stdin_fd)
            listsrv.endpoints["history"].items.append({ "timestamp": timestamp, "columns": columns, "lines": lines, "user_buffer": user_buffer, "command_buffer": command_buffer })

        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGWINCH, subshell.on_resize)

        middle_task = asyncio.create_task(subshell.man_in_the_middle(on_master_data=async_on_master_data, on_stdin_data=async_on_stdin_data))
        dequeue_task = asyncio.create_task(termsplit.get())

        while not middle_task.done():
            await asyncio.wait([middle_task, dequeue_task], return_when=asyncio.FIRST_COMPLETED, timeout=0.1)
            if dequeue_task.done():
                user_buffer, command_buffer = dequeue_task.result()
                process_user_command_tuple(user_buffer, command_buffer)
                dequeue_task = asyncio.create_task(termsplit.get())
            elif middle_task.done():
                while True:
                    try:
                        user_buffer, command_buffer = await asyncio.wait_for(termsplit.get(), timeout=0)
                        process_user_command_tuple(user_buffer, command_buffer)
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