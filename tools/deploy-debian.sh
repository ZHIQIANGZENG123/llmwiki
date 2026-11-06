#!/usr/bin/env bash
# Deploy and run LLM Wiki on an air-gapped Debian 12 (bookworm) host.
#
# Everything this needs is inside the bundle (built by tools/build-bundle-linux.sh):
# Python wheels, a portable Node runtime and the prebuilt web app. No network, no pip on
# the host, no venv — dependencies are installed into the bundle itself.
#
#   sudo ./deploy.sh --workspace /srv/llmwiki --host 0.0.0.0
#   sudo ./deploy.sh --check
#   sudo ./deploy.sh --install-service --workspace /srv/llmwiki --host 0.0.0.0
#
# The server keeps the API and MCP on loopback and exposes one port, so the workspace
# password is the only thing standing between the LAN and the vault. It is generated on
# the first LAN start and saved to <workspace>/.llmwiki/lan-password.txt.

set -euo pipefail

BUNDLE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="/srv/llmwiki"
HOST="0.0.0.0"
PORT="3000"
PASSWORD="${LLMWIKI_PASSWORD:-}"
READ_PASSWORD="${LLMWIKI_READ_PASSWORD:-}"
SERVICE_USER=""
INSTALL_SERVICE=0
RUN_MODE=0
CHECK_ONLY=0
FORCE=0
PYTHON="${LLMWIKI_PYTHON:-python3}"
REBUILD=0

log()  { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m  ok\033[0m %s\n' "$*"; }
warn() { printf '\033[33m  !!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: deploy.sh [options]

  --workspace DIR      Vault folder (default: /srv/llmwiki)
  --host ADDR          Listen address; 127.0.0.1 keeps it single-machine (default: 0.0.0.0)
  --port N             Web port (default: 3000)
  --password PW        Shared password. Omit to have one generated and saved once.
  --read-password PW   Optional read-only password (browse/search/download only).
  --install-pi         Point the chat bridge at the bundled pi agent (done automatically).
  --python PATH        Python interpreter (default: python3)
  --check              Verify prerequisites and exit
  --install-service    Install, enable and start a systemd unit
  --service-user NAME  Account the service runs as (default: llmwiki when root)
  --rebuild            Force a web rebuild (usually impossible offline — see notes)
  --run                Internal: run in the foreground (used by the systemd unit)
  --force              Continue despite Python minor version mismatch
  -h, --help           Show this help
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --workspace)      WORKSPACE="${2:-}"; shift 2 ;;
    --host)           HOST="${2:-}"; shift 2 ;;
    --port)           PORT="${2:-}"; shift 2 ;;
    --password)       PASSWORD="${2:-}"; shift 2 ;;
    --read-password)  READ_PASSWORD="${2:-}"; shift 2 ;;
    --python)         PYTHON="${2:-}"; shift 2 ;;
    --service-user)   SERVICE_USER="${2:-}"; shift 2 ;;
    --check)          CHECK_ONLY=1; shift ;;
    --install-service) INSTALL_SERVICE=1; shift ;;
    --rebuild)        REBUILD=1; shift ;;
    --run)            RUN_MODE=1; shift ;;
    --force)          FORCE=1; shift ;;
    -h|--help)        usage; exit 0 ;;
    *)                die "unknown argument: $1" ;;
  esac
done

REPO="$BUNDLE/repo"
WHEELS="$BUNDLE/vendor/wheels"
REQUIREMENTS="$BUNDLE/vendor/requirements.txt"
NODE_BIN="$BUNDLE/runtime/bin/node"
PYLIB="$BUNDLE/pylib"
PYBOOT="$BUNDLE/.pip-bootstrap"

