#!/usr/bin/env python3
"""Query and render a codemap.json produced by scan.py.

    ./view.py summary                     overview: size, layers, hubs, entries
    ./view.py graph --level package       Mermaid graph of package dependencies
    ./view.py graph --focus src/auth      neighbourhood of one file/dir
    ./view.py cycles                      import cycles, worst first
    ./view.py interfaces --path src/auth  declared classes / functions / types
    ./view.py html -o map.html            self-contained interactive graph

Stdlib only. Python 3.8+.
"""
from __future__ import annotations

import argparse
import html as html_mod
import json
import os
import re
import sys
from collections import defaultdict

# --------------------------------------------------------------------------
# loading and aggregation
# --------------------------------------------------------------------------


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except OSError as exc:
        sys.exit("cannot read %s (%s) -- run scan.py first" % (path, exc))


def package_of(path, depth):
    parts = path.split("/")[:-1]
    if not parts:
        return "(root)"
    return "/".join(parts[:depth]) or "(root)"


def auto_depth(nodes, target=24):
    """Shallowest directory depth that splits the tree into a readable number
    of groups: go deeper only while it keeps adding groups and stays under
    `target`."""
    best, best_count = 1, len({package_of(n["path"], 1) for n in nodes})
    for depth in range(2, 7):
        count = len({package_of(n["path"], depth) for n in nodes})
        if count > target or count == best_count:
            break
        best, best_count = depth, count
        if count >= min(target, 8):
            break
    return best


def aggregate(graph, depth):
    packages = defaultdict(lambda: {"files": 0, "loc": 0, "paths": []})
    pkg_of = {}
    for n in graph["nodes"]:
        pkg = package_of(n["path"], depth)
        pkg_of[n["path"]] = pkg
        p = packages[pkg]
        p["files"] += 1
        p["loc"] += n["loc"]
        p["paths"].append(n["path"])

    weights = defaultdict(int)
    for e in graph["edges"]:
        a, b = pkg_of[e["from"]], pkg_of[e["to"]]
        if a != b:
            weights[(a, b)] += 1
    return packages, weights, pkg_of


def sccs(node_ids, out_edges):
    """Tarjan strongly connected components with size > 1, iterative."""
    index, low, on_stack, stack, result = {}, {}, set(), [], []
    counter = [0]
    for root in node_ids:
        if root in index:
            continue
        work = [(root, iter(out_edges.get(root, ())))]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter[0]
                    counter[0] += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(out_edges.get(nxt, ()))))
                    advanced = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                if len(comp) > 1:
                    result.append(sorted(comp))
    return sorted(result, key=len, reverse=True)


def adjacency(graph):
    out, inc = defaultdict(list), defaultdict(list)
    for e in graph["edges"]:
        out[e["from"]].append(e["to"])
        inc[e["to"]].append(e["from"])
    return out, inc


# --------------------------------------------------------------------------
# summary
# --------------------------------------------------------------------------

