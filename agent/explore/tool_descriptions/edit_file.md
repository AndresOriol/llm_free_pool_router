Replace an exact string in a file you have already read.

Two jobs, and both are ones `write_file` does badly:

1. **Extend a note as evidence arrives**, instead of holding findings in your
   head and writing the whole file at the end.
2. **Correct what you have already written.** When you read your report back
   and find a claim its source does not support, a figure whose units are wrong,
   or a citation pointing at the wrong page, fix that sentence here. Rewriting a
   thirty-thousand-character report to change one line costs the whole report in
   output tokens and quietly drops whatever you forget to retype.

Usage:
- Read the file first; this is refused otherwise.
- `old_string` must appear exactly once, whitespace included, unless you pass
  `replace_all`. Quote enough surrounding text to make it unique.
- To append a section, anchor on the last heading or line you wrote and put both
  it and the new text in `new_string`.
- Prefer this to `write_file` on any path that already holds work: `write_file`
  replaces the file, and an accidental overwrite of your own note costs the
  searches that produced it.