# ---------------------------------------------------------------- preflight
preflight() {
  log "Checking prerequisites"

  [ -f "$BUNDLE/BUNDLE-INFO" ] || die "not a bundle: $BUNDLE/BUNDLE-INFO is missing"
  [ -d "$REPO" ]               || die "bundle is incomplete: $REPO is missing"
  [ -f "$REPO/llmwiki" ]       || die "bundle is incomplete: $REPO/llmwiki is missing"

  # Two web layouts are supported: a source checkout with a full node_modules (next start),
  # or a prebuilt `output: standalone` server that carries its own traced dependencies.
  if [ -f "$REPO/web/.next/standalone/server.js" ]; then
    ok "web app: prebuilt standalone"
  elif [ -f "$REPO/web/.next/BUILD_ID" ]; then
    ok "web app: built .next (next start)"
  else
    die "bundle has no web app (neither web/.next/standalone/server.js nor web/.next/BUILD_ID)"
  fi

  if [ -f "$PYLIB/.bundle-complete" ]; then
    ok "python packages: bundled (nothing to install)"
  elif [ -d "$WHEELS" ]; then
    ok "python packages: $(find "$WHEELS" -maxdepth 1 -name '*.whl' | wc -l) wheels"
  else
    die "bundle has neither pylib/ nor vendor/wheels/"
  fi

  if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    ( . /etc/os-release; printf '  ok %s %s\n' "${NAME:-Linux}" "${VERSION_ID:-}" )
  fi

  local machine_arch bundle_arch expected_arch
  machine_arch="$(uname -m)"
  bundle_arch="$(sed -n 's/^node_arch=//p' "$BUNDLE/BUNDLE-INFO")"
  case "$machine_arch" in
    x86_64)  expected_arch="x64" ;;
    aarch64) expected_arch="arm64" ;;
    *)       expected_arch="" ;;
  esac
  if [ -n "$bundle_arch" ] && [ -n "$expected_arch" ] && [ "$bundle_arch" != "$expected_arch" ]; then
    die "bundle carries a linux-$bundle_arch Node runtime, but this host is $machine_arch"
  fi
  ok "arch $machine_arch"

  command -v "$PYTHON" >/dev/null || die "'$PYTHON' not found; install Python 3.11+ or pass --python"
  local py_ver want_ver
  py_ver="$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "Python $py_ver is too old; 3.11+ is required"

  # shellcheck disable=SC1090
  want_ver="$(sed -n 's/^python_minor=//p' "$BUNDLE/BUNDLE-INFO" 2>/dev/null)"
  if [ -n "$want_ver" ] && [ "$py_ver" != "$want_ver" ]; then
    if [ "$FORCE" -eq 1 ]; then
      warn "bundle was built for Python $want_ver, this host has $py_ver — continuing because --force"
    else
      die "bundle was built for Python $want_ver, this host has $py_ver.
     Native wheels (asyncpg, lxml, cryptography) will not install across minor versions.
     Rebuild the bundle on a host with Python $py_ver, or re-run with --force if you know
     the wheels are portable."
    fi
  fi
  ok "python $py_ver"

  if [ ! -f "$NODE_BIN" ]; then
    die "bundled Node runtime is missing: $NODE_BIN"
  fi
  # Archives produced on Windows may land without the execute bit.
  [ -x "$NODE_BIN" ] || chmod +x "$NODE_BIN" 2>/dev/null || true
  [ -x "$NODE_BIN" ] || die "bundled Node runtime is not executable: chmod +x \"$NODE_BIN\""
  "$NODE_BIN" --version >/dev/null 2>&1 \
    || die "the bundled Node runtime does not run here (wrong architecture or libc?)"
  ok "node $("$NODE_BIN" --version)"
  ok "wheelhouse: $(find "$WHEELS" -maxdepth 1 -name '*.whl' | wc -l) wheels"

  local need_kb
  need_kb=$(du -sk "$BUNDLE" 2>/dev/null | cut -f1)
  need_kb=$(( need_kb / 1024 + 256 ))
  local free_kb
  free_kb=$(df -Pk "$BUNDLE" | awk 'NR==2 {print $4}')
  [ "$free_kb" -ge "$need_kb" ] || die "need about ${need_kb} MiB free, ${free_kb} KiB available"
  ok "disk space"

  if [ "$HOST" != "127.0.0.1" ] && [ "$HOST" != "localhost" ] && [ "$HOST" != "::1" ]; then
    ok "LAN exposure enabled — a workspace password will be required"
  fi

  for optional in soffice java; do
    if command -v "$optional" >/dev/null; then
      ok "$optional present"
    else
      warn "$optional not found — Office conversion / PDF parsing will degrade for those formats"
    fi
  done
}

# ---------------------------------------------------------------- pip + deps
ensure_pip() {
  if "$PYTHON" -m pip --version >/dev/null 2>&1; then return 0; fi
  if "$PYTHON" -m ensurepip --default-pip >/dev/null 2>&1 \
     && "$PYTHON" -m pip --version >/dev/null 2>&1; then
    return 0
  fi

  local wheel
  # `-print -quit` rather than `| head -n1`: with `set -o pipefail`, find can die from
  # SIGPIPE when head exits early, which would abort the script.
  wheel="$(find "$WHEELS" -maxdepth 1 -name 'pip-*.whl' -print -quit)"
  [ -n "$wheel" ] || die "pip is not installed and the bundle has no pip wheel"
  warn "pip is not installed on this host — bootstrapping it from the bundle"
  rm -rf "$PYBOOT"
  mkdir -p "$PYBOOT"
  "$PYTHON" -m zipfile -e "$wheel" "$PYBOOT"
  PYTHONPATH="$PYBOOT${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON" -m pip --version >/dev/null 2>&1 \
    || die "could not bootstrap pip from $wheel"
  export PYTHONPATH="$PYBOOT${PYTHONPATH:+:$PYTHONPATH}"
}

