"""The debt ledger's own machinery: the operation journal (programme 015, phase B step 4).

`journal` is the only module here today, and since step 4 slice C (2026-09-12) it is LIVE: importing
it arms the journal on the `Engine` and `Session` classes for the whole process, and
`app/db/models/__init__.py` imports it alongside the tables it protects. From then on a row in
`debts` may only change inside a declared debt operation, on every engine this process creates - a
process that imported only the models included, which is what the protection has to survive
(`C15`). Slice A built the mechanism and registered nothing; slice C is the registration.
"""