def cmd_summary(graph, args):
    nodes = graph["nodes"]
    by_path = {n["path"]: n for n in nodes}
    s = graph["stats"]
    depth = args.depth or auto_depth(nodes)
    packages, weights, _ = aggregate(graph, depth)

    print("# %s" % graph["name"])
    print("%d files - %s LOC - %d internal import edges"
          % (s["files"], format(s["loc"], ","), s["edges"]))
    print("languages: " + ", ".join("%s %d" % (k, v)
                                    for k, v in s["languages"].items()))
    if s.get("truncated"):
        print("WARNING: partial graph (file cap was hit during scan)")
    if s.get("unresolved_imports"):
        top = list((s.get("unresolved_samples") or {}).items())[:6]
        print("note: %d imports could not be mapped to a file%s"
              % (s["unresolved_imports"],
                 " (" + ", ".join("%s x%d" % kv for kv in top) + ")"
                 if top else ""))
    print()

    print("## packages (depth %d)" % depth)
    rows = sorted(packages.items(), key=lambda kv: -kv[1]["loc"])
    width = max((len(k) for k, _ in rows), default=10)
    for pkg, p in rows[:args.limit]:
        ins = sum(w for (a, b), w in weights.items() if b == pkg)
        outs = sum(w for (a, b), w in weights.items() if a == pkg)
        print("  %-*s  %4d files  %9s LOC   in:%-4d out:%-4d"
              % (width, pkg, p["files"], format(p["loc"], ","), ins, outs))
    if len(rows) > args.limit:
        print("  ... %d more" % (len(rows) - args.limit))
    print()

    # a real entry point looks like one *and* nothing else imports it
    def entry_rank(n):
        return n["entry_score"] + (3 if n["fan_in"] == 0 else 0)

    entries = sorted((n for n in nodes if entry_rank(n) >= 4),
                     key=lambda n: (-entry_rank(n), n["path"]))
    if entries:
        print("## likely entry points")
        for n in entries[:12]:
            print("  %-52s %5d LOC  out:%d"
                  % (n["path"], n["loc"], n["fan_out"]))
        print()

    print("## most depended upon  (read these first)")
    for n in sorted(nodes, key=lambda n: (-n["fan_in"], -n["loc"]))[:12]:
        if n["fan_in"] == 0:
            break
        kinds = ", ".join(sorted({sy["kind"] for sy in n["symbols"]}))
        print("  %-52s in:%-4d %5d LOC  %s"
              % (n["path"], n["fan_in"], n["loc"], kinds))
    print()

    print("## largest files")
    for n in sorted(nodes, key=lambda n: -n["loc"])[:8]:
        print("  %-52s %6d LOC  %d symbols"
              % (n["path"], n["loc"], len(n["symbols"])))
    print()

    out, _ = adjacency(graph)
    comps = sccs([n["path"] for n in nodes], out)
    print("## import cycles: %d" % len(comps))
    for comp in comps[:3]:
        print("  %d files: %s%s" % (len(comp), ", ".join(comp[:4]),
                                    " ..." if len(comp) > 4 else ""))
    print()

    if graph["externals"]:
        print("## top third-party dependencies")
        items = list(graph["externals"].items())[:15]
        print("  " + ", ".join("%s(%d)" % (k, v) for k, v in items))
        print()

    orphans = [n["path"] for n in nodes
               if n["fan_in"] == 0 and n["fan_out"] == 0
               and n["entry_score"] == 0 and n["loc"] > 30]
    if orphans:
        print("## unreferenced files (%d)" % len(orphans))
        for p in orphans[:10]:
            print("  %s  (%d LOC)" % (p, by_path[p]["loc"]))


# --------------------------------------------------------------------------
# mermaid / dot graphs
# --------------------------------------------------------------------------

# identifiers Mermaid parses as syntax rather than as a node name
MERMAID_RESERVED = {
    "graph", "subgraph", "end", "class", "classDef", "click", "style",
    "linkStyle", "direction", "flowchart", "default", "call", "href",
}


def mermaid_id(name):
    ident = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if not ident or ident[0].isdigit() or ident in MERMAID_RESERVED:
        return "n_" + ident
    return ident


def render_mermaid(nodes, edges, labels, direction="LR", clusters=None):
    lines = ["```mermaid", "graph %s" % direction]
    if clusters:
        for cluster, members in clusters.items():
            lines.append('  subgraph %s["%s"]'
                         % (mermaid_id("sg_" + cluster), cluster))
            for m in members:
                lines.append('    %s["%s"]' % (mermaid_id(m), labels[m]))
            lines.append("  end")
    else:
        for n in nodes:
            lines.append('  %s["%s"]' % (mermaid_id(n), labels[n]))
    for a, b, w in edges:
        arrow = "==>" if w >= 8 else "-->"
        lines.append("  %s %s%s %s"
                     % (mermaid_id(a), arrow,
                        "|%d|" % w if w > 1 else "", mermaid_id(b)))
    lines.append("```")
    return "\n".join(lines)


def render_dot(nodes, edges, labels):
    lines = ["digraph codemap {", "  rankdir=LR;",
             '  node [shape=box, style="rounded,filled", fillcolor="#eef2f7",'
             ' fontname="Helvetica"];']
    for n in nodes:
        lines.append('  "%s" [label="%s"];'
                     % (n, labels[n].replace("<br/>", "\\n")))
    for a, b, w in edges:
        lines.append('  "%s" -> "%s" [penwidth=%.1f];'
                     % (a, b, min(1 + w / 4.0, 5)))
    lines.append("}")
    return "\n".join(lines)


def cmd_graph(graph, args):
    if args.focus:
        nodes, edges, labels, clusters = focus_view(graph, args)
    elif args.level == "package":
        nodes, edges, labels, clusters = package_view(graph, args)
    else:
        nodes, edges, labels, clusters = file_view(graph, args)

    if not nodes:
        sys.exit("nothing matched -- check --focus / --level")
    if args.format == "dot":
        print(render_dot(nodes, edges, labels))
    else:
        print(render_mermaid(nodes, edges, labels, args.direction,
                             clusters if args.cluster else None))


