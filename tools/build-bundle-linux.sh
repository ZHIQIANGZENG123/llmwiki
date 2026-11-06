#!/usr/bin/env bash
# Build a self-contained LLM Wiki bundle for an offline Debian 12 (bookworm) host.
#
# Run this on a CONNECTED Linux machine with the same CPU architecture and the same
# Python minor version as the target. Debian 12 itself is the natural choice: it ships
# Python 3.11, which is exactly what the app requires, so the wheelhouse matches the
# target out of the box.
#
#   ./tools/build-bundle-linux.sh --out /tmp/bundle
#
# Then copy the resulting tarball to the air-gapped host and run deploy.sh there.
#
# Why it must be Linux: the Python wheelhouse is platform-specific, and web/node_modules
# contains native binaries (sharp, lightningcss, @tailwindcss/oxide). Windows artifacts
# cannot be reused on Linux.
#
# The build host needs Node.js 20+ on PATH (Debian 12's own nodejs is 18 — install a
# current Node from nodesource or use nvm). The bundled runtime is downloaded separately,
# so the target does not need Node at all.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT=""
ARCH=""
NODE_VERSION=""
SKIP_BUILD=0

log()  { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: build-bundle-linux.sh --out DIR [options]

  --out DIR             Destination bundle directory (required)
  --arch ARCH           Node runtime arch: x64 | arm64 (default: this machine)
  --node-version VER    Node version to bundle (default: the local `node` version)
  --skip-build          Reuse an existing web/.next instead of rebuilding
  -h, --help            Show this help
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --out)           OUT="${2:-}"; shift 2 ;;
    --arch)          ARCH="${2:-}"; shift 2 ;;
    --node-version)  NODE_VERSION="${2:-}"; shift 2 ;;
    --skip-build)    SKIP_BUILD=1; shift ;;
    -h|--help)       usage; exit 0 ;;
    *)               die "unknown argument: $1" ;;
  esac
done

[ -n "$OUT" ] || { usage; die "--out is required"; }

case "$(uname -s)" in
  Linux) ;;
  *) die "this builds a Linux bundle and must run on Linux — use a Debian 12 VM or container" ;;
esac

PY="$(command -v python3 || true)"
[ -n "$PY" ] || die "python3 not found"
command -v npm >/dev/null || die "npm not found (install Node.js 20+)"
command -v node >/dev/null || die "node not found"
command -v curl >/dev/null || die "curl not found"

PY_VER="$("$PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
[ -n "$PY_VER" ] || die "'$PY' does not report a version"
NODE_VERSION="${NODE_VERSION:-$(node --version | sed 's/^v//')}"
ARCH="${ARCH:-$(uname -m)}"
case "$ARCH" in
  x86_64|amd64) NODE_ARCH="x64" ;;
  aarch64|arm64) NODE_ARCH="arm64" ;;
  *) die "unsupported arch: $ARCH (expected x86_64 or aarch64)" ;;
esac

