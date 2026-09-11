Replay an issue's signature over the runs and update its status.

With no `issue_id`, lists the ledger instead. `since` is a UTC prefix
bounding what counts as evidence; left empty it defaults to when the
fix was delegated, which is the only boundary that answers "did it stop
happening". An issue never closes because nobody looked: with no run
recorded after that moment, the status is left alone and this says so.