def package_view(graph, args):
    depth = args.depth or auto_depth(graph["nodes"])
    packages, weights, _ = aggregate(graph, depth)
    keep = [p for p, _ in sorted(packages.items(),
                                 key=lambda kv: -kv[1]["loc"])][:args.max_nodes]
    keepset = set(keep)
    edges = [(a, b, w) for (a, b), w in weights.items()
             if a in keepset and b in keepset and w >= args.min_weight]
    labels = {p: "%s<br/>%d files - %s LOC"
                 % (p, packages[p]["files"], format(packages[p]["loc"], ","))
              for p in keep}
    return keep, sorted(edges, key=lambda e: -e[2]), labels, None


def file_view(graph, args):
    nodes = graph["nodes"]
    if args.path:
        nodes = [n for n in nodes if n["path"].startswith(args.path)]
    nodes = sorted(nodes, key=lambda n: -(n["fan_in"] * 3 + n["fan_out"]))
    keep = [n["path"] for n in nodes[:args.max_nodes]]
    keepset = set(keep)
    by_path = {n["path"]: n for n in graph["nodes"]}
    edges = [(e["from"], e["to"], 1) for e in graph["edges"]
             if e["from"] in keepset and e["to"] in keepset]
    labels = {p: "%s<br/>%d LOC" % (os.path.basename(p), by_path[p]["loc"])
              for p in keep}
    clusters = defaultdict(list)
    for p in keep:
        clusters[by_path[p]["dir"]].append(p)
    return keep, edges, labels, dict(clusters)


def focus_view(graph, args):
    by_path = {n["path"]: n for n in graph["nodes"]}
    seeds = {p for p in by_path
             if p == args.focus or p.startswith(args.focus.rstrip("/") + "/")}
    if not seeds:
        seeds = {p for p in by_path if args.focus in p}
    if not seeds:
        return [], [], {}, None

    out, inc = adjacency(graph)
    reached, frontier = set(seeds), set(seeds)
    for _ in range(args.hops):
        nxt = set()
        for p in frontier:
            nxt |= set(out.get(p, ()))
            if not args.downstream_only:
                nxt |= set(inc.get(p, ()))
        frontier = nxt - reached
        reached |= frontier
        if not frontier:
            break

    ranked = sorted(reached, key=lambda p: (p not in seeds,
                                            -by_path[p]["fan_in"]))
    keep = ranked[:args.max_nodes]
    keepset = set(keep)
    edges = [(e["from"], e["to"], 1) for e in graph["edges"]
             if e["from"] in keepset and e["to"] in keepset]
    labels = {p: "%s%s<br/>%d LOC"
                 % ("* " if p in seeds else "", os.path.basename(p),
                    by_path[p]["loc"])
              for p in keep}
    clusters = defaultdict(list)
    for p in keep:
        clusters[by_path[p]["dir"]].append(p)
    return keep, edges, labels, dict(clusters)


# --------------------------------------------------------------------------
# cycles / interfaces
# --------------------------------------------------------------------------

def cmd_cycles(graph, args):
    out, _ = adjacency(graph)
    by_path = {n["path"]: n for n in graph["nodes"]}
    comps = sccs([n["path"] for n in graph["nodes"]], out)
    if not comps:
        print("no import cycles between files")
    else:
        print("# file-level cycles: %d" % len(comps))
        for comp in comps[:args.limit]:
            loc = sum(by_path[p]["loc"] for p in comp)
            print("\n## %d files, %s LOC" % (len(comp), format(loc, ",")))
            inside = set(comp)
            for p in comp:
                targets = [t for t in out.get(p, ()) if t in inside]
                print("  %s -> %s" % (p, ", ".join(sorted(set(targets)))))

    depth = args.depth or auto_depth(graph["nodes"])
    _, weights, pkg_of = aggregate(graph, depth)
    pkg_out = defaultdict(list)
    for (a, b) in weights:
        pkg_out[a].append(b)
    pkg_comps = sccs(sorted({pkg_of[n["path"]] for n in graph["nodes"]}),
                     pkg_out)
    print("\n# package-level cycles (depth %d): %d" % (depth, len(pkg_comps)))
    for comp in pkg_comps[:args.limit]:
        print("  " + " <-> ".join(comp))


