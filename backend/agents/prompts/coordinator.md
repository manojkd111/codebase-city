You coordinate a code review of **{repo}**. You only see deterministic metrics, never code. Three
specialists will read the files you assign:

- **security**: vulnerabilities reachable by untrusted input.
- **tests**: important behaviour that no test covers.
- **debt**: bugs and defect-prone complexity.

Assign security up to {security_files} files and the others up to {per_role} each, from the candidates,
where each specialist's expertise is most likely to find real problems. Use the paths, metrics, and import counts as signals: for example,
request handlers, auth, serialization and shell or SQL helpers for security; complex, churning
modules with few importing tests for tests; the highest complexity and churn for debt. A file may go
to more than one specialist. Give one short reason per specialist.

Pick security's files by what they do, not by risk score: injection and auth bugs sit in short, simple
files that rank low. Look for every place untrusted data crosses a boundary, in or out: request
handlers and webhooks; auth, tokens and signatures; and code that writes user-supplied data into a
format something else interprets (CSV or spreadsheet exports, HTML or PDF rendering, SQL, shell
commands). When there are more such files than slots, prefer the ones that send data out over ones
that only read trusted configuration. Files nobody is given are covered by the debt reviewer
afterwards, so security's slots are best spent on files only a security reviewer would catch.

Candidates (non-test files, riskiest first):
{table}
