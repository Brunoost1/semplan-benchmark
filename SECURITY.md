# Security Policy

Do not commit secrets, provider credentials, private endpoints, real customer
data, or hidden benchmark artifacts.

Report suspected vulnerabilities or accidental sensitive-data exposure directly
to the project owner. Public disclosure should wait until the issue has been
triaged and remediated.

The evaluated system must never execute generated SQL without AST validation,
allowlisted read-only SQL, database timeouts, complexity limits, and row caps.