def cmd_interfaces(graph, args):
    nodes = graph["nodes"]
    if args.path:
        prefix = args.path.rstrip("/") + "/"
        nodes = [n for n in nodes
                 if n["path"] == args.path or n["path"].startswith(prefix)
                 or args.path in n["path"]]
    if not nodes:
        sys.exit("no files matched %s" % args.path)
    nodes = sorted(nodes, key=lambda n: (-n["fan_in"], n["path"]))[:args.limit]

    for n in nodes:
        syms = n["symbols"]
        if args.exported_only:
            syms = [s for s in syms if s.get("exported", True)]
        if not syms and not args.show_empty:
            continue
        print("\n## %s  [%s, %d LOC, in:%d out:%d]"
              % (n["path"], n["lang"], n["loc"], n["fan_in"], n["fan_out"]))
        for s in syms:
            print("  %-9s %s%s  :%d"
                  % (s["kind"], s["name"], s.get("sig", ""), s["line"]))
            for m in s.get("members", [])[:args.members]:
                if not args.exported_only or m.get("exported", True):
                    print("      . %s%s  :%d" % (m["name"], m["sig"], m["line"]))


# --------------------------------------------------------------------------
# interactive HTML
# --------------------------------------------------------------------------

HTML_TEMPLATE = r"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
  :root{--bg:#fbfbfa;--fg:#1c1c1a;--muted:#6b6a66;--line:#dedcd6;
        --panel:#fff;--accent:#3c6ea5;--edge:#c3c1ba;--edge-hi:#3c6ea5}
  @media (prefers-color-scheme:dark){
    :root{--bg:#17171a;--fg:#e8e6e1;--muted:#96948e;--line:#33333a;
          --panel:#1f1f24;--accent:#7fb0e0;--edge:#3d3d45;--edge-hi:#7fb0e0}}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font:13px/1.5 ui-sans-serif,-apple-system,Segoe UI,Roboto,sans-serif}
  #wrap{display:flex;height:100vh;overflow:hidden}
  #stage{flex:1;position:relative;min-width:0}
  svg{width:100%;height:100%;display:block;cursor:grab}
  svg.drag{cursor:grabbing}
  #side{width:330px;flex:none;border-left:1px solid var(--line);
        background:var(--panel);overflow-y:auto;padding:14px 16px}
  #bar{position:absolute;top:12px;left:12px;right:12px;display:flex;gap:8px;
       flex-wrap:wrap;align-items:center;pointer-events:none}
  #bar>*{pointer-events:auto}
  input,select,button{font:inherit;padding:5px 9px;border-radius:6px;
       border:1px solid var(--line);background:var(--panel);color:var(--fg)}
  button{cursor:pointer}
  h1{font-size:14px;margin:0 0 2px}
  h2{font-size:11px;text-transform:uppercase;letter-spacing:.06em;
     color:var(--muted);margin:18px 0 6px;font-weight:600}
  .meta{color:var(--muted);font-size:12px}
  .legend{display:flex;flex-wrap:wrap;gap:5px;margin-top:6px}
  .chip{display:flex;align-items:center;gap:5px;padding:2px 7px;
        border:1px solid var(--line);border-radius:11px;cursor:pointer;
        font-size:11px}
  .chip.off{opacity:.35}
  .sw{width:9px;height:9px;border-radius:50%;flex:none}
  ul{margin:4px 0;padding-left:16px}
  li{margin:2px 0;word-break:break-word}
  code{font:11.5px ui-monospace,SFMono-Regular,Menlo,monospace;
       background:rgba(128,128,128,.13);padding:1px 4px;border-radius:3px}
  .k{color:var(--muted);font-size:11px}
  .hint{color:var(--muted);font-size:11.5px;margin-top:12px}
</style>
<div id="wrap">
  <div id="stage">
    <div id="bar">
      <input id="q" placeholder="filter files..." size="18">
      <select id="mode">
        <option value="file">files</option>
        <option value="pkg">packages</option>
      </select>
      <button id="fit">fit</button>
      <button id="freeze">pause</button>
      <span class="meta" id="count"></span>
    </div>
    <svg id="svg"><defs>
      <marker id="ah" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6"
              markerHeight="6" orient="auto-start-reverse">
        <path d="M0 0 L10 5 L0 10 z" fill="var(--edge)"/></marker>
      <marker id="ahi" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="7"
              markerHeight="7" orient="auto-start-reverse">
        <path d="M0 0 L10 5 L0 10 z" fill="var(--edge-hi)"/></marker>
    </defs><g id="scene"></g></svg>
  </div>
  <div id="side"></div>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);
const svg = document.getElementById('svg');
const scene = document.getElementById('scene');
const side = document.getElementById('side');
const NS = 'http://www.w3.org/2000/svg';
const PALETTE = ['#3c6ea5','#c2703d','#4e8c6a','#9b5a9e','#b0563f','#5f7b9c',
                 '#8a7a3c','#7a5ca8','#3f8b8b','#a8534f','#5b8f45','#8f6b4a',
                 '#4a6fb0','#b8683a','#57937c','#7f5f96'];
