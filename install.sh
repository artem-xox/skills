#!/bin/sh
# Link (or copy) skills from this repo into a Claude Code skills directory.
#
#   ./install.sh                  link every skill into ~/.claude/skills
#   ./install.sh codemap          link one skill
#   ./install.sh --project        link into ./.claude/skills of the current dir
#   ./install.sh --dest DIR       link into an explicit directory
#   ./install.sh --copy codemap   copy instead of symlink, for machines that
#                                 will not have this repo checked out
#   ./install.sh --list           show what is available and what is installed
#   ./install.sh --uninstall      remove links this script created
#
# POSIX sh: works with macOS /bin/sh, bash and zsh alike.
set -eu

REPO=$(cd "$(dirname "$0")" && pwd)
SRC="$REPO/skills"
DEST="${HOME}/.claude/skills"
MODE=link
ACTION=install
SELECTED=""

while [ $# -gt 0 ]; do
  case "$1" in
    --project)   DEST="$PWD/.claude/skills" ;;
    --dest)      shift; DEST="${1:?--dest needs a path}" ;;
    --copy)      MODE=copy ;;
    --list)      ACTION=list ;;
    --uninstall) ACTION=uninstall ;;
    -h|--help)   sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)          echo "unknown option: $1" >&2; exit 2 ;;
    *)           SELECTED="$SELECTED $1" ;;
  esac
  shift
done

available() {
  for dir in "$SRC"/*/; do
    [ -f "$dir/SKILL.md" ] && basename "$dir"
  done
}

describe() {
  sed -n 's/^description: //p' "$SRC/$1/SKILL.md" | head -1 | cut -c1-64
}

if [ "$ACTION" = list ]; then
  printf '%-22s %-10s %s\n' SKILL STATE DESCRIPTION
  available | while read -r name; do
    if   [ -L "$DEST/$name" ]; then state=linked
    elif [ -e "$DEST/$name" ]; then state=present
    else                            state=-
    fi
    printf '%-22s %-10s %s\n' "$name" "$state" "$(describe "$name")"
  done
  printf '\ndestination: %s\n' "$DEST"
  exit 0
fi

[ -n "$SELECTED" ] || SELECTED=$(available | tr '\n' ' ')

mkdir -p "$DEST"

for name in $SELECTED; do
  src="$SRC/$name"
  dst="$DEST/$name"

  if [ ! -f "$src/SKILL.md" ]; then
    echo "skip $name: no SKILL.md in $src" >&2
    continue
  fi

  if [ "$ACTION" = uninstall ]; then
    if [ -L "$dst" ]; then
      rm "$dst"
      echo "unlinked $name"
    elif [ -e "$dst" ]; then
      echo "skip $name: $dst is not a symlink, remove it by hand" >&2
    fi
    continue
  fi

  if [ -L "$dst" ]; then
    rm "$dst"
  elif [ -e "$dst" ]; then
    echo "skip $name: $dst exists and is not a symlink" >&2
    continue
  fi

  if [ "$MODE" = copy ]; then
    cp -R "$src" "$dst"
    echo "copied  $name -> $dst"
  else
    ln -s "$src" "$dst"
    echo "linked  $name -> $dst"
  fi
done

[ "$ACTION" = install ] &&
  printf '\nStart a new Claude Code session to pick up the changes.\n'
exit 0