OUT="$(mkdir -p "$OUT" && cd "$OUT" && pwd)"
[ "$OUT" != "$REPO" ] || die "--out must not be the repo root"
case "$OUT/" in
  "$REPO"/*) die "--out must not be inside the repo" ;;
esac

log "repo:      $REPO"
log "bundle:    $OUT"
log "python:    $PY_VER"
log "node:      v$NODE_VERSION ($NODE_ARCH)"
log ""

# ---------------------------------------------------------------- web build
if [ "$SKIP_BUILD" -eq 0 ]; then
  log "Installing web dependencies"
  ( cd "$REPO/web" && npm ci --no-audit --no-fund )

  log "Building the web app (local mode, same-origin /api)"
  ( cd "$REPO/web" && NEXT_PUBLIC_MODE=local NEXT_PUBLIC_API_URL=/api \
      NEXT_TELEMETRY_DISABLED=1 npm run build )
else
  [ -f "$REPO/web/.next/BUILD_ID" ] || die "--skip-build given but web/.next is missing"
  log "Reusing the existing web build"
fi

[ -f "$REPO/web/.next/BUILD_ID" ] || die "web build produced no BUILD_ID"

# Tell the CLI not to rebuild on the offline host. Must match `_build_marker` in ./llmwiki.
printf 'NEXT_PUBLIC_MODE=local|NEXT_PUBLIC_API_URL=/api|NEXT_PUBLIC_MCP_URL=\n' \
  > "$REPO/web/.next/.llmwiki-build"

[ -f "$REPO/web/public/pdf.worker.min.mjs" ] || \
  die "web/public/pdf.worker.min.mjs missing — PDF previews would need the network"

# ---------------------------------------------------------------- wheels
log "Downloading Python wheels for $PY_VER"
WHEELS="$OUT/vendor/wheels"
rm -rf "$WHEELS"
mkdir -p "$WHEELS"

REQ="$OUT/vendor/requirements.txt"
cat "$REPO/api/requirements.txt" "$REPO/mcp/requirements.txt" > "$REQ"

"$PY" -m pip download --only-binary=:all: --dest "$WHEELS" -r "$REQ"
# Debian's minimal image has no pip; bundle it so the target can bootstrap offline.
"$PY" -m pip download --only-binary=:all: --dest "$WHEELS" pip setuptools wheel

# ---------------------------------------------------------------- node runtime
log "Downloading Node v$NODE_VERSION for linux-$NODE_ARCH"
RUNTIME="$OUT/runtime"
rm -rf "$RUNTIME"
mkdir -p "$RUNTIME"
# .tar.gz rather than .tar.xz: a minimal Debian install may not have xz-utils.
NODE_TARBALL="node-v$NODE_VERSION-linux-$NODE_ARCH.tar.gz"
NODE_URL="https://nodejs.org/dist/v$NODE_VERSION/$NODE_TARBALL"
curl -fsSL "$NODE_URL" -o "$OUT/$NODE_TARBALL" || die "cannot download $NODE_URL"
tar -xzf "$OUT/$NODE_TARBALL" -C "$RUNTIME" --strip-components=1
rm -f "$OUT/$NODE_TARBALL"
"$RUNTIME/bin/node" --version >/dev/null || die "bundled node does not run on this machine"

# ---------------------------------------------------------------- repo copy
log "Copying the repo"
BUNDLE_REPO="$OUT/repo"
rm -rf "$BUNDLE_REPO"
mkdir -p "$BUNDLE_REPO"

tar -C "$REPO" -cf - \
  --exclude=./.git \
  --exclude=./.venv \
  --exclude=./venv \
  --exclude=./.llmwiki \
  --exclude=./.pydeps \
  --exclude=./.piptmp \
  --exclude=./.pytest_cache \
  --exclude=./__pycache__ \
  --exclude='*/__pycache__' \
  --exclude=./web/.next/cache \
  --exclude='*.log' \
  . | tar -C "$BUNDLE_REPO" -xf -

# ---------------------------------------------------------------- deploy payload
cp "$REPO/tools/deploy-debian.sh" "$OUT/deploy.sh"
chmod +x "$OUT/deploy.sh"

cat > "$OUT/BUNDLE-INFO" <<EOF
built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
built_on="$(uname -srm)"
python_minor=$PY_VER
node_version=$NODE_VERSION
node_arch=$NODE_ARCH
EOF

cat > "$OUT/README-OFFLINE.md" <<'EOF'
# LLM Wiki — offline bundle (Debian 12 bookworm)

## 1. Copy this directory to the offline host

## 2. Run the deploy script

```bash
sudo ./deploy.sh --workspace /srv/llmwiki --host 0.0.0.0 --port 3000
```

The first LAN start prints a generated workspace password and saves it to
`/srv/llmwiki/.llmwiki/lan-password.txt`. Re-run with `--password '...'` to set your own.

## 3. Install it as a service (optional)

```bash
sudo ./deploy.sh --install-service --workspace /srv/llmwiki --host 0.0.0.0
```

Then `systemctl status llmwiki` / `journalctl -u llmwiki -f`.

## Notes

- `deploy.sh --check` verifies prerequisites without starting anything.
- Nothing here needs the network: the Python wheels, the Node runtime and the prebuilt
  web app are all inside this bundle. No pip, no venv, no node on the host.
- The bundle was built for the Python minor version recorded in `BUNDLE-INFO` (3.11 on
  Debian 12). `deploy.sh` refuses to run on a different minor unless you pass `--force`.
- Optional extras, install from the Debian ISO if you need them:
  `libreoffice-writer libreoffice-impress` (Word/PowerPoint), `default-jre-headless`
  (opendataloader PDF parsing).
- To rebuild the web app after changing `repo/web/src`, rebuild the bundle on a connected
  machine. `next build` downloads the UI font, so it cannot run here.
EOF

log "Creating the tarball"
TARBALL="${OUT%/}.tar.gz"
tar -C "$(dirname "$OUT")" -czf "$TARBALL" "$(basename "$OUT")"

log ""
log "Bundle:  $TARBALL"
log "Size:    $(du -h "$TARBALL" | cut -f1)"
log "Next:    copy it to the offline host, unpack, then run ./deploy.sh"
