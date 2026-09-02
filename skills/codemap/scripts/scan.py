#!/usr/bin/env python3
"""Build a dependency graph of a codebase.

Walks a repository, extracts declared interfaces (classes / functions / types)
and import edges between internal files, and writes a single JSON document
that view.py turns into diagrams and reports.

Stdlib only. Python 3.8+.

    ./scan.py /path/to/repo -o codemap.json
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

# --------------------------------------------------------------------------
# file discovery
# --------------------------------------------------------------------------

LANG_BY_EXT = {
    ".py": "python", ".pyi": "python",
    ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js",
    ".ts": "ts", ".tsx": "ts", ".mts": "ts", ".cts": "ts",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin", ".kts": "kotlin",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp",
    ".hpp": "cpp", ".hh": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".swift": "swift",
    ".scala": "scala",
    ".sh": "shell", ".bash": "shell",
    ".sql": "sql",
}

SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "vendor", "__pycache__",
    ".venv", "venv", "env", ".env", ".tox", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "dist", "build", "target", "out", ".next", ".nuxt",
    ".output", "coverage", ".gradle", ".idea", ".vscode", "bin", "obj",
    ".terraform", "site-packages", ".cache", ".turbo", ".parcel-cache",
    "Pods", "DerivedData", ".dart_tool",
}

TEST_RE = re.compile(
    r"(^|/)(tests?|spec|specs|__tests__|testdata|fixtures)(/|$)"
    r"|(^|/)(test_[^/]*|[^/]*_test|[^/]*\.test|[^/]*\.spec)\.[a-z]+$",
    re.I,
)

MAX_FILE_BYTES = 2_000_000
MAX_LINE_CHARS = 4000        # beyond this a file is minified or generated


def git_files(root):
    """Tracked + untracked-but-not-ignored files, or None outside a git repo."""
    try:
        out = subprocess.run(
            ["git", "-C", root, "ls-files", "--cached", "--others",
             "--exclude-standard"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return [p for p in out.stdout.splitlines() if p]


def walk_files(root):
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            full = os.path.join(dirpath, name)
            found.append(os.path.relpath(full, root))
    return found


def discover(root, include_tests):
    paths = git_files(root)
    if paths is None:
        paths = walk_files(root)
    else:
        paths = [p for p in paths
                 if not any(part in SKIP_DIRS for part in p.split("/"))]

    keep = []
    for p in paths:
        ext = os.path.splitext(p)[1].lower()
        if ext not in LANG_BY_EXT:
            continue
        if not include_tests and TEST_RE.search(p):
            continue
        full = os.path.join(root, p)
        try:
            # symlinked files are read (Homebrew-style trees are all links);
            # symlinked directories are not followed, so loops cannot occur
            if os.path.getsize(full) > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        keep.append(p.replace(os.sep, "/"))
    return sorted(keep)


def read(root, rel):
    try:
        with open(os.path.join(root, rel), "r", encoding="utf-8",
                  errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


# --------------------------------------------------------------------------
# extraction: symbols + raw import strings, per language
# --------------------------------------------------------------------------

def sym(kind, name, line, sig="", exported=True):
    return {"kind": kind, "name": name, "line": line, "sig": sig,
            "exported": exported}


def extract_python(src, rel):
    symbols, imports = [], []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return symbols, imports

    def sig_of(node):
        args = [a.arg for a in node.args.posonlyargs + node.args.args]
        if node.args.vararg:
            args.append("*" + node.args.vararg.arg)
        args += [a.arg for a in node.args.kwonlyargs]
        if node.args.kwarg:
            args.append("**" + node.args.kwarg.arg)
        return "(%s)" % ", ".join(args)

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            bases = [ast.unparse(b) if hasattr(ast, "unparse") else ""
                     for b in node.bases]
            methods = [sym("method", m.name, m.lineno, sig_of(m),
                           not m.name.startswith("_"))
                       for m in node.body
                       if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
            s = sym("class", node.name, node.lineno,
                    "(%s)" % ", ".join(b for b in bases if b),
                    not node.name.startswith("_"))
            s["members"] = methods
            symbols.append(s)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(sym("function", node.name, node.lineno, sig_of(node),
                               not node.name.startswith("_")))
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    symbols.append(sym("const", t.id, node.lineno))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imports.append({"raw": a.name, "level": 0})
        elif isinstance(node, ast.ImportFrom):
            module, level = node.module or "", node.level or 0
            imports.append({"raw": module, "level": level})
            # `from . import views` / `from pkg import submod`: the name is
            # only sometimes a module, so these are marked derived and never
            # counted as third-party dependencies
            for a in node.names:
                if a.name != "*":
                    imports.append({
                        "raw": module + "." + a.name if module else a.name,
                        "level": level, "derived": True})
    return symbols, imports


JS_EXPORT_RE = re.compile(
    r"^\s*export\s+(?:default\s+)?"
    r"(?:(?:async\s+)?function\s*\*?\s*(\w+)"
    r"|class\s+(\w+)"
    r"|(?:const|let|var)\s+(\w+)"
    r"|interface\s+(\w+)"
    r"|type\s+(\w+)"
    r"|enum\s+(\w+))",
    re.M,
)
JS_LOCAL_RE = re.compile(
    r"^\s*(?:(?:async\s+)?function\s*\*?\s*(\w+)|class\s+(\w+))", re.M)
JS_IMPORT_RE = re.compile(
    r"""(?:^|\s)(?:import|export)\s[^'"]{0,600}?from\s*['"]([^'"]+)['"]"""
    r"""|(?:^|\s)import\s*['"]([^'"]+)['"]"""
    r"""|require\(\s*['"]([^'"]+)['"]\s*\)"""
    r"""|import\(\s*['"]([^'"]+)['"]\s*\)""",
    re.M,
)


