Write down the task's requirements, and keep the list true as you work.

The list is kept in state while the conversation behind it is summarized away,
so late in a long run it is the only record of what is done and what is not.
That only works if every status on it is true.

## When

Once, after you have read the files the task concerns and before your first
edit, when the task asks for more than one thing. A task with one requirement
needs no list. Change the list when you find a requirement you missed; do not
rewrite it instead of starting. Never call it twice in the same response: the
second call replaces the first.

## What an item is

One requirement of the task, naming the file it changes and the fact that will
show it done.

- `pipeline/normalise.py skips a row whose amount cell is blank`
- `docs/pipeline.md no longer promises "one row in, one record out"`

These are not items:

- `Read NOTES.md`, `Understand the code`, `Run the tests`, `Implement the
  requested changes`. Reading and testing are how you work, not what was
  asked, and a list of phases can be ticked off without a single requirement
  being met.
- `Update the docs if needed`. Decide from what you have read, then either
  write the item or leave it out.

Each item is an object, `{"content": "...", "status": "pending"}`, never a bare
string.

## Statuses

- `in_progress`: the item you are working on now.
- `completed`: only once the edit or the command output that makes its fact
  true is in this conversation. Mark it in the next update after that evidence,
  one item at a time. Marking several items completed in one update at the end
  is a report written from memory, and it is how work that was never done gets
  reported as done.
- back to `pending`: when `git diff` (or, with no git, the file itself) shows
  nothing that makes a completed item's fact true.

An item you could not finish is not completed. Leave it `in_progress` or
`pending`, and say why in your final message.

## What it is not

It is not your account of the work. Marking the last item completed tells
nobody anything: your final message, written after your last update, names each
requirement with the `file_path:line_number` or command output that shows it
done.
