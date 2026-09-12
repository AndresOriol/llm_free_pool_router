Replace an exact string in a file you have already read.

Two jobs, and both are ones `write_file` does badly:

1. **Extend a page as evidence arrives** — yours, or one an earlier run wrote —
   instead of holding findings in your head or starting a duplicate page.
2. **Correct what has already been written.** When you read a page back and
   find a claim its source does not support, a figure whose units are wrong, or
   a citation pointing at the wrong page, fix that sentence here. Rewriting a
   thirty-thousand-character page to change one line costs the whole page in
   output tokens and quietly drops whatever you forget to retype.

Usage:
- Read the file first; this is refused otherwise.
- `old_string` must appear exactly once, whitespace included, unless you pass
  `replace_all`. Quote enough surrounding text to make it unique.
- To append — a finding, an entry to `log.md` — anchor on the last line of the
  file and put both it and the new text in `new_string`.
- Prefer this to `write_file` on any path that already holds work: `write_file`
  replaces the file, and an accidental overwrite of a page costs the searches
  that produced it.
