#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -d .git ]]; then
  echo "[autosync] not a git repository: $REPO_ROOT" >&2
  exit 1
fi

INTERVAL="${AUTOSYNC_INTERVAL:-5}"
COMMIT_PREFIX="${AUTOSYNC_COMMIT_PREFIX:-autosync}"
REMOTE_NAME="${AUTOSYNC_REMOTE:-origin}"
ALLOWED_BRANCHES="${AUTOSYNC_BRANCHES:-main}"
STATE_DIR="${AUTOSYNC_STATE_DIR:-$REPO_ROOT/.git/autosync}"
PID_FILE="$STATE_DIR/watch.pid"
LOG_FILE="$STATE_DIR/watch.log"

mkdir -p "$STATE_DIR"

log() {
  echo "[autosync] $*"
}

current_branch() {
  git rev-parse --abbrev-ref HEAD
}

is_branch_allowed() {
  local branch="$1"
  local item
  IFS=',' read -r -a items <<< "$ALLOWED_BRANCHES"
  for item in "${items[@]}"; do
    item="${item//[[:space:]]/}"
    if [[ -n "$item" && "$branch" == "$item" ]]; then
      return 0
    fi
  done
  return 1
}

is_ignored_change() {
  local path="$1"
  [[ "$path" == ".DS_Store" ||
     "$path" == */".DS_Store" ||
     "$path" == *__pycache__/* ||
     "$path" == *.pyc ||
     "$path" == *.pyo ||
     "$path" == *.tmp ||
     "$path" == *.swp ]]
}

is_git_busy() {
  [[ -f .git/MERGE_HEAD || -d .git/rebase-merge || -d .git/rebase-apply || -f .git/CHERRY_PICK_HEAD || -f .git/REVERT_HEAD || -f .git/BISECT_LOG ]]
}

upstream_exists() {
  git rev-parse --abbrev-ref --symbolic-full-name "@{u}" >/dev/null 2>&1
}

sync_with_remote_if_needed() {
  local behind ahead counts

  if ! upstream_exists; then
    return 0
  fi

  git fetch --quiet "$REMOTE_NAME" "$(current_branch)"
  counts="$(git rev-list --left-right --count "@{u}...HEAD")"
  behind="${counts%% *}"
  ahead="${counts##* }"

  if [[ "$behind" -gt 0 ]]; then
    log "remote has $behind new commit(s); rebasing local branch"
    if ! git rebase "@{u}"; then
      log "rebase failed; aborting this cycle"
      git rebase --abort >/dev/null 2>&1 || true
      return 1
    fi
  fi

  # ahead value is informational; push handles final sync.
  if [[ "$ahead" -gt 0 ]]; then
    log "local branch has $ahead commit(s) ready to push"
  fi

  return 0
}

push_with_upstream_if_needed() {
  local branch
  branch="$(current_branch)"

  if [[ "$branch" == "HEAD" ]]; then
    log "detached HEAD - skipping push"
    return 0
  fi

  if upstream_exists; then
    git push
  else
    git push -u "$REMOTE_NAME" "$branch"
  fi
}

stage_selected_changes() {
  local changed=()
  local path

  while IFS= read -r -d '' path; do
    if is_ignored_change "$path"; then
      continue
    fi
    changed+=("$path")
  done < <(git -c core.quotepath=false ls-files -z -m -o -d --exclude-standard)

  if [[ ${#changed[@]} -eq 0 ]]; then
    return 1
  fi

  git add -- "${changed[@]}"
  return 0
}

commit_and_push_if_dirty() {
  local branch
  branch="$(current_branch)"

  if [[ "$branch" == "HEAD" ]]; then
    log "detached HEAD - skipping cycle"
    return 0
  fi

  if ! is_branch_allowed "$branch"; then
    log "branch '$branch' is not in AUTOSYNC_BRANCHES='$ALLOWED_BRANCHES' - skipping cycle"
    return 0
  fi

  if [[ -z "$(git status --porcelain --untracked-files=all)" ]]; then
    return 0
  fi

  if ! stage_selected_changes; then
    return 0
  fi

  if git diff --cached --quiet; then
    return 0
  fi

  local stamp msg
  stamp="$(date '+%Y-%m-%d %H:%M:%S')"
  msg="$COMMIT_PREFIX: $stamp"

  log "commit: $msg"
  git commit -m "$msg"

  if ! sync_with_remote_if_needed; then
    return 1
  fi

  push_with_upstream_if_needed
  log "pushed"
}

watch_loop() {
  trap 'log "stop requested"; exit 0' INT TERM
  log "watching $REPO_ROOT (interval=${INTERVAL}s, branches=${ALLOWED_BRANCHES})"
  while true; do
    if is_git_busy; then
      log "git busy (merge/rebase/cherry-pick) - skipping cycle"
    else
      if ! commit_and_push_if_dirty; then
        log "cycle failed - will retry"
      fi
    fi
    sleep "$INTERVAL"
  done
}

is_running() {
  if [[ ! -f "$PID_FILE" ]]; then
    return 1
  fi

  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -z "$pid" ]]; then
    return 1
  fi

  if kill -0 "$pid" >/dev/null 2>&1; then
    return 0
  fi

  rm -f "$PID_FILE"
  return 1
}

start_daemon() {
  if is_running; then
    log "already running (pid $(cat "$PID_FILE"))"
    return 0
  fi

  touch "$LOG_FILE"
  nohup "$0" watch >>"$LOG_FILE" 2>&1 &
  local pid=$!
  echo "$pid" >"$PID_FILE"
  log "started (pid $pid)"
  log "log: $LOG_FILE"
}

stop_daemon() {
  if ! is_running; then
    log "not running"
    rm -f "$PID_FILE"
    return 0
  fi

  local pid
  pid="$(cat "$PID_FILE")"
  kill "$pid" >/dev/null 2>&1 || true
  rm -f "$PID_FILE"
  log "stopped"
}

status_daemon() {
  if is_running; then
    log "running (pid $(cat "$PID_FILE"))"
    log "log: $LOG_FILE"
  else
    log "not running"
  fi
}

case "${1:-watch}" in
  start)
    start_daemon
    ;;
  stop)
    stop_daemon
    ;;
  restart)
    stop_daemon
    start_daemon
    ;;
  status)
    status_daemon
    ;;
  watch)
    watch_loop
    ;;
  once)
    if is_git_busy; then
      log "git busy (merge/rebase/cherry-pick) - skipping"
      exit 0
    fi
    commit_and_push_if_dirty
    ;;
  *)
    echo "Usage: $0 [start|stop|restart|status|watch|once]"
    exit 2
    ;;
esac
