# skills

Personal Claude Code skills, kept in one place so any of them can be loaded
into any environment on demand.

Each skill is a self-contained directory under `skills/`. Nothing here depends
on anything else in the repo — a skill can be symlinked, copied, installed as a
plugin, or dropped into a project, and it will work the same way.

## Catalogue

| Skill | What it does |
|---|---|
| [`codemap`](skills/codemap/) | Maps an unfamiliar codebase as a dependency graph — packages, interfaces, hubs, entry points, import cycles — as terminal reports, Mermaid diagrams, or a self-contained interactive HTML graph. Python, TS/JS, Go, Rust, JVM, C/C++. |

## Installing

Four ways in, depending on how permanent you want it to be.

**1. Symlink into your user skills directory** — available in every project,
and edits to this repo take effect immediately.

```bash
./install.sh
```

`./install.sh --list` shows what is available and what is already installed.
`./install.sh codemap` installs one. `./install.sh --uninstall` removes the
links again. Start a new Claude Code session afterwards.

**2. Install into a single project** — when only one repo should see it.

```bash
./install.sh --project
```

Writes into `./.claude/skills/`. Commit it to share the skill with the team.

**3. As a plugin** — the whole repo is also a plugin marketplace, so a machine
without this checkout can pull it from git:

```bash
/plugin marketplace add <your-git-remote>
/plugin install artem-skills@artem-skills
```

**4. Copy it** — for a container, a CI image, or anywhere the repo will not be
present:

```bash
./install.sh --copy --dest /path/to/.claude/skills
```

## Using a skill

Skills are model-invoked: Claude reads the `description` in each `SKILL.md` and
loads the skill when a request matches. You do not have to name it. Asking
"what does this repo do, where should I start reading?" is enough to pull in
`codemap`.

To force it, name it: *"use the codemap skill on ./services/api"*.

## Layout

```
.claude-plugin/
  plugin.json          this repo as an installable plugin
  marketplace.json     this repo as a plugin marketplace
skills/
  codemap/
    SKILL.md           frontmatter + instructions (the only required file)
    scripts/           executable helpers, stdlib only
    references/        detail Claude loads on demand, not up front
docs/
  skill-template/      starting point for a new skill
CONVENTIONS.md         how skills in this repo are written
install.sh             symlink / copy skills into a skills directory
```

## Adding a skill

```bash
cp -R docs/skill-template skills/my-skill
$EDITOR skills/my-skill/SKILL.md
./install.sh my-skill
```

Then read [CONVENTIONS.md](CONVENTIONS.md) — the description field is what
decides whether the skill ever gets used, and it is worth getting right.

## Licence

Apache 2.0. See [LICENSE](LICENSE).
