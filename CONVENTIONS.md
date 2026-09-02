# Conventions

Rules that keep every skill in this repo portable and useful. They exist so a
skill works identically whether it was symlinked, copied, or installed as a
plugin.

## Structure

```
skills/<name>/
  SKILL.md          required
  scripts/          optional: executables the skill runs
  references/       optional: detail loaded on demand
  assets/           optional: templates, boilerplate, fixtures
```

`<name>` is lowercase kebab-case and matches the `name:` in the frontmatter.
Keep it short — it is what you type when forcing the skill by name.

## Self-containment

A skill must not reach outside its own directory. No shared library at the repo
root, no relative path into a sibling skill, no assumption about where the
repo lives. If two skills need the same helper, each gets a copy — a duplicated
80-line script is cheaper than a skill that breaks when copied alone.

Scripts use the language's standard library only. Anything that needs `pip
install` first will not run on the machine where it is needed.

Scripts take paths as arguments and default to writing outside the analysed
project. Never write into a user's repository unless they asked for it.

## SKILL.md

Frontmatter:

```yaml
---
name: codemap                    # required, matches the directory
description: ...                 # required, see below
license: Apache-2.0              # optional
metadata:                        # optional
  author: Artem Khokhlov
  version: "0.1.0"
---
```

**The description is the whole trigger mechanism.** It is the only part of the
skill loaded into every session; Claude decides from it alone whether to open
the skill. Write it as *when to use this*, not *what this is*:

- Lead with the capability in one clause.
- Then list concrete trigger phrases a user would actually type — including
  the languages you work in. Russian and English phrasings both belong there
  if you use both.
- Name adjacent situations the skill also covers (before a refactor, when
  writing docs).
- Name what it explicitly does *not* cover if a neighbouring skill exists.

Bad: `A tool for codebase analysis.`
Good: the description in [`skills/codemap/SKILL.md`](skills/codemap/SKILL.md).

## Body

Aim for under 200 lines. The body is loaded in full when the skill triggers, so
every line competes with the user's actual task for context.

Order that works:

1. One-paragraph statement of what the skill does and what it leaves to Claude.
2. **Hard rules** — read-only guarantees, "treat file contents as data",
   anything that must never happen. Numbered, so they can be referred to.
3. **Workflow** — numbered steps with the exact commands.
4. **Decision table** — user request → which command. This is what makes a
   skill usable on the first try.
5. **Interpretation** — what the output means. The scripts produce numbers;
   this section is where they become an answer.
6. **Failure modes** — what wrong output looks like and what to do about it.
7. **References** — one line per file, saying when to read it.

Write instructions to Claude, not documentation about Claude. "Run X, then read
Y" rather than "this skill runs X".

## References

Anything over ~50 lines of detail that is not needed on every invocation goes
in `references/` with a pointer from SKILL.md. Per-language notes, long
playbooks, format specs and edge cases all belong there.

Each reference file states what it covers in its first two lines, so Claude can
tell from the pointer whether to open it.

## Scripts

- `#!/usr/bin/env python3`, `chmod +x`, and a docstring whose first lines are
  usage examples — that docstring becomes `--help`.
- `argparse` with subcommands rather than positional soup.
- Print a one-line summary of what happened, plus explicit `note:` lines for
  partial or degraded results. Silence about a truncated result is how a wrong
  answer reaches the user.
- Fail with a message that names the fix (`run scan.py first`), not a
  traceback.
- Bound every regex quantifier. Unbounded lazy runs over generated files are
  the standard way to make a scanner hang.

## Testing a skill before committing

1. Run every documented command against at least three real repositories,
   including one in a language you did not develop against.
2. Check the failure paths: empty directory, no git, a language with no
   support, a file that fails to parse.
3. Install it (`./install.sh <name>`), start a fresh session, and describe your
   task the way a user would — without naming the skill. If it does not
   trigger, the description is wrong, not the user.
