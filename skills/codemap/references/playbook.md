# Onboarding playbook

The full sequence for "I have never seen this repo, explain it to me". Aim to
have something useful to say after step 3, and stop as soon as the user's
actual question is answered.

## 1. Scan and read the shape

```bash
python3 scripts/scan.py . -o /tmp/map.json
python3 scripts/view.py -m /tmp/map.json summary
```

Before reading further, sanity-check the header line. If edges are near zero
for a language that should have them, or the unresolved note is large, fix that
first (`references/languages.md`) — every conclusion below rests on the edges.

## 2. Read the cheap non-graph evidence

The graph says how the code is wired; these say what it is *for*. Both matter,
and this takes thirty seconds:

- `README.md`, `pyproject.toml` / `package.json` / `go.mod` / `Cargo.toml`
- `git log --oneline -15` and `git log --format='%an' | sort | uniq -c | sort -rn | head`
  — where the work actually happens
- the CI config: it names the real entry points, test commands and build steps

## 3. Form the layering hypothesis

From the package table, sort by `in:` (incoming coupling):

- **high in, low out** — the core. Domain types, shared utilities. Read first.
- **low in, high out** — the edges. CLI, HTTP handlers, jobs, UI. These are
  where behaviour starts.
- **high in, high out** — a coordinator, or a layering violation. Worth a look.
- **isolated** — vendored code, generated code, or something abandoned.

Then confirm it against the package graph:

```bash
python3 scripts/view.py -m /tmp/map.json graph --level package
```

Arrows should mostly point one way, from the edges toward the core. Where they
do not, you have found either a genuine cycle or a misnamed layer — say which.

## 4. Read the three or four files that matter

Take the top of "most depended upon" and the top-ranked entry point. Actually
open them. `interfaces` tells you what a file declares; only the source tells
you what it means.

```bash
python3 scripts/view.py -m /tmp/map.json interfaces --path <core file> --exported-only
```

This is the step that separates a real explanation from a restated graph. Do
not skip it.

## 5. Trace one path end to end

Pick the most representative flow — a request, a command, a job — and follow it
from entry point to core:

```bash
python3 scripts/view.py -m /tmp/map.json graph --focus <entry file> --hops 2 --downstream-only
```

`--downstream-only` follows what the entry point uses, without pulling in
everything that uses it.

## 6. Write it up

A good overview is short and specific. Roughly:

> **What it is.** One or two sentences: purpose, language, size.
>
> **How it is layered.** Three to five bullets, each naming a real directory and
> what lives there. A package-level Mermaid diagram here.
>
> **Where to start reading.** Three to five files, each with one line on why.
>
> **How a request flows.** One traced path, named files in order.
>
> **What to watch out for.** Cycles, hot spots, dead code, anything the static
> view cannot see (DI, plugins, reflection, codegen).

Name real files with real paths throughout — that is what makes it usable.
Percentages and node counts are not insight; say what the numbers mean.

If this is a written deliverable rather than a chat answer, publish it as an
Artifact with the Mermaid diagrams inline, and offer the interactive HTML
alongside it.

## Variant: blast radius before a change

```bash
python3 scripts/view.py -m /tmp/map.json graph --focus <file> --hops 2 --cluster
python3 scripts/view.py -m /tmp/map.json interfaces --path <file>
```

Incoming edges are the blast radius. Report it as: direct importers, then the
second ring, then which of those are entry points — that last group is what
actually ships broken.

## Variant: dead code hunt

Start from `summary`'s unreferenced list, then rule out the false positives —
CLI entry points, plugins, test-only helpers (rescan with `--include-tests` to
check), dynamically imported modules, and anything referenced from config,
templates or CI. Grep for the module name across the whole repo, including
non-source files, before calling anything dead.

## Variant: comparing two revisions

Scan two worktrees into separate JSON files and diff the summaries. Growth in
package fan-in, new cycles, and files whose fan_in jumped are the signals worth
reporting.
