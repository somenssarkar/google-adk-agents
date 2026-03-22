#!/usr/bin/env bash
# start_toolbox.sh — Download and start MCP Toolbox for Databases (Linux/macOS/Cloud Shell)
#
# Starts the genai-toolbox server using mcp_toolbox/tools.yaml.
# Keep this terminal open while running the quiz_agent or testing MCP tools.
# The toolbox listens at http://127.0.0.1:5000/mcp
#
# Usage (run from repo root):
#   bash scripts/infra/start_toolbox.sh
#
# Prerequisites:
#   - gcloud auth application-default login (ADC for AlloyDB connector)
#   - DB_PASSWORD env var set (or sourced from env.sh)
#   - mcp_toolbox/tools.yaml present (committed to git)

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration — update TOOLBOX_VERSION to the latest release from:
# https://github.com/googleapis/genai-toolbox/releases
# ---------------------------------------------------------------------------
TOOLBOX_VERSION="0.7.0"
TOOLBOX_DIR="$(cd "$(dirname "$0")" && pwd)"       # scripts/infra/
REPO_ROOT="$(cd "$TOOLBOX_DIR/../.." && pwd)"      # repo root
TOOLS_FILE="$REPO_ROOT/mcp_toolbox/tools.yaml"
BINARY_PATH="$TOOLBOX_DIR/toolbox"

# ---------------------------------------------------------------------------
# Detect OS and architecture
# ---------------------------------------------------------------------------
OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS" in
  Linux)
    case "$ARCH" in
      x86_64)  PLATFORM="linux/amd64" ;;
      aarch64) PLATFORM="linux/arm64" ;;
      *)       echo "Unsupported architecture: $ARCH"; exit 1 ;;
    esac
    ;;
  Darwin)
    case "$ARCH" in
      x86_64)  PLATFORM="darwin/amd64" ;;
      arm64)   PLATFORM="darwin/arm64" ;;
      *)       echo "Unsupported architecture: $ARCH"; exit 1 ;;
    esac
    ;;
  MINGW*|CYGWIN*|MSYS*)
    echo "Windows detected — use start_toolbox.ps1 instead:"
    echo "  .\\scripts\\infra\\start_toolbox.ps1"
    exit 1
    ;;
  *)
    echo "Unsupported OS: $OS"; exit 1 ;;
esac

DOWNLOAD_URL="https://storage.googleapis.com/genai-toolbox/v${TOOLBOX_VERSION}/${PLATFORM}/toolbox"

# ---------------------------------------------------------------------------
# Verify prerequisites
# ---------------------------------------------------------------------------
echo "=== MCP Toolbox for Databases ==="
echo "Version : v${TOOLBOX_VERSION}"
echo "Platform: ${PLATFORM}"
echo ""

if [[ ! -f "$TOOLS_FILE" ]]; then
  echo "ERROR: tools.yaml not found at $TOOLS_FILE"
  echo "Ensure you are running from the repo root:"
  echo "  bash scripts/infra/start_toolbox.sh"
  exit 1
fi

# Resolve DB_PASSWORD — try env.sh first, then Secret Manager
if [[ -z "${DB_PASSWORD:-}" ]]; then
  echo "DB_PASSWORD not set. Attempting to retrieve from Secret Manager..."
  source "$TOOLBOX_DIR/env.sh" 2>/dev/null || true
  if [[ -z "${DB_PASSWORD:-}" ]]; then
    DB_PASSWORD="$(gcloud secrets versions access latest \
      --secret="${SECRET_NAME:-alloydb-tutor-password}" \
      --project="${PROJECT_ID:-genai-apac-demo-project}" 2>/dev/null || true)"
  fi
  if [[ -z "${DB_PASSWORD:-}" ]]; then
    echo "ERROR: Could not retrieve DB_PASSWORD."
    echo "Set it manually:  export DB_PASSWORD=<your-password>"
    exit 1
  fi
  echo "DB_PASSWORD retrieved successfully."
fi
export DB_PASSWORD

# Verify Application Default Credentials
if ! gcloud auth application-default print-access-token &>/dev/null; then
  echo "ERROR: Application Default Credentials not configured."
  echo "Run: gcloud auth application-default login"
  exit 1
fi
echo "ADC credentials : OK"

# ---------------------------------------------------------------------------
# Download binary if not already present
# ---------------------------------------------------------------------------
if [[ ! -f "$BINARY_PATH" ]]; then
  echo ""
  echo "Downloading toolbox binary..."
  echo "  $DOWNLOAD_URL"
  curl -fsSL "$DOWNLOAD_URL" -o "$BINARY_PATH"
  chmod +x "$BINARY_PATH"
  echo "Saved to: $BINARY_PATH"
else
  echo "Binary          : $BINARY_PATH (cached)"
fi

# ---------------------------------------------------------------------------
# Start the server
# ---------------------------------------------------------------------------
echo ""
echo "Starting MCP Toolbox server..."
echo "  Tools file : $TOOLS_FILE"
echo "  Endpoint   : http://127.0.0.1:5000/mcp"
echo ""
echo "Keep this terminal open while the quiz_agent is running."
echo "Press Ctrl+C to stop."
echo ""

exec "$BINARY_PATH" \
  --tools-file "$TOOLS_FILE" \
  --address "127.0.0.1" \
  --port 5000
