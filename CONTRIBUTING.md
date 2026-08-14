# Contributing

SemPlan Benchmark is a research artifact. Before proposing a change, inspect
the public schemas, prompts, benchmark fixtures, and tests that define the
behavior being changed.

Contributions must preserve the independence rules: use only synthetic or
explicitly compatible public data, avoid provider calls in ordinary tests, and
do not inspect hidden evaluation data for tuning.

Every behavior change should include focused tests and should avoid changing
frozen benchmark/result artifacts unless the change is explicitly labeled as a
new benchmark version.
