Read one file from this workspace, by its exact path.

You cannot browse this workspace. There is no `ls`, no `glob` and no `grep`,
and that is deliberate: exploring a project is the coding agent's job and every
listing here would cost a model call out of a daily budget. Every path you can
read reaches you already:

- **The research wiki.** `research_status` lists everything under
  `/{research_dir}/`, and `index.md` says what each page holds. Call it when you
  need a path you do not have.
- **Project files the request named.** A brief that wants you to read the code
  is expected to give the paths. If you need a file nobody named, say so in your
  findings rather than guessing at a path.

Usage:
- Paths are absolute within this workspace: `/{research_dir}/pricing.md`,
  `/llm_router/config.yaml`. There is no filesystem above `/`.
- Reads the WHOLE file by default, which is what you normally want: one call,
  and you have the page. `offset` and `limit` take a window out of a file too
  long to hold; `offset` is 0-based, so the line printed as 148 is `offset=147`.
- Output is `cat -n` style, one line number per source line.
- You must read a file before `edit_file` will change it.
- Re-reading a file you already read costs a request and tells you nothing new.
  Read a note again only when you wrote to it since.