const GROUPS = [...new Set(DATA.nodes.map(n => n.pkg))].sort();
const color = g => PALETTE[Math.max(0, GROUPS.indexOf(g)) % PALETTE.length];

let mode = 'file', view = {x:0, y:0, k:1}, frozen = false, selected = null;
let nodes = [], links = [], hidden = new Set(), query = '';
let gEdges, gNodes, settling = 0, userMovedView = false;
const PAD = 18, REACH = 2.4;   // node clearance, and repulsion cutoff factor
let maxR = 26, cell = 160;

function build(){
  if(mode === 'file'){
    nodes = DATA.nodes.map(n => Object.assign({}, n, {id:n.path}));
    links = DATA.edges.map(e => ({s:e[0], t:e[1], w:1}));
  } else {
    const agg = new Map();
    for(const n of DATA.nodes){
      let a = agg.get(n.pkg);
      if(!a){ a = {id:n.pkg, path:n.pkg, pkg:n.pkg, loc:0, files:0,
                   fan_in:0, fan_out:0, syms:[]}; agg.set(n.pkg, a); }
      a.loc += n.loc; a.files++;
    }
    nodes = [...agg.values()];
    const byPath = new Map(DATA.nodes.map(n => [n.path, n.pkg]));
    const w = new Map();
    for(const e of DATA.edges){
      const a = byPath.get(e[0]), b = byPath.get(e[1]);
      if(a && b && a !== b) w.set(a + '\u0000' + b, (w.get(a + '\u0000' + b) || 0) + 1);
    }
    links = [...w].map(([k, v]) => {
      const [s, t] = k.split('\u0000'); return {s, t, w:v};
    });
    const idx = new Map(nodes.map(n => [n.id, n]));
    for(const l of links){ idx.get(l.s).fan_out++; idx.get(l.t).fan_in++; }
  }
  const cap = mode === 'pkg' ? 46 : 26;
  const scale = mode === 'pkg' ? 0.34 : 0.55;
  const R = Math.max(300, Math.sqrt(nodes.length) * 52);
  nodes.forEach((n, i) => {
    const a = i * 2.399963;
    const rad = R * Math.sqrt((i + 1) / nodes.length);
    n.x = Math.cos(a) * rad; n.y = Math.sin(a) * rad;
    n.vx = n.vy = 0;
    n.r = Math.max(4, Math.min(cap, 4 + Math.sqrt(n.loc || 1) * scale));
  });
  maxR = nodes.reduce((m, n) => Math.max(m, n.r), 4);
  cell = Math.max(120, Math.ceil(REACH * (2 * maxR + PAD)));
  draw();
  settling = 150;          // keep re-fitting while the layout spreads out
  userMovedView = false;
  fit();
}

const visible = n => !hidden.has(n.pkg) &&
  (!query || n.path.toLowerCase().includes(query));

function draw(){
  scene.textContent = '';
  gEdges = document.createElementNS(NS, 'g'); scene.appendChild(gEdges);
  gNodes = document.createElementNS(NS, 'g'); scene.appendChild(gNodes);
  const idx = new Map(nodes.map(n => [n.id, n]));
  for(const l of links){
    l.a = idx.get(l.s); l.b = idx.get(l.t);
    if(!l.a || !l.b){ l.el = null; continue; }
    const p = document.createElementNS(NS, 'line');
    p.setAttribute('stroke', 'var(--edge)');
    p.setAttribute('stroke-width', Math.min(1 + l.w * 0.25, 3.5));
    p.setAttribute('marker-end', 'url(#ah)');
    l.el = p; gEdges.appendChild(p);
  }
  for(const n of nodes){
    const g = document.createElementNS(NS, 'g');
    g.style.cursor = 'pointer';
    const c = document.createElementNS(NS, 'circle');
    c.setAttribute('r', n.r);
    c.setAttribute('fill', color(n.pkg));
    c.setAttribute('stroke', 'var(--bg)');
    c.setAttribute('stroke-width', '1.5');
    const t = document.createElementNS(NS, 'text');
    t.textContent = mode === 'pkg' ? n.pkg : n.path.split('/').pop();
    t.setAttribute('font-size', '10');
    t.setAttribute('text-anchor', 'middle');
    t.setAttribute('fill', 'var(--fg)');
    t.setAttribute('dy', -n.r - 4);
    g.append(c, t);
    g.addEventListener('click', ev => { ev.stopPropagation(); select(n); });
    g.addEventListener('pointerdown', ev => startDrag(ev, n));
    n.el = g; n.circle = c; n.label = t;
    gNodes.appendChild(g);
  }
  document.getElementById('count').textContent =
    nodes.length + ' nodes, ' + links.length + ' edges';
  paint();
}

