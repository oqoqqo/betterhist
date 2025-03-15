# betterhist
Better shell history.
* Maintain a history of command-output pairs. Uses a heuristic that works in most cases (with one notable exception: the output of shell built-ins currently looks like "more user input").  
* Supports the output of command-output pairs as a Markdown block.  
* Supports simple text search in the history.  

# Quickstart

First you run an instance of `bh`, which forks a subshell in a pty and monitors everything to build the history.  This is the default command if you’re not yet a child process of `bh`.  Once you are a child process of a `bh`, the default command is &ldquo;Print the last part of my shell history as a markdown block.&rdquo;  

Example:
<div class="highlight highlight-source-shell">
<pre>(bh) oqoqqo@oqoqqo:~/src/betterhist$ bh
(base) oqoqqo@oqoqqo:~/src/betterhist$ conda activate bh
(bh) oqoqqo@oqoqqo:~/src/betterhist$ bh
```shell
(base) oqoqqo@oqoqqo:~/src/betterhist$ conda activate bh
```
</pre>
</div>
This is a bit confusing to read, but what happens is:

* `bh` is called at the beginning, which created a subshell.  
* A command was executed (`conda activate bh`).
* `bh` printed the last command and output as a markdown formatted block; the command was `conda activate bh` which had no output.

## Inspiration: [wave terminal](https://github.com/wavetermdev/waveterm) integration
`bh` makes it easy to send shell activity to the `wsh ai` widget.
```shell
(bh) oqoqqo@oqoqqo:~/src/betterhist$ wsh ai "how can i tell if the code executable is vscode or cursor"
(bh) oqoqqo@oqoqqo:~/src/betterhist$ which code
/mnt/c/Users/oqoqq/AppData/Local/Programs/cursor/resources/app/bin/code
(bh) oqoqqo@oqoqqo:~/src/betterhist$ which cursor
/mnt/c/Users/oqoqq/AppData/Local/Programs/cursor/resources/app/bin/cursor
(bh) oqoqqo@oqoqqo:~/src/betterhist$ bh search which
7 bh search which 
6 which cursor /mnt/c/Users/oqoqq/AppData/Local/Programs/curs...
5 which code /mnt/c/Users/oqoqq/AppData/Local/Programs/curs...
(bh) oqoqqo@oqoqqo:~/src/betterhist$ (bh get 5; bh get 6) | wsh ai -
```
