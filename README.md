# SemPlan Benchmark

**SemPlan: Benchmarking Structured Semantic Planning for LLM-Based Queries over Enterprise Data**

SemPlan is an independent research project that evaluates four architectural strategies for LLM-mediated queries over governed enterprise-style data:

- **A1 — Direct SQL**
- **A2 — Tool Agent**
- **A3 — Structured Semantic Request + deterministic planning/execution**
- **A4 — Clarification + Structured State**

The reported study uses a deterministic synthetic bilingual benchmark in English and Brazilian Portuguese. No customer or proprietary data are used.

## Frozen study

- Benchmark version: `1.0.0-rc.2`
- Benchmark SHA-256: `60fe39c51a71637afd2a88f9fd44c3eee620a6e5467591d3c36a0a39a16792ed`
- Scientific results SHA-256: `5f06bc8bad81a8c0c151d4890a34432bb0166e73331371fb5c7fb8536aefb816`
- Benchmark cases: **1,800** total, **1,200** in the frozen scientific evaluation subset
- Primary records: **4,800**
- Stability records: **1,200**
- Total recorded API spend for the frozen scientific run: **USD 4.028973**

## Main findings

Absolute answer correctness was low across all four approaches. A3 had the highest observed primary answer correctness (25.67%) and the highest answer-correct repeatability (98.67%). A1 retained the highest policy-correct rate and the lowest unsafe-or-invalid rate. A4 had the lowest mean API cost and the lowest false-refusal rate. The results therefore support a **trade-off interpretation**, not a universal ranking of architectures.

Latency is not used as primary comparative evidence because the frozen runner recorded zero latency for some captured typed failures, making cross-approach medians non-comparable.

## Public Contents

This repository intentionally contains only material suitable for public scientific distribution and no internal working documentation:

- `src/semplan/` — runnable benchmark, provider, scoring, and analysis code;
- `tests/` — offline unit, contract, property, and golden tests;
- `schemas/`, `catalog/`, `configs/`, `prompts/`, `db/` — executable contracts and benchmark configuration;
- `data/benchmark/` — deterministic synthetic benchmark fixtures used by the public checks;
- `results/` — frozen derived CSV result tables used by the manuscript, plus a convenience ZIP bundle;
- `paper/figures/` — generated public result figures;
- `paper/README.md` — public preprint pointer and DOI;
- `release/` — public release metadata, reproducibility notes, and checksums.

Private implementation specifications, internal authoring/development instructions, local credentials, provider caches, raw provider responses, and other non-public working materials are intentionally excluded.

## Quick Start

Install `uv`, then run the free validation path:

```bash
uv sync --python 3.12 --extra dev --frozen
make validate-free
```

This path is offline with respect to model providers: it must not require `OPENAI_API_KEY`, does not call paid APIs, regenerates deterministic small synthetic data, and validates the public benchmark smoke fixture.

Optional Docker/PostgreSQL checks are available:

```bash
make db-up db-migrate db-load-small validate-benchmark-db e2e-free test-integration db-down
```

The release-scale benchmark can be validated without provider calls:

```bash
uv run --python 3.12 --extra dev python -m semplan.cli.main validate-benchmark data/benchmark/f7_release_scale --allow-hidden --require-approved
uv run --python 3.12 --extra dev python -m semplan.cli.main validate-release-benchmark data/benchmark/f7_release_scale
uv run --python 3.12 --extra dev python -m semplan.cli.main validate-language-quality data/benchmark/f7_release_scale
```

## Preprint

Zenodo DOI: **10.5281/zenodo.21904872**

## Author

**Bruno Santos Teixeira**  
Universidade Federal de Ouro Preto (UFOP), Brazil  
ORCID: `0009-0007-3860-7114`

## AI assistance disclosure

Generative AI tools assisted development and debugging of the experimental software pipeline. The author is responsible for the study design, validation, analysis, and scientific claims.

## Licenses

- Code/source files authored for SemPlan: **Apache License 2.0**.
- Dataset/documentation and scientific artifact metadata authored for SemPlan: **CC BY 4.0**, unless a file states otherwise.
- Third-party files retain their original licenses.