function paint(){
  const nbr = new Set();
  if(selected){
    nbr.add(selected.id);
    for(const l of links){
      if(l.s === selected.id) nbr.add(l.t);
      if(l.t === selected.id) nbr.add(l.s);
    }
  }
  for(const n of nodes){
    const vis = visible(n), on = selected && n.id === selected.id;
    n.el.style.display = vis ? '' : 'none';
    n.el.setAttribute('opacity', selected && !nbr.has(n.id) ? 0.16 : 1);
    n.circle.setAttribute('stroke', on ? 'var(--accent)' : 'var(--bg)');
    n.circle.setAttribute('stroke-width', on ? 3 : 1.5);
    n.label.style.display =
      (n.r > 7 || (selected && nbr.has(n.id)) || query) ? '' : 'none';
  }
  for(const l of links){
    if(!l.el) continue;
    const vis = visible(l.a) && visible(l.b);
    const hot = selected && (l.s === selected.id || l.t === selected.id);
    l.el.style.display = vis ? '' : 'none';
    l.el.setAttribute('stroke', hot ? 'var(--edge-hi)' : 'var(--edge)');
    l.el.setAttribute('marker-end', hot ? 'url(#ahi)' : 'url(#ah)');
    l.el.setAttribute('opacity', selected ? (hot ? 0.95 : 0.07) : 0.5);
  }
}

/* force layout: grid-bucketed repulsion + springs on edges.
   The grid cell is sized so a 3x3 neighbourhood always covers the repulsion
   cutoff, which keeps the sweep O(n) without missing close pairs. */
function step(){
  if(frozen) return;
  const k = 0.045, grid = new Map();
  for(const n of nodes){
    const key = Math.floor(n.x / cell) + ',' + Math.floor(n.y / cell);
    let b = grid.get(key);
    if(!b){ b = []; grid.set(key, b); }
    b.push(n);
  }
  for(const n of nodes){
    const cx = Math.floor(n.x / cell), cy = Math.floor(n.y / cell);
    for(let i = -1; i <= 1; i++) for(let j = -1; j <= 1; j++){
      const bucket = grid.get((cx + i) + ',' + (cy + j));
      if(!bucket) continue;
      for(const m of bucket){
        if(m === n) continue;
        let dx = n.x - m.x, dy = n.y - m.y;
        let d = Math.hypot(dx, dy);
        if(d < 1e-3){
          dx = Math.random() - 0.5; dy = Math.random() - 0.5; d = 0.5;
        }
        const rest = n.r + m.r + PAD;
        if(d > rest * REACH) continue;
        const ratio = rest / d;
        const push = Math.min(ratio * ratio * 0.9, 12);
        n.vx += dx / d * push; n.vy += dy / d * push;
        if(d < rest * 0.75 && !n.fixed){        // hard separation
          const fix = (rest * 0.75 - d) * 0.5;
          n.x += dx / d * fix; n.y += dy / d * fix;
        }
      }
    }
    n.vx -= n.x * 0.0016; n.vy -= n.y * 0.0016;
  }
  for(const l of links){
    if(!l.a || !l.b) continue;
    const dx = l.b.x - l.a.x, dy = l.b.y - l.a.y;
    const d = Math.hypot(dx, dy) || 1, target = 60 + l.a.r + l.b.r;
    const f = (d - target) / d * k;
    if(!l.a.fixed){ l.a.vx += dx * f; l.a.vy += dy * f; }
    if(!l.b.fixed){ l.b.vx -= dx * f; l.b.vy -= dy * f; }
  }
  for(const n of nodes){
    if(n.fixed) continue;
    n.vx *= 0.82; n.vy *= 0.82;
    n.x += Math.max(-18, Math.min(18, n.vx));
    n.y += Math.max(-18, Math.min(18, n.vy));
  }
  if(settling > 0){
    settling--;
    if(!userMovedView && settling % 10 === 0){ fit(); return; }
  }
  render();
}

function render(){
  for(const n of nodes)
    n.el.setAttribute('transform',
      'translate(' + n.x.toFixed(1) + ',' + n.y.toFixed(1) + ')');
  for(const l of links){
    if(!l.el) continue;
    const dx = l.b.x - l.a.x, dy = l.b.y - l.a.y, d = Math.hypot(dx, dy) || 1;
    l.el.setAttribute('x1', l.a.x + dx / d * l.a.r);
    l.el.setAttribute('y1', l.a.y + dy / d * l.a.r);
    l.el.setAttribute('x2', l.b.x - dx / d * (l.b.r + 5));
    l.el.setAttribute('y2', l.b.y - dy / d * (l.b.r + 5));
  }
  scene.setAttribute('transform',
    'translate(' + view.x + ',' + view.y + ') scale(' + view.k + ')');
}

