import asyncio
from dataclasses import dataclass, field, KW_ONLY
from enum import IntEnum
import fcntl
import struct
import termios
from typing import Optional

# TODO: screws up multiline input on the shell
#       using shell-integration escape sequences could mitigate this

class State(IntEnum):
    WAIT_FOR_USER = 0,
    WAIT_FOR_COMMAND = 1,

@dataclass
class TermSplit:
    _: KW_ONLY
    pid: int
    master_fd: int
    state: State = field(default = State.WAIT_FOR_USER, init=False)
    buffer: list[bytes] = field(default_factory=list, init=False)
    queue: asyncio.Queue[tuple[bytes, bytes]] = field(default_factory=asyncio.Queue, init=False)
    wait_for_user_buffer: Optional[bytes] = field(default=None, init=False)

    def _get_foreground_pid(self) -> Optional[int]:
        buf = struct.pack("i", 0)
        result = fcntl.ioctl(self.master_fd, termios.TIOCGPGRP, buf)
        foreground_pgid = struct.unpack("i", result)[0]
        return foreground_pgid

    def _transition_command_to_user(self):
        if self.state == State.WAIT_FOR_COMMAND:
            self.state = State.WAIT_FOR_USER
            wait_for_command_buffer = b''.join(self.buffer)
            self.buffer.clear()
            self.queue.put_nowait((self.wait_for_user_buffer, wait_for_command_buffer))
            self.wait_for_user_buffer = None

    def _edge_trigger_command_to_user(self):
        if self.state == State.WAIT_FOR_COMMAND:
            foreground_pid = self._get_foreground_pid()
            if foreground_pid == self.pid:
                self._transition_command_to_user()

    def _transition_user_to_command(self):
        if self.state == State.WAIT_FOR_USER:
            self.state = State.WAIT_FOR_COMMAND
            self.wait_for_user_buffer = b''.join(self.buffer)
            self.buffer.clear()

    def _edge_trigger_user_to_command(self):
        if self.state == State.WAIT_FOR_USER:
            foreground_pid = self._get_foreground_pid()
            if foreground_pid != self.pid:
                self._transition_user_to_command()

    def on_master_data(self, data: bytes) -> bool:
        self.buffer.append(data)
        if self.state == State.WAIT_FOR_COMMAND:
            self._edge_trigger_user_to_command()

        return True

    def on_stdin_data(self, data: bytes) -> bool:
        if self.state == State.WAIT_FOR_COMMAND:
            self._edge_trigger_command_to_user()

        if self.state == State.WAIT_FOR_USER:
            if b'\r' in data:
                self._transition_user_to_command()
            else:
                self._edge_trigger_user_to_command()

        return True

    def on_idle(self):
        if self.state == State.WAIT_FOR_COMMAND:
            self._edge_trigger_command_to_user()

    async def get(self) -> tuple[bytes, bytes]:
        return await self.queue.get()