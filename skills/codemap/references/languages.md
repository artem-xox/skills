# Language support

For every language the scanner records files, LOC and declared symbols. Import
edges — the part that makes the graph a graph — need per-language resolution,
and that is where coverage differs.

| Language | Symbols | Import edges | Notes |
|---|---|---|---|
| Python | AST (exact) | full | packages, relative imports, `from pkg import submod` |
| TypeScript / JavaScript | regex | full | relative, `tsconfig` `paths`, workspaces, `@/` aliases, NodeNext `.js` → `.ts` |
| Go | regex | full | needs `go.mod`; resolves module-internal import paths to their package dir |
| Rust | regex | full | `use crate::/self::/super::`, `mod x;` → `x.rs` or `x/mod.rs` |
| Java / Kotlin / Scala | regex | partial | `import a.b.C` matched against the file tree by suffix |
| C / C++ | regex | partial | `#include "..."` only; relative first, then unique basename |
| Ruby, PHP, C#, Swift, shell, SQL | generic regex | none | nodes and symbols only, no edges |

## Python

`ast.parse`, so symbols and their line numbers are exact: top-level classes
(with their methods), functions, and `UPPER_CASE` module constants.

Import roots are inferred: the repo root, `src/`, `lib/`, `app/`, `python/`, and
the parent of every top-level package. When the scan root is *itself* a package
(`scan.py site-packages/pygments`), imports prefixed with the package's own name
are also tried without the prefix.

`from . import views` and `from pkg import submod` both emit a derived candidate
for the imported name, so submodule imports resolve. Derived candidates never
count towards the third-party dependency histogram.

Misses: `importlib`, `__import__`, plugin registries, anything under
`if TYPE_CHECKING` is *included* (it is a real import statement), star-imports
resolve to the module but not to what they pull in.

## TypeScript / JavaScript

Import forms matched: `import … from '…'`, bare `import '…'`, `export … from
'…'`, `require('…')`, dynamic `import('…')`.

Resolution order:

1. Relative paths, trying `.ts .tsx .js .jsx .mjs .cjs .mts .d.ts` and
   `…/index.<ext>`. NodeNext specifiers (`./foo.js` in a TS project) are also
   tried as `./foo.ts` / `.tsx`.
2. `compilerOptions.paths` from every `tsconfig.json` / `jsconfig.json` in the
   tree (up to 4 levels deep), honouring `baseUrl`, longest prefix first.
   Comments and trailing commas are tolerated.
3. Workspace packages: every `package.json` `name` in the tree (up to 5 levels)
   maps to its directory; `@scope/pkg/sub` resolves through it.
4. `@/`, `~/`, `#/` prefixes against the root, `src/`, `app/`, `lib/`.

Asset imports (`.css`, `.svg`, `.json`, fonts, media) are recognised and
excluded from both the edge list and the unresolved count.

Misses: `paths` in a tsconfig that is `extends`-ed from outside the repo,
Webpack/Vite `resolve.alias` (not read), and monorepo packages whose entry point
is a built `dist/` path with no matching source.

Minified or generated files — any file with a line over 4000 characters — are
kept as nodes but not parsed.

## Go

Requires `go.mod` at the scan root to know the module path. An import starting
with the module path maps to that directory; the first `.go` file in it stands
in for the package, so package-level aggregation is accurate and file-level
edges point at a representative file.

Standard-library imports are recognised and excluded from the third-party list.

## Rust

`use crate::a::b` and `use self::/super::` walk the path from longest to
shortest, trying `a/b.rs` then `a/b/mod.rs`. `mod x;` resolves relative to the
declaring file, handling both `foo.rs` + `foo/bar.rs` and `foo/mod.rs` layouts.
`use` of an external crate is recorded as a third-party dependency.

## Java / Kotlin / Scala

`import a.b.C` is matched by suffix against the file tree, longest match first —
so `com.example.auth.TokenStore` finds `src/main/java/com/example/auth/TokenStore.java`
regardless of the source root. Same-package references without an explicit
import are invisible, which understates coupling inside a package. Spring-style
runtime wiring is invisible too.

## C / C++

Only `#include "..."` (quoted) is followed — `<system>` includes are not.
Resolution tries the path relative to the including file first, then a unique
basename match anywhere in the tree. A quoted include that matches nothing is
reported as an external header rather than a missing edge, which is usually
right for vendored trees.

## Adding a language

1. Write `extract_<lang>(src, rel) -> (symbols, imports)` in `scan.py`. Return
   `sym(kind, name, line, sig, exported)` entries and `{"raw": …, "level": 0}`
   import records.
2. Register it in `EXTRACTORS` and add the extensions to `LANG_BY_EXT`.
3. Add a `Resolver.<lang>(src_file, raw)` method and a branch in
   `Resolver.resolve`.
4. Add the language's standard-library roots to `is_stdlib`.

Bound every quantifier in a new regex. Unbounded lazy runs (`[^x]*?`) and
nested alternation under `*` cause catastrophic backtracking on large generated
files, which is the one way to make this scanner hang.