function fit(){
  const vis = nodes.filter(visible);
  if(!vis.length) return;
  const xs = vis.map(n => n.x), ys = vis.map(n => n.y);
  const w = svg.clientWidth || 900, h = svg.clientHeight || 600;
  const bw = Math.max(...xs) - Math.min(...xs) + 160;
  const bh = Math.max(...ys) - Math.min(...ys) + 160;
  view.k = Math.max(0.08, Math.min(2, Math.min(w / bw, h / bh)));
  view.x = w / 2 - (Math.min(...xs) + Math.max(...xs)) / 2 * view.k;
  view.y = h / 2 - (Math.min(...ys) + Math.max(...ys)) / 2 * view.k;
  render();
}

let panning = null, dragging = null;
svg.addEventListener('pointerdown', e => {
  if(dragging) return;
  panning = {x:e.clientX - view.x, y:e.clientY - view.y};
  userMovedView = true;
  svg.classList.add('drag');
});
addEventListener('pointermove', e => {
  if(dragging){
    const r = svg.getBoundingClientRect();
    dragging.n.x = (e.clientX - r.left - view.x) / view.k;
    dragging.n.y = (e.clientY - r.top - view.y) / view.k;
    render();
  } else if(panning){
    view.x = e.clientX - panning.x; view.y = e.clientY - panning.y; render();
  }
});
addEventListener('pointerup', () => {
  if(dragging){ dragging.n.fixed = false; dragging = null; }
  panning = null; svg.classList.remove('drag');
});
function startDrag(e, n){ e.stopPropagation(); n.fixed = true; dragging = {n}; }

svg.addEventListener('wheel', e => {
  e.preventDefault();
  const r = svg.getBoundingClientRect();
  const mx = e.clientX - r.left, my = e.clientY - r.top;
  userMovedView = true;
  const f = Math.exp(-e.deltaY * 0.0016);
  const k = Math.max(0.05, Math.min(4, view.k * f));
  view.x = mx - (mx - view.x) * (k / view.k);
  view.y = my - (my - view.y) * (k / view.k);
  view.k = k; render();
}, {passive:false});
svg.addEventListener('click', () => { selected = null; paint(); info(); });

document.getElementById('q').addEventListener('input', e => {
  query = e.target.value.toLowerCase().trim(); paint();
});
document.getElementById('fit').addEventListener('click', () => {
  userMovedView = false; settling = Math.max(settling, 30); fit();
});
document.getElementById('freeze').addEventListener('click', e => {
  frozen = !frozen; e.target.textContent = frozen ? 'resume' : 'pause';
});
document.getElementById('mode').addEventListener('change', e => {
  mode = e.target.value; selected = null; build(); info();
});

