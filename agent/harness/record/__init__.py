"""What a session writes down: git, the journal, the transcript, the rationale.

The graph (agent/harness/graph.py) decides what happens. This package is
everything that survives it -- and under a review model where the human reads
prose rather than code, that is the deliverable.

Four things make a session rather than a long task, and each has a file here:

- **It commits** (git.py). Each completed unit is a commit on `session/<id>`;
  the human's gate is the merge, so nothing here can merge, push or reset.
- **It survives dying** (journal.py). Every step is appended before the next one
  starts, so a killed process resumes with what it knew rather than from zero.
- **It reports** (rationale.py, and transcript.py underneath it). The rationale
  is built from commands that actually ran and the exit codes they returned,
  not from a model's recollection of its own work.
- **It answers to the notes** (notes.py). The project's notes file is the input
  and the output: the human writes feedback there, the session appends its
  account.
"""

from agent.harness.record.git import STATE_DIR, Git
from agent.harness.record.journal import Journal, Step, replay
from agent.harness.record.notes import (NOTES_FILENAMES, append_to_notes,
                                        find_notes, read_notes)
from agent.harness.record.rationale import summarize, write_rationale
from agent.harness.record.transcript import TRANSCRIPT_DIR, Transcript

__all__ = ["STATE_DIR", "Git", "Journal", "Step", "replay", "NOTES_FILENAMES",
           "append_to_notes", "find_notes", "read_notes", "summarize",
           "write_rationale", "TRANSCRIPT_DIR", "Transcript"]