install_deps() {
  # Bundles produced on Windows already carry a Linux-targeted dependency tree, so the
  # target needs neither pip nor a wheelhouse.
  if [ -f "$PYLIB/.bundle-complete" ]; then
    log "Python dependencies are bundled — nothing to install"
    return 0
  fi

  [ -f "$REQUIREMENTS" ] || die "missing $REQUIREMENTS"
  ensure_pip

  local stamp
  stamp="$( { cat "$REQUIREMENTS"; find "$WHEELS" -maxdepth 1 -name '*.whl' | sort; } | sha256sum | cut -d' ' -f1)"
  if [ -f "$PYLIB/.stamp" ] && [ "$(cat "$PYLIB/.stamp")" = "$stamp" ]; then
    log "Python dependencies already installed"
    return 0
  fi

  log "Installing Python dependencies (offline)"
  rm -rf "$PYLIB"
  mkdir -p "$PYLIB"
  "$PYTHON" -m pip install \
    --no-index --find-links "$WHEELS" \
    --target "$PYLIB" \
    --disable-pip-version-check \
    -r "$REQUIREMENTS"
  printf '%s\n' "$stamp" > "$PYLIB/.stamp"
  ok "installed into $PYLIB"
}

# ---------------------------------------------------------------- workspace
prepare_workspace() {
  mkdir -p "$WORKSPACE"
  if [ ! -f "$WORKSPACE/.llmwiki/index.db" ]; then
    log "Initializing workspace: $WORKSPACE"
    "$PYTHON" "$REPO/llmwiki" init "$WORKSPACE"
  else
    log "Workspace already initialized: $WORKSPACE"
  fi
}

# ---------------------------------------------------------------- service
install_service() {
  [ "$(id -u)" -eq 0 ] || die "--install-service must run as root"

  local svc_user="${SERVICE_USER:-llmwiki}"
  if ! id -u "$svc_user" >/dev/null 2>&1; then
    log "Creating service account: $svc_user"
    useradd --system --home-dir "$WORKSPACE" --shell /usr/sbin/nologin "$svc_user"
  fi

  chown -R "$svc_user":"$svc_user" "$BUNDLE" "$WORKSPACE"

  local env_file=/etc/llmwiki/env
  mkdir -p /etc/llmwiki
  if [ -n "$PASSWORD" ]; then
    # Kept out of ExecStart so it never shows up in `ps`.
    printf 'LLMWIKI_PASSWORD=%s\n' "$PASSWORD" > "$env_file"
    chmod 600 "$env_file"
  fi

  local unit=/etc/systemd/system/llmwiki.service
  log "Writing $unit"
  cat > "$unit" <<EOF
[Unit]
Description=LLM Wiki
Documentation=file://$BUNDLE/README-OFFLINE.md
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$svc_user
Group=$svc_user
WorkingDirectory=$BUNDLE
Environment=PYTHONUNBUFFERED=1
Environment=LLMWIKI_PYTHON=$PYTHON
EnvironmentFile=-$env_file
ExecStart=$BUNDLE/deploy.sh --run --workspace "$WORKSPACE" --host "$HOST" --port "$PORT"
Restart=on-failure
RestartSec=5
KillMode=control-group
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable --now llmwiki.service
  ok "service installed and started"
  log ""
  log "Status: systemctl status llmwiki"
  log "Logs:   journalctl -u llmwiki -f"
  if [ -n "$PASSWORD" ]; then
    log "Password: set in $env_file (0600)"
  else
    log "Password: ${WORKSPACE}/.llmwiki/lan-password.txt"
  fi
}

# ---------------------------------------------------------------- main
preflight
if [ "$CHECK_ONLY" -eq 1 ]; then
  log "Checks passed (nothing was started)"
  exit 0
fi

install_deps
prepare_workspace

if [ "$INSTALL_SERVICE" -eq 1 ] && [ "$RUN_MODE" -eq 0 ]; then
  install_service
  exit 0
fi

export PATH="$BUNDLE/runtime/bin:$PATH"
export PYTHONPATH="$PYLIB${PYTHONPATH:+:$PYTHONPATH}"

# The chat drawer drives the bundled pi agent with the bundled Node runtime; nothing has to
# be installed on the host, and `pi` never needs to be on PATH.
PI_ENTRY="$BUNDLE/pi/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js"
if [ -f "$PI_ENTRY" ]; then
  export LLMWIKI_PI_BIN="$NODE_BIN $PI_ENTRY"
  export PI_CODING_AGENT_DIR="$REPO/.pi-agent"
  export PI_NO_UPDATE_CHECK=1
else
  warn "bundle has no pi agent — the chat drawer will be unavailable"
fi

export PYTHONNOUSERSITE=1
# The CLI reads LLMWIKI_PASSWORD, so the password never reaches the process table.
if [ -n "$PASSWORD" ]; then
  export LLMWIKI_PASSWORD="$PASSWORD"
fi
if [ -n "$READ_PASSWORD" ]; then
  export LLMWIKI_READ_PASSWORD="$READ_PASSWORD"
fi

served="$HOST"
if [ "$served" = "0.0.0.0" ]; then
  served="<this-host-ip>"
fi

log ""
log "Starting LLM Wiki"
log "  Web:      http://$served:$PORT"
log "  Password: ${WORKSPACE}/.llmwiki/lan-password.txt (generated on first LAN start)"
log ""

args=( serve "$WORKSPACE" --host "$HOST" --port "$PORT" --no-browser )
if [ "$REBUILD" -eq 1 ]; then
  args+=( --rebuild )
fi

exec "$PYTHON" "$REPO/llmwiki" "${args[@]}"
