# ledger (benchmark fixture)

A small invoicing and payments service with deliberately planted problems, used to measure how many
of them Codebase City's agents find, and how often they flag code that is actually fine. Harder than
`shop`: more modules than the reviewers can each be assigned, subtler and cross-file bugs, real
tests that pass, and decoys. Never deploy it. The answers are in `../../expected_ledger.json`.
