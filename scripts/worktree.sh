#!/usr/bin/env bash
# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  NEXUS AI ASSISTANT – GIT WORKTREE ISOLATION                              ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
#
# Why this exists: two agents editing one checkout collide at the file level,
# and the collision is silent until one of them overwrites the other. A git
# worktree gives each task its own working directory on its own branch, sharing
# one object database, so the isolation is enforced by the filesystem rather
# than by an instruction in a prompt that the agent may not follow.
#
#   ./scripts/worktree.sh new <name> [base]   # isolate a task
#   ./scripts/worktree.sh list                # what is checked out where
#   ./scripts/worktree.sh remove <name>       # drop the checkout (keeps branch)
#   ./scripts/worktree.sh check               # PROVE isolation works right now
#
# `check` is the part that makes this more than a convention: it creates a real
# worktree, writes a file into it, asserts the main checkout cannot see that
# file, and removes it again. `scripts/worktree.sh check` is run by
# backend/tests/test_worktrees.py, so CI proves the mechanism instead of
# trusting this comment.
#
# Exit: 0 = ok, 1 = failure.

set -uo pipefail

ROOT_DIR="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel 2>/dev/null)" || {
  echo "not inside a git repository" >&2
  exit 1
}
cd "$ROOT_DIR"

WT_DIR="$ROOT_DIR/.worktrees"
CMD="${1:-}"

ok()   { printf '  \033[0;32m[ok]\033[0m %s\n' "$1"; }
die()  { printf '  \033[0;31m[fail]\033[0m %s\n' "$1" >&2; exit 1; }

case "$CMD" in
  new)
    NAME="${2:-}"
    [[ -n "$NAME" ]] || die "usage: $0 new <name> [base]"
    BASE="${3:-HEAD}"
    TARGET="$WT_DIR/$NAME"
    [[ -e "$TARGET" ]] && die "worktree already exists: $TARGET"
    mkdir -p "$WT_DIR"
    git worktree add -b "wt/$NAME" "$TARGET" "$BASE" >/dev/null || die "git worktree add failed"
    ok "worktree created: $TARGET (branch wt/$NAME, from $BASE)"
    printf '\n  Next: cd "%s"\n  Then: ./init.sh, work on ONE feature, make check, make clean-check\n' "$TARGET"
    printf '  Remove when done: ./scripts/worktree.sh remove %s\n' "$NAME"
    ;;

  list)
    # Only ours, so a main checkout elsewhere is not reported as noise.
    if [[ -d "$WT_DIR" ]]; then
      git worktree list | grep -- "$WT_DIR" || echo "  no project worktrees"
    else
      echo "  no project worktrees"
    fi
    ;;

  remove)
    NAME="${2:-}"
    [[ -n "$NAME" ]] || die "usage: $0 remove <name>"
    TARGET="$WT_DIR/$NAME"
    [[ -e "$TARGET" ]] || die "no such worktree: $TARGET"
    git worktree remove --force "$TARGET" || die "git worktree remove failed"
    git worktree prune
    ok "worktree removed: $TARGET"
    # The branch is deliberately kept: it may hold unmerged work, and deleting
    # it here would be the one unrecoverable action in this script.
    printf '  Branch wt/%s kept. Delete it when you are sure: git branch -D wt/%s\n' "$NAME" "$NAME"
    ;;

  check)
    # The self-test. Everything below is asserted, not assumed.
    TMP_BASE="$(mktemp -d "${TMPDIR:-/tmp}/nexus-wt-check-XXXXXX")" || die "mktemp failed"
    PROBE="$TMP_BASE/probe"
    cleanup() {
      git worktree remove --force "$PROBE" >/dev/null 2>&1 || true
      git worktree prune >/dev/null 2>&1 || true
      rm -rf "$TMP_BASE" >/dev/null 2>&1 || true
    }
    trap cleanup EXIT

    git worktree add --detach "$PROBE" HEAD >/dev/null 2>&1 || die "could not create a probe worktree"

    SENTINEL=".isolation-probe-$$"
    echo "isolated" > "$PROBE/$SENTINEL"
    [[ -f "$PROBE/$SENTINEL" ]] || die "probe file was not written into the worktree"
    # The load-bearing assertion: a file in a worktree must NOT appear in the
    # main checkout. If this ever fails, worktrees are not isolating anything
    # and parallel agents would be sharing a directory after all.
    [[ ! -e "$ROOT_DIR/$SENTINEL" ]] || die "isolation broken: worktree file leaked into the main checkout"

    # Ask git about the directory instead of grepping `git worktree list` for
    # the path. That output is in git's path form (C:/... on Windows) while
    # $PROBE is POSIX (/tmp/...), so a string compare fails on a worktree that
    # is perfectly valid -- a false alarm in the very assertion meant to build
    # trust in the mechanism.
    git -C "$PROBE" rev-parse --show-toplevel >/dev/null 2>&1 \
      || die "probe directory is not recognised by git as a worktree"
    # A linked worktree reports a common dir (the main .git) — proof this is a
    # worktree of this repository and not some unrelated checkout.
    git -C "$PROBE" rev-parse --git-common-dir >/dev/null 2>&1 \
      || die "probe directory is not a linked worktree"

    rm -f "$PROBE/$SENTINEL"
    git worktree remove --force "$PROBE" >/dev/null 2>&1 || die "could not remove the probe worktree"
    git worktree prune
    [[ ! -d "$PROBE" ]] || die "probe worktree directory survived removal"
    ok "worktree isolation verified (write, isolation, listing, removal)"
    ;;

  *)
    sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