def _line_of(src, pos):
    return src.count("\n", 0, pos) + 1


def extract_js(src, rel):
    symbols, imports, seen = [], [], set()
    for m in JS_EXPORT_RE.finditer(src):
        name = next((g for g in m.groups() if g), None)
        if not name or name in seen:
            continue
        seen.add(name)
        kind = ("class" if m.group(2) else
                "interface" if m.group(4) else
                "type" if m.group(5) else
                "enum" if m.group(6) else
                "function" if m.group(1) else "const")
        symbols.append(sym(kind, name, _line_of(src, m.start())))
    for m in JS_LOCAL_RE.finditer(src):
        name = m.group(1) or m.group(2)
        if name and name not in seen:
            seen.add(name)
            symbols.append(sym("class" if m.group(2) else "function", name,
                               _line_of(src, m.start()), exported=False))
    for m in JS_IMPORT_RE.finditer(src):
        raw = next((g for g in m.groups() if g), None)
        if raw:
            imports.append({"raw": raw, "level": 0})
    return symbols, imports


GO_DECL_RE = re.compile(
    r"^func\s+(?:\([^)]*\)\s*)?(\w+)\s*(\([^\n]*)"
    r"|^type\s+(\w+)\s+(struct|interface|\w[\w\.\[\]\*]*)",
    re.M,
)
GO_IMPORT_BLOCK_RE = re.compile(r"import\s*\((.{0,4000}?)\)", re.S)
GO_IMPORT_ONE_RE = re.compile(r"""^\s*import\s+(?:\w+\s+)?["]([^"]+)["]""", re.M)
GO_QUOTED_RE = re.compile(r'"([^"]+)"')


def extract_go(src, rel):
    symbols, imports = [], []
    for m in GO_DECL_RE.finditer(src):
        line = _line_of(src, m.start())
        if m.group(1):
            name = m.group(1)
            symbols.append(sym("function", name, line,
                               m.group(2).split("{")[0].strip(),
                               name[:1].isupper()))
        else:
            name = m.group(3)
            kind = m.group(4) if m.group(4) in ("struct", "interface") else "type"
            symbols.append(sym(kind, name, line, exported=name[:1].isupper()))
    for block in GO_IMPORT_BLOCK_RE.findall(src):
        for raw in GO_QUOTED_RE.findall(block):
            imports.append({"raw": raw, "level": 0})
    for raw in GO_IMPORT_ONE_RE.findall(src):
        imports.append({"raw": raw, "level": 0})
    return symbols, imports


