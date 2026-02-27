#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${OSX_NEXT_REPO_URL:-https://github.com/lucid-fabrics/osx-proxmox-next.git}"
REPO_DIR="${OSX_NEXT_REPO_DIR:-/root/osx-proxmox-next}"
REPO_BRANCH="${OSX_NEXT_BRANCH:-main}"
VENV_DIR="${OSX_NEXT_VENV_DIR:-$REPO_DIR/.venv}"
LOG_FILE="${OSX_NEXT_LOG_FILE:-/root/osx-proxmox-next-install.log}"

log() {
  local msg="$1"
  echo "$msg" | tee -a "$LOG_FILE"
}

die() {
  log "ERROR: $1"
  exit 1
}

require_root() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    die "Run as root."
  fi
}

install_dependencies() {
  log "Installing dependencies..."
  apt-get update >>"$LOG_FILE" 2>&1 || die "apt-get update failed"
  apt-get install -y git python3 python3-venv python3-pip >>"$LOG_FILE" 2>&1 || die "Failed to install packages"
}

sync_repo() {
  if [[ -d "$REPO_DIR/.git" ]]; then
    log "Updating existing repository..."
    git -C "$REPO_DIR" fetch origin >>"$LOG_FILE" 2>&1 || die "git fetch failed"
    git -C "$REPO_DIR" checkout "$REPO_BRANCH" >>"$LOG_FILE" 2>&1 || die "git checkout failed"
    git -C "$REPO_DIR" reset --hard "origin/$REPO_BRANCH" >>"$LOG_FILE" 2>&1 || die "git reset failed"
  else
    log "Cloning repository..."
    git clone "$REPO_URL" "$REPO_DIR" >>"$LOG_FILE" 2>&1 || die "git clone failed"
    git -C "$REPO_DIR" checkout "$REPO_BRANCH" >>"$LOG_FILE" 2>&1 || die "git checkout failed"
  fi
  # Purge stale bytecode so Python imports fresh source
  find "$REPO_DIR" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
}

setup_runtime() {
  log "Setting up runtime..."
  python3 -m venv "$VENV_DIR" >>"$LOG_FILE" 2>&1 || die "venv creation failed"
  # shellcheck source=/dev/null
  source "$VENV_DIR/bin/activate"
  pip install --upgrade pip >>"$LOG_FILE" 2>&1 || die "pip upgrade failed"
  pip install --force-reinstall --no-deps -e "$REPO_DIR" >>"$LOG_FILE" 2>&1 || die "editable install failed"
  pip install -e "$REPO_DIR" >>"$LOG_FILE" 2>&1 || die "dependency install failed"
}

launch() {
  log "Launching osx-next TUI..."
  # shellcheck source=/dev/null
  source "$VENV_DIR/bin/activate"
  exec osx-next
}

nuke_stale() {
  # Remove previous install to prevent stale code/venv from persisting
  if [[ -d "$REPO_DIR" ]]; then
    log "Removing previous install..."
    rm -rf "$REPO_DIR"
  fi
}

main() {
  require_root
  nuke_stale
  install_dependencies
  sync_repo
  setup_runtime
  launch
}

main "$@"
