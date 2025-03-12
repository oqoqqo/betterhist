import asyncio
from betterhist.views import pyte_view, bot_format
from betterhist.subshell import Subshell
from betterhist.termsplit import TermSplit
import os
import signal
import typer

app = typer.Typer(invoke_without_command=True)

# TODO: make history server

history = [ (0, ['hello'], ['world']) ]

@app.callback(invoke_without_command=True)
def default(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        return asyncio.run(subshell())

@app.command()
async def get(index: int):
    _, user_view, command_view = history[index]
    print(bot_format(user_view, command_view))

@app.command()
async def subshell():
    server = os.environ.get("BETTERHIST_SERVER", None)
    if server is not None:
        return await get(-1)
    else:
        os.environ["BETTERHIST_SERVER"] = "something"
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
            user_view, command_view = pyte_view(user_buffer, command_buffer, columns=columns, lines=lines)
            history.append((timestamp, user_view, command_view))

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

        for i, (timestamp, user_view, command_view) in enumerate(history):
            import time
            formatted_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))
            print(f'-------------- user {i} ({formatted_time}) ---------------')
            for line in user_view:
                print(line)
            print(f'-------------- end user {i} ---------------')
            print(f'-------------- command {i} ---------------')
            for line in command_view:
                print(line)
            print(f'-------------- end command {i} ---------------')

        return status

def main():
    app()

if __name__ == '__main__':
    main()