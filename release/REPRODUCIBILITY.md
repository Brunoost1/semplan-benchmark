# Public Reproducibility Notes

This repository contains a curated public manuscript-and-results snapshot for SemPlan Benchmark. It is deliberately narrower than the private development repository.

## Frozen identifiers

- Benchmark version: `1.0.0-rc.2`
- Benchmark SHA-256: `60fe39c51a71637afd2a88f9fd44c3eee620a6e5467591d3c36a0a39a16792ed`
- Scientific results SHA-256: `5f06bc8bad81a8c0c151d4890a34432bb0166e73331371fb5c7fb8536aefb816`
- Primary records: `4800`
- Stability records: `1200`
- Recorded API spend: `USD 4.028973`

## Public scope

The public snapshot contains:

- a pointer to the archived public preprint and its DOI;
- frozen derived CSV tables supporting the reported results;
- a convenience ZIP containing the same derived CSV tables;
- public citation, license, release-manifest, and checksum metadata.

The following are deliberately excluded:

- private implementation specifications and internal development/authoring instructions;
- local credentials and environment files;
- provider caches and raw provider-response payloads;
- private working notes and machine-specific artifacts.

The manuscript and release metadata retain the frozen benchmark and scientific-result hashes so a separately archived software/dataset reproducibility deposit can be cross-checked against the reported experiment without altering the paper's numeric results.

## Latency caveat

Latency is retained in derived tables for completeness but must not be interpreted as primary cross-approach performance evidence. Captured typed failures recorded zero latency in the frozen runner, making cross-approach medians non-comparable.
