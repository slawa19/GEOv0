"""The debt ledger: its single writer and its detector.

* `book.py` - the only writer of `debts` (programme 018): every caller declares an operation
  envelope and hands the book its effects.
* `reconciliation.py` - criteria (a) and (b) of programme 015 and the reaction to a confirmed
  `FAILED` (an integrity hold).

The journal itself is written by the DATABASE: triggers on `debts` and on the three journal tables
(`app/db/journal_triggers.py`, migration 029, programme 018 stage B). HISTORY: until then this
package held `journal.py`, a listener armed on `Engine`/`Session` at import (015, phase B step 4,
slice C, 2026-09-12); stage B deleted it.
"""
