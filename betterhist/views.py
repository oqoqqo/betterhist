def pyte_view_one(data: bytes, *, columns: int, lines: int):
    import pyte
    screen = pyte.Screen(columns=columns, lines=lines)
    stream = pyte.Stream(screen)
    stream.feed(data.decode(errors="ignore"))
    return [ line for raw in screen.display for line in (raw.rstrip(),) if line ]

def pyte_view(*, user_buffer: bytes, command_buffer: bytes, columns: int, lines: int):
    user_view = pyte_view_one(user_buffer, columns=columns, lines=lines)
    command_view = pyte_view_one(command_buffer, columns=columns, lines=lines)
    return user_view, command_view

def markdown_format(*, user_view: list[str], command_view: list[str]):
    nice_user_view = '\n'.join(user_view)
    nice_command_view = '\n'.join(command_view)
    return f"""
```shell
{nice_user_view}
{nice_command_view}
```
""".strip()