const esc = s => String(s).replace(/[&<>]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

function select(n){ selected = n; paint(); info(); }

function info(){
  if(!selected){
    const top = [...DATA.nodes].sort((a, b) => b.fan_in - a.fan_in).slice(0, 12);
    side.innerHTML =
      '<h1>' + esc(DATA.name) + '</h1><div class="meta">' +
      DATA.stats.files + ' files &middot; ' + DATA.stats.loc.toLocaleString() +
      ' LOC &middot; ' + DATA.stats.edges + ' edges</div>' +
      '<h2>packages</h2><div class="legend">' +
      GROUPS.map(g => '<span class="chip' + (hidden.has(g) ? ' off' : '') +
        '" data-g="' + esc(g) + '"><span class="sw" style="background:' +
        color(g) + '"></span>' + esc(g) + '</span>').join('') + '</div>' +
      '<h2>most depended upon</h2><ul>' +
      top.map(n => '<li><code>' + esc(n.path) + '</code> <span class="k">in ' +
        n.fan_in + '</span></li>').join('') + '</ul>' +
      '<div class="hint">Click a node for its interface. Drag nodes, scroll ' +
      'to zoom, click empty space to clear the selection.</div>';
    side.querySelectorAll('.chip').forEach(el => {
      el.onclick = () => {
        const g = el.dataset.g;
        hidden.has(g) ? hidden.delete(g) : hidden.add(g);
        paint(); info();
      };
    });
    return;
  }
  const n = selected;
  const ins = links.filter(l => l.t === n.id).map(l => l.s).sort();
  const outs = links.filter(l => l.s === n.id).map(l => l.t).sort();
  const syms = n.syms || [];
  side.innerHTML =
    '<h1>' + esc(mode === 'pkg' ? n.pkg : n.path.split('/').pop()) + '</h1>' +
    '<div class="meta"><code>' + esc(n.path) + '</code></div>' +
    '<div class="meta" style="margin-top:6px">' +
      (mode === 'pkg' ? n.files + ' files &middot; ' : esc(n.lang) + ' &middot; ') +
      n.loc.toLocaleString() + ' LOC</div>' +
    (syms.length ? '<h2>declares</h2><ul>' + syms.map(s =>
      '<li><span class="k">' + esc(s.kind) + '</span> <code>' + esc(s.name) +
      esc(s.sig || '') + '</code> <span class="k">:' + s.line +
      '</span></li>').join('') + '</ul>' : '') +
    '<h2>depends on (' + outs.length + ')</h2><ul>' +
      (outs.slice(0, 40).map(p => '<li><code>' + esc(p) + '</code></li>').join('')
       || '<li class="k">nothing internal</li>') + '</ul>' +
    '<h2>used by (' + ins.length + ')</h2><ul>' +
      (ins.slice(0, 40).map(p => '<li><code>' + esc(p) + '</code></li>').join('')
       || '<li class="k">nothing internal</li>') + '</ul>';
}

build();
info();
addEventListener('resize', fit);
setInterval(step, 1000 / 45);
</script>
"""


def cmd_html(graph, args):
    depth = args.depth or auto_depth(graph["nodes"])
    known = {n["path"] for n in graph["nodes"]}
    nodes = []
    for n in graph["nodes"]:
        syms = [s for s in n["symbols"] if s.get("exported", True)]
        nodes.append({
            "path": n["path"],
            "pkg": package_of(n["path"], depth),
            "lang": n["lang"],
            "loc": n["loc"],
            "fan_in": n["fan_in"],
            "fan_out": n["fan_out"],
            "syms": [{"kind": s["kind"], "name": s["name"],
                      "sig": (s.get("sig") or "")[:70], "line": s["line"]}
                     for s in syms[:40]],
        })
    payload = {
        "name": graph["name"],
        "stats": graph["stats"],
        "nodes": nodes,
        "edges": [[e["from"], e["to"]] for e in graph["edges"]
                  if e["from"] in known and e["to"] in known],
    }
    blob = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    page = (HTML_TEMPLATE
            .replace("__TITLE__", html_mod.escape(graph["name"] + " code map"))
            .replace("__DATA__", blob))
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(page)
    print("wrote %s (%d nodes, %d edges, %.0f KB)"
          % (args.out, len(nodes), len(payload["edges"]), len(page) / 1024.0))


# --------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("-m", "--map", default="codemap.json",
                    help="codemap.json path (default: codemap.json)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("summary", help="overview of the codebase")
    p.add_argument("--depth", type=int, help="package depth (default: auto)")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_summary)

    p = sub.add_parser("graph", help="Mermaid/DOT dependency diagram")
    p.add_argument("--level", choices=["package", "file"], default="package")
    p.add_argument("--focus", help="file or directory to centre on")
    p.add_argument("--path", help="restrict file-level graph to this prefix")
    p.add_argument("--hops", type=int, default=1, help="hops around --focus")
    p.add_argument("--downstream-only", action="store_true",
                   help="follow only outgoing edges from --focus")
    p.add_argument("--depth", type=int, help="package depth (default: auto)")
    p.add_argument("--max-nodes", type=int, default=40)
    p.add_argument("--min-weight", type=int, default=1)
    p.add_argument("--direction", default="LR", choices=["LR", "TB", "RL", "BT"])
    p.add_argument("--format", default="mermaid", choices=["mermaid", "dot"])
    p.add_argument("--cluster", action="store_true",
                   help="wrap file nodes in directory subgraphs")
    p.set_defaults(fn=cmd_graph)

    p = sub.add_parser("cycles", help="import cycles")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--depth", type=int)
    p.set_defaults(fn=cmd_cycles)

    p = sub.add_parser("interfaces", help="declared symbols per file")
    p.add_argument("--path", help="file or directory prefix")
    p.add_argument("--limit", type=int, default=25, help="max files")
    p.add_argument("--members", type=int, default=12, help="max class members")
    p.add_argument("--exported-only", action="store_true")
    p.add_argument("--show-empty", action="store_true")
    p.set_defaults(fn=cmd_interfaces)

    p = sub.add_parser("html", help="self-contained interactive graph")
    p.add_argument("-o", "--out", default="codemap.html")
    p.add_argument("--depth", type=int)
    p.set_defaults(fn=cmd_html)

    args = ap.parse_args(argv)
    args.fn(load(args.map), args)


if __name__ == "__main__":
    main()
