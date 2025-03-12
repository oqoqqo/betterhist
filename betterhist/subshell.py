import asyncio
from dataclasses import dataclass, KW_ONLY, field
import fcntl
import os
import pty
import sys
import termios
import struct
import tty
from typing import Any, Awaitable, Callable, Optional

def set_terminal_size(fd, *, columns, lines):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', lines, columns, 0, 0))

def add_reader_once(loop: asyncio.AbstractEventLoop, fd: int, callback: Callable[[], bool], *args: Any, **kwargs: dict[str, Any]) -> None:
    def wrapped_callback() -> None:
        if callback(*args, **kwargs):
            loop.remove_reader(fd)

    loop.add_reader(fd, wrapped_callback)

async def write_to_fd(fd: int, data: bytes) -> bool:
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, os.write, fd, data)
    except OSError:
        return False
    return True

@dataclass
class Subshell:
    _: KW_ONLY
    stdin_fd : int = field(default_factory=lambda: sys.stdin.fileno())
    shell_command: str = field(default_factory=lambda: os.environ.get('SHELL', '/bin/bash'))
    pid: int = field(init=False)
    master_fd: int = field(init=False)

    def __post_init__(self):
        shell_name = os.path.basename(self.shell_command)
        self.pid, self.master_fd = pty.fork()

        if self.pid == 0:
            os.execlp(self.shell_command, shell_name, "-i")

        self.on_resize()

    def on_resize(self):
        columns, lines = os.get_terminal_size(self.stdin_fd)
        set_terminal_size(self.master_fd, columns=columns, lines=lines)

    async def man_in_the_middle(self, *, on_master_data: Callable[[bytes], Awaitable[bool]], on_stdin_data: Callable[[bytes], Awaitable[bool]]):
        loop = asyncio.get_running_loop()
        master_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()
        stdin_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()

        def ready(fd: int, queue: asyncio.Queue[Optional[bytes]]) -> bool:
            try:
                data = os.read(fd, 1024)
                if data:
                    queue.put_nowait(data)
                    return True
                else:
                    return False
            except OSError:
                queue.put_nowait(None)
                return True

        mode = tty.tcgetattr(self.stdin_fd)
        tty.setraw(self.stdin_fd)

        try:
            master_task, stdin_task = asyncio.create_task(master_queue.get()), asyncio.create_task(stdin_queue.get())
            add_reader_once(loop, self.master_fd, ready, self.master_fd, master_queue)
            add_reader_once(loop, self.stdin_fd, ready, self.stdin_fd, stdin_queue)

            try:
                while (result := os.waitpid(self.pid, os.WNOHANG))[0] == 0:
                    try:
                        await asyncio.wait([master_task, stdin_task], return_when=asyncio.FIRST_COMPLETED, timeout=1)
                    except asyncio.CancelledError:
                        break

                    if stdin_task.done():
                        data = stdin_task.result()
                        if data is None or not await on_stdin_data(data) or not await write_to_fd(self.master_fd, data):
                            return
                        try:
                            add_reader_once(loop, self.stdin_fd, ready, self.stdin_fd, stdin_queue)
                        except OSError:
                            return
                        stdin_task = asyncio.create_task(stdin_queue.get())

                    if master_task.done():
                        data = master_task.result()
                        if data is None or not await on_master_data(data) or not await write_to_fd(self.stdin_fd, data):
                            return
                        try:
                            add_reader_once(loop, self.master_fd, ready, self.master_fd, master_queue)
                        except OSError:
                            return
                        master_task = asyncio.create_task(master_queue.get())
            finally:
                for task in [ master_task, stdin_task ]:
                    task.cancel()
                for task in [ master_task, stdin_task ]:
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                loop.remove_reader(self.master_fd)
                loop.remove_reader(self.stdin_fd)
                tty.tcsetattr(self.stdin_fd, tty.TCSAFLUSH, mode)
                if result[0] == 0:
                    result = await loop.run_in_executor(None, os.waitpid, self.pid, 0)
                return result[1]
        finally:
            tty.tcsetattr(self.stdin_fd, tty.TCSAFLUSH, mode)