RUST_DECL_RE = re.compile(
    r"^\s*(pub(?:\([^)]*\))?\s+)?(fn|struct|enum|trait|type|impl)\s+(\w+)"
    r"([^\n{;]*)", re.M)
RUST_USE_RE = re.compile(r"^\s*(?:pub\s+)?use\s+([^;]+);", re.M)
RUST_MOD_RE = re.compile(r"^\s*(?:pub\s+)?mod\s+(\w+)\s*;", re.M)


def extract_rust(src, rel):
    symbols, imports = [], []
    for m in RUST_DECL_RE.finditer(src):
        kind = {"fn": "function", "impl": "impl"}.get(m.group(2), m.group(2))
        symbols.append(sym(kind, m.group(3), _line_of(src, m.start()),
                           m.group(4).strip()[:80], bool(m.group(1))))
    for use in RUST_USE_RE.findall(src):
        imports.append({"raw": use.split("{")[0].strip().rstrip(":"), "level": 0})
    for mod in RUST_MOD_RE.findall(src):
        imports.append({"raw": "mod:" + mod, "level": 0})
    return symbols, imports


JVM_MODS = (r"(?:(?:public|private|protected|internal|open|abstract|final|"
            r"sealed|data|static|override|suspend|inner|value|annotation)"
            r"[ \t]+){0,6}")
JVM_DECL_RE = re.compile(
    r"^[ \t]*(?:@\w+[ \t]*)*(" + JVM_MODS + r")"
    r"\b(class|interface|enum|object|record|trait)[ \t]+(\w+)([^\n{]{0,200})",
    re.M)
JVM_FUN_RE = re.compile(
    r"^[ \t]*(?:@\w+[ \t]*)*" + JVM_MODS +
    r"(?:fun|[\w<>\[\],.?]{1,80})[ \t]+(\w+)[ \t]*\(", re.M)
JVM_IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)(?:\.\*)?\s*;?",
                           re.M)
JVM_PKG_RE = re.compile(r"^\s*package\s+([\w.]+)", re.M)


def extract_jvm(src, rel):
    symbols, imports = [], []
    for m in JVM_DECL_RE.finditer(src):
        symbols.append(sym(m.group(2), m.group(3), _line_of(src, m.start()),
                           m.group(4).strip()[:80],
                           "private" not in (m.group(1) or "")))
    for m in JVM_FUN_RE.finditer(src):
        name = m.group(1)
        if name not in ("if", "for", "while", "switch", "catch", "return",
                        "new", "when", "synchronized"):
            symbols.append(sym("method", name, _line_of(src, m.start())))
    for raw in JVM_IMPORT_RE.findall(src):
        imports.append({"raw": raw, "level": 0})
    return symbols, imports


C_INCLUDE_RE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)
C_DECL_RE = re.compile(
    r"^(?:[A-Za-z_][\w:<>,\*&\[\] \t]{0,120}[ \t\*&])?"
    r"(\w+)[ \t]*\([^;{\n]{0,400}\)[ \t]*\{"
    r"|^[ \t]*(?:typedef[ \t]+)?(struct|class|enum|union)[ \t]+(\w+)",
    re.M)


def extract_c(src, rel):
    symbols, imports = [], []
    for m in C_DECL_RE.finditer(src):
        if m.group(1):
            if m.group(1) in ("if", "for", "while", "switch", "return",
                              "sizeof", "catch"):
                continue
            symbols.append(sym("function", m.group(1), _line_of(src, m.start())))
        else:
            symbols.append(sym(m.group(2), m.group(3), _line_of(src, m.start())))
    for raw in C_INCLUDE_RE.findall(src):
        imports.append({"raw": raw, "level": 0})
    return symbols, imports


GENERIC_DECL_RE = re.compile(
    r"^[ \t]*(?:(?:public|private|protected|open|export|abstract|final)"
    r"[ \t]+){0,4}"
    r"(class|module|interface|trait|struct|enum|def|function|func)[ \t]+(\w+)",
    re.M)


def extract_generic(src, rel):
    symbols = [sym({"def": "function", "func": "function",
                    "function": "function"}.get(m.group(1), m.group(1)),
                   m.group(2), _line_of(src, m.start()))
               for m in GENERIC_DECL_RE.finditer(src)]
    return symbols, []


EXTRACTORS = {
    "python": extract_python,
    "js": extract_js, "ts": extract_js,
    "go": extract_go,
    "rust": extract_rust,
    "java": extract_jvm, "kotlin": extract_jvm, "scala": extract_jvm,
    "c": extract_c, "cpp": extract_c,
}


# --------------------------------------------------------------------------
# resolution: raw import string -> internal file path
# --------------------------------------------------------------------------

JS_EXTS = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".d.ts"]

# NodeNext/ESM imports name the *emitted* file; map it back to the source
JS_SOURCE_ALIASES = {
    ".js": [".ts", ".tsx", ".js", ".jsx"],
    ".mjs": [".mts", ".mjs"],
    ".cjs": [".cts", ".cjs"],
    ".jsx": [".tsx", ".jsx"],
}

JSONC_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def load_json_loose(path):
    """json.load, retried with comments and trailing commas stripped so that
    tsconfig.json files parse."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return {}
    for candidate in (text, TRAILING_COMMA_RE.sub(
            r"\1", JSONC_COMMENT_RE.sub("", text))):
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        return data if isinstance(data, dict) else {}
    return {}


class Resolver:
    def __init__(self, root, paths):
        self.root = root
        self.paths = set(paths)
        self.by_noext = defaultdict(list)
        self.by_basename = defaultdict(list)
        for p in paths:
            self.by_noext[os.path.splitext(p)[0]].append(p)
            self.by_basename[os.path.basename(p)].append(p)
        self.top_dirs = {p.split("/")[0] for p in paths if "/" in p}
        self.go_module = self._go_module()
        self.js_aliases = self._js_aliases()
        self.js_workspaces = self._js_workspaces()
        self.py_roots = self._py_roots()
        # scanning a package directory directly (e.g. site-packages/pygments):
        # its own name still prefixes every absolute import inside it
        self.self_pkg = (os.path.basename(os.path.abspath(root))
                         if "__init__.py" in self.paths else None)

    def _find_configs(self, names, max_depth=4):
        hits = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            rel = os.path.relpath(dirpath, self.root)
            depth = 0 if rel == "." else rel.count(os.sep) + 1
            dirnames[:] = [d for d in dirnames
                           if d not in SKIP_DIRS and not d.startswith(".")]
            if depth >= max_depth:
                dirnames[:] = []
            for name in names:
                if name in filenames:
                    hits.append(os.path.join(dirpath, name))
        return hits

    def _inside(self, abs_path):
        """Repo-relative path, or None when it escapes the root."""
        rel = os.path.relpath(abs_path, self.root).replace(os.sep, "/")
        return None if rel.startswith("..") else ("" if rel == "." else rel)

    def _js_aliases(self):
        """compilerOptions.paths from every tsconfig/jsconfig in the tree, as
        (prefix, [target prefixes], exact) triples."""
        aliases = []
        for cfg in self._find_configs(("tsconfig.json", "jsconfig.json")):
            opts = load_json_loose(cfg).get("compilerOptions") or {}
            paths = opts.get("paths")
            if not isinstance(paths, dict):
                continue
            base = os.path.normpath(os.path.join(
                os.path.dirname(cfg), opts.get("baseUrl") or "."))
            for pattern, targets in paths.items():
                if not isinstance(targets, list):
                    continue
                resolved = []
                for t in targets:
                    if not isinstance(t, str):
                        continue
                    rel = self._inside(os.path.normpath(
                        os.path.join(base, t.replace("*", ""))))
                    if rel is not None:
                        resolved.append(rel.rstrip("/"))
                if resolved:
                    aliases.append((pattern.replace("*", ""), resolved,
                                    "*" not in pattern))
        # longest prefix first so "@app/ui" beats "@app/"
        return sorted(aliases, key=lambda a: -len(a[0]))

    def _js_workspaces(self):
        """package.json `name` -> the directory that declares it."""
        out = {}
        for cfg in self._find_configs(("package.json",), max_depth=5):
            name = load_json_loose(cfg).get("name")
            if not isinstance(name, str) or not name:
                continue
            rel = self._inside(os.path.dirname(cfg))
            if rel is not None:
                out[name] = rel
        return out

    def _go_module(self):
        gomod = os.path.join(self.root, "go.mod")
        if os.path.exists(gomod):
            with open(gomod, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.startswith("module "):
                        return line.split(None, 1)[1].strip()
        return None

    def _py_roots(self):
        """Directories that behave as import roots: the repo root, the usual
        source dirs, and the parent of every top-level package."""
        roots = {""}
        for cand in ("src", "lib", "app", "python"):
            if os.path.isdir(os.path.join(self.root, cand)):
                roots.add(cand)
        pkg_dirs = {os.path.dirname(p) for p in self.paths
                    if os.path.basename(p) == "__init__.py"}
        for d in pkg_dirs:
            cur = d
            while True:
                parent = os.path.dirname(cur)
                if parent != cur and parent in pkg_dirs:
                    cur = parent
                else:
                    break
            roots.add(os.path.dirname(cur))
        return sorted(roots)

    # -- helpers ----------------------------------------------------------
    def _first(self, candidates):
        for c in candidates:
            c = c.lstrip("/")
            if c in self.paths:
                return c
        return None

    def _js_candidates(self, base):
        base = base.strip("/")
        out = []
        stem, ext = os.path.splitext(base)
        if ext in JS_SOURCE_ALIASES:
            out += [stem + e for e in JS_SOURCE_ALIASES[ext]]
        if base in self.paths:
            out.append(base)
        out += [base + e for e in JS_EXTS]
        out += [base + "/index" + e for e in JS_EXTS]
        return out

    # -- per language -----------------------------------------------------
    def python(self, src_file, raw, level):
        parts = [p for p in raw.split(".") if p]
        if level:
            base = os.path.dirname(src_file)
            for _ in range(level - 1):
                base = os.path.dirname(base)
            stem = "/".join([base] + parts) if base else "/".join(parts)
            return self._first([stem + ".py", stem + "/__init__.py"])
        variants = [parts]
        if self.self_pkg and parts[:1] == [self.self_pkg]:
            variants.append(parts[1:])
        for cand in variants:
            if not cand:
                continue
            for root in self.py_roots:
                stem = "/".join(([root] if root else []) + cand)
                hit = self._first([stem + ".py", stem + "/__init__.py"])
                if hit:
                    return hit
                # `from pkg.mod import name` where name is itself the module
                if len(cand) > 1:
                    stem2 = "/".join(([root] if root else []) + cand[:-1])
                    hit = self._first([stem2 + ".py", stem2 + "/__init__.py"])
                    if hit:
                        return hit
        return None

    def js(self, src_file, raw):
        if raw.startswith("."):
            base = os.path.normpath(
                os.path.join(os.path.dirname(src_file), raw)).replace(os.sep, "/")
            return self._first(self._js_candidates(base))

        for prefix, targets, exact in self.js_aliases:
            if exact:
                if raw != prefix.rstrip("/"):
                    continue
                rest = ""
            else:
                if not prefix or not raw.startswith(prefix):
                    continue
                rest = raw[len(prefix):]
            for t in targets:
                hit = self._first(self._js_candidates(
                    t + "/" + rest if rest else t))
                if hit:
                    return hit

        for name, d in self.js_workspaces.items():
            if raw != name and not raw.startswith(name + "/"):
                continue
            rest = raw[len(name):].lstrip("/")
            bases = ([d + "/" + rest, d + "/src/" + rest] if rest
                     else [d + "/src/index", d + "/index", d + "/src/main"])
            hit = self._first([c for b in bases
                               for c in self._js_candidates(b)])
            if hit:
                return hit

        stripped = None
        for prefix in ("@/", "~/", "#/"):
            if raw.startswith(prefix):
                stripped = raw[len(prefix):]
                break
        if stripped is None:
            if raw.startswith("@") or raw.split("/")[0] not in self.top_dirs:
                return None
            stripped = raw
        cands = []
        for root in ("", "src", "app", "lib"):
            base = "/".join([p for p in (root, stripped) if p])
            cands += self._js_candidates(base)
        return self._first(cands)

    def go(self, raw):
        if not self.go_module or not raw.startswith(self.go_module):
            return None
        rel = raw[len(self.go_module):].strip("/")
        hits = [p for p in self.paths
                if p.endswith(".go") and os.path.dirname(p) == rel]
        return sorted(hits)[0] if hits else None

    def rust(self, src_file, raw):
        if raw.startswith("mod:"):
            name = raw[4:]
            d = os.path.dirname(src_file)
            stem = os.path.basename(src_file).rsplit(".", 1)[0]
            bases = [f"{d}/{name}", f"{d}/{stem}/{name}"] if stem not in (
                "mod", "lib", "main") else [f"{d}/{name}"]
            return self._first([b + ".rs" for b in bases]
                               + [b + "/mod.rs" for b in bases])
        parts = [p for p in raw.replace(" ", "").split("::") if p]
        if not parts or parts[0] not in ("crate", "self", "super"):
            return None
        if parts[0] == "crate":
            rest = parts[1:]
            bases = ["src/" + "/".join(rest[:i]) for i in range(len(rest), 0, -1)]
        else:
            d = os.path.dirname(src_file)
            if parts[0] == "super":
                d = os.path.dirname(d)
            rest = parts[1:]
            bases = [d + "/" + "/".join(rest[:i]) for i in range(len(rest), 0, -1)]
        cands = []
        for b in bases:
            cands += [b + ".rs", b + "/mod.rs"]
        return self._first(cands)

    def jvm(self, raw):
        parts = raw.split(".")
        for i in range(len(parts), 0, -1):
            stem = "/".join(parts[:i])
            hits = [p for p in self.by_noext
                    if p == stem or p.endswith("/" + stem)]
            if hits:
                return self.by_noext[sorted(hits, key=len)[0]][0]
        return None

    def c(self, src_file, raw):
        base = os.path.normpath(
            os.path.join(os.path.dirname(src_file), raw)).replace(os.sep, "/")
        hit = self._first([base])
        if hit:
            return hit
        hits = self.by_basename.get(os.path.basename(raw), [])
        return sorted(hits, key=len)[0] if hits else None

    def resolve(self, src_file, lang, imp):
        raw, level = imp["raw"], imp.get("level", 0)
        if not raw and not level:
            return None
        try:
            if lang == "python":
                return self.python(src_file, raw, level)
            if lang in ("js", "ts"):
                return self.js(src_file, raw)
            if lang == "go":
                return self.go(raw)
            if lang == "rust":
                return self.rust(src_file, raw)
            if lang in ("java", "kotlin", "scala"):
                return self.jvm(raw)
            if lang in ("c", "cpp"):
                return self.c(src_file, raw)
        except (ValueError, OSError):
            return None
        return None


NODE_BUILTINS = {
    "fs", "path", "os", "http", "https", "url", "util", "events", "stream",
    "crypto", "child_process", "buffer", "zlib", "net", "tls", "dns", "assert",
    "readline", "worker_threads", "cluster", "timers", "querystring", "vm",
    "perf_hooks", "string_decoder", "tty", "process", "module", "console",
}
GO_STDLIB_ROOTS = {
    "fmt", "os", "io", "net", "time", "strings", "strconv", "errors", "sort",
    "sync", "context", "encoding", "bytes", "bufio", "math", "regexp", "log",
    "path", "reflect", "runtime", "testing", "flag", "unicode", "crypto",
    "database", "html", "text", "container", "hash", "compress", "archive",
    "embed", "slices", "maps", "cmp",
}
JVM_STDLIB_ROOTS = {"java", "javax", "jakarta", "kotlin", "kotlinx", "scala"}
RUST_STDLIB_ROOTS = {"std", "core", "alloc"}

try:
    PY_STDLIB = set(sys.stdlib_module_names)          # Python 3.10+
except AttributeError:                                # pragma: no cover
    PY_STDLIB = {
        "os", "sys", "re", "json", "io", "time", "math", "typing", "abc",
        "collections", "itertools", "functools", "logging", "pathlib", "enum",
        "dataclasses", "subprocess", "argparse", "datetime", "random", "copy",
        "hashlib", "unittest", "asyncio", "threading", "shutil", "csv", "glob",
        "textwrap", "traceback", "warnings", "inspect", "operator", "string",
        "struct", "socket", "urllib", "http", "sqlite3", "pickle", "uuid",
    }


def is_stdlib(lang, name):
    if lang == "python":
        return name in PY_STDLIB
    if lang in ("js", "ts"):
        return name in NODE_BUILTINS or name.startswith("node:")
    if lang == "go":
        return name in GO_STDLIB_ROOTS
    if lang == "rust":
        return name in RUST_STDLIB_ROOTS
    if lang in ("java", "kotlin", "scala"):
        return name in JVM_STDLIB_ROOTS
    return False


ASSET_EXTS = (".css", ".scss", ".sass", ".less", ".styl", ".svg", ".png",
              ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".ico", ".json",
              ".yaml", ".yml", ".graphql", ".gql", ".wasm", ".txt", ".md",
              ".html", ".woff", ".woff2", ".mp3", ".mp4", ".webm", ".csv")


def is_asset(raw):
    return raw.lower().split("?")[0].endswith(ASSET_EXTS)


def external_name(lang, raw, level=0):
    """Normalised third-party package name, or None if it looks internal."""
    if level or not raw or raw.startswith(".") or raw.startswith("mod:"):
        return None
    if lang in ("js", "ts"):
        if raw.startswith(("@/", "~/", "#/", "/")):
            return None
        parts = raw.split("/")
        return "/".join(parts[:2]) if raw.startswith("@") else parts[0]
    if lang == "rust":
        head = raw.split("::")[0]
        return None if head in ("crate", "self", "super") else head
    if lang in ("c", "cpp"):
        # only reached for includes that matched no file in the tree, so this
        # is a system or vendored header rather than a missing edge
        return raw.split("/")[0]
    return raw.split(".")[0].split("/")[0]


# --------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------

ENTRY_NAMES = {
    "main.py", "__main__.py", "app.py", "cli.py", "manage.py", "wsgi.py",
    "asgi.py", "server.py", "run.py", "main.go", "main.rs", "lib.rs",
    "index.js", "index.ts", "main.js", "main.ts", "app.js", "app.ts",
    "server.js", "server.ts", "Main.java", "Application.java", "Main.kt",
    "main.c", "main.cpp",
}


def entry_score(rel, src):
    name = os.path.basename(rel)
    score = 0
    if name in ENTRY_NAMES:
        score += 3
    if rel.startswith(("cmd/", "bin/", "scripts/", "src/bin/")):
        score += 2
    if 'if __name__ == "__main__"' in src or "if __name__ == '__main__'" in src:
        score += 3
    if re.search(r"^func\s+main\s*\(\s*\)", src, re.M):
        score += 3
    if re.search(r"^\s*fn\s+main\s*\(", src, re.M):
        score += 3
    if re.search(r"public\s+static\s+void\s+main\s*\(", src):
        score += 3
    return score


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def build(root, include_tests, max_files):
    paths = discover(root, include_tests)
    truncated = False
    if max_files and len(paths) > max_files:
        # keep the largest files: they carry the structure
        sized = sorted(paths,
                       key=lambda p: os.path.getsize(os.path.join(root, p)),
                       reverse=True)
        paths = sorted(sized[:max_files])
        truncated = True

    resolver = Resolver(root, paths)
    nodes, raw_imports = {}, {}

    for rel in paths:
        lang = LANG_BY_EXT[os.path.splitext(rel)[1].lower()]
        src = read(root, rel)
        minified = any(len(line) > MAX_LINE_CHARS for line in src.split("\n"))
        extractor = EXTRACTORS.get(lang, extract_generic)
        try:
            symbols, imports = ([], []) if minified else extractor(src, rel)
        except (re.error, RecursionError, ValueError):
            symbols, imports = [], []
        nodes[rel] = {
            "path": rel,
            "lang": lang,
            "dir": os.path.dirname(rel) or ".",
            "loc": src.count("\n") + 1 if src else 0,
            "bytes": len(src),
            "is_test": bool(TEST_RE.search(rel)),
            "minified": minified,
            "symbols": symbols[:200],
            "entry_score": entry_score(rel, src),
        }
        raw_imports[rel] = imports

    edges, unresolved, externals = [], defaultdict(int), defaultdict(int)
    seen = set()
    for rel, imports in raw_imports.items():
        lang = nodes[rel]["lang"]
        for imp in imports:
            target = resolver.resolve(rel, lang, imp)
            if target == rel:
                continue
            if target:
                key = (rel, target)
                if key not in seen:
                    seen.add(key)
                    edges.append({"from": rel, "to": target})
                continue
            if is_asset(imp["raw"]) or imp.get("derived"):
                continue
            ext = external_name(lang, imp["raw"], imp.get("level", 0))
            if not ext:
                unresolved[imp["raw"] or "."] += 1
            elif not is_stdlib(lang, ext):
                externals[ext] += 1

    for n in nodes.values():
        n["fan_out"] = 0
        n["fan_in"] = 0
    for e in edges:
        nodes[e["from"]]["fan_out"] += 1
        nodes[e["to"]]["fan_in"] += 1
    for n in nodes.values():
        total = n["fan_in"] + n["fan_out"]
        n["instability"] = round(n["fan_out"] / total, 2) if total else 0.0

    # externals: drop internal top-level dirs, the package's own name, and
    # anything that is part of the language's standard library
    ext_clean = {k: v for k, v in externals.items()
                 if k not in resolver.top_dirs and k != resolver.self_pkg}

    return {
        "version": 1,
        "root": os.path.abspath(root),
        "name": os.path.basename(os.path.abspath(root)),
        "stats": {
            "files": len(nodes),
            "edges": len(edges),
            "loc": sum(n["loc"] for n in nodes.values()),
            "languages": dict(sorted(
                ((l, sum(1 for n in nodes.values() if n["lang"] == l))
                 for l in {n["lang"] for n in nodes.values()}),
                key=lambda kv: -kv[1])),
            "truncated": truncated,
            "unresolved_imports": sum(unresolved.values()),
            # the import strings that could not be mapped to a file: a long
            # tail here usually means an unhandled alias or path mapping
            "unresolved_samples": dict(sorted(unresolved.items(),
                                              key=lambda kv: -kv[1])[:20]),
        },
        "externals": dict(sorted(ext_clean.items(),
                                 key=lambda kv: -kv[1])[:60]),
        "nodes": list(nodes.values()),
        "edges": edges,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("root", nargs="?", default=".", help="repository root")
    ap.add_argument("-o", "--out", default="codemap.json",
                    help="output JSON path (default: codemap.json)")
    ap.add_argument("--include-tests", action="store_true",
                    help="keep test files in the graph")
    ap.add_argument("--max-files", type=int, default=4000,
                    help="cap on analysed files (0 = no cap)")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.root):
        sys.exit("not a directory: %s" % args.root)

    graph = build(args.root, args.include_tests, args.max_files)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(graph, fh, indent=1)

    s = graph["stats"]
    print("%s: %d files, %d edges, %d LOC -> %s"
          % (graph["name"], s["files"], s["edges"], s["loc"], args.out))
    if s["truncated"]:
        print("  note: file cap hit, graph is partial (raise --max-files)")
    if s["unresolved_imports"]:
        print("  note: %d internal-looking imports unresolved"
              % s["unresolved_imports"])


if __name__ == "__main__":
    main()
