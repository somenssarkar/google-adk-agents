#!/bin/bash
# =============================================================================
# AlloyDB Auth Proxy — Local Development Connection
# =============================================================================
# Purpose: Starts the AlloyDB Auth Proxy so your local machine can connect
#          to AlloyDB as if it were localhost:5432.
#
# This is needed when running ADK agents and MCP Toolbox locally — they
# connect to AlloyDB through the proxy tunnel.
#
# Prerequisites:
#   - gcloud CLI authenticated (gcloud auth application-default login)
#   - setup_alloydb.sh completed
#   - AlloyDB Auth Proxy binary installed (this script installs it if missing)
#
# Usage:
#   sh start_proxy.sh
#
# After the proxy is running, connect from any local tool using:
#   Host:     127.0.0.1
#   Port:     5432
#   Database: tutor_db
#   User:     postgres
#   Password: gcloud secrets versions access latest --secret=alloydb-tutor-password
# =============================================================================

set -e

# ---- Load environment variables ----
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ ! -f "$SCRIPT_DIR/env.sh" ]; then
    echo "ERROR: env.sh not found. Run setup_alloydb.sh first."
    exit 1
fi
source "$SCRIPT_DIR/env.sh"

# AlloyDB instance URI format:
# projects/PROJECT/locations/REGION/clusters/CLUSTER/instances/INSTANCE
INSTANCE_URI="projects/${PROJECT_ID}/locations/${REGION}/clusters/${CLUSTER_NAME}/instances/${INSTANCE_NAME}"
PROXY_BIN="$SCRIPT_DIR/alloydb-auth-proxy"

echo ""
echo "============================================="
echo " AlloyDB Auth Proxy — Local Dev"
echo "============================================="
echo " Instance : $INSTANCE_URI"
echo " Tunneling: 127.0.0.1:5432 → AlloyDB"
echo "============================================="
echo ""

# ---- Install proxy if not present ----
if [ ! -f "$PROXY_BIN" ]; then
    echo "AlloyDB Auth Proxy not found. Downloading..."

    OS=$(uname -s | tr '[:upper:]' '[:lower:]')
    ARCH=$(uname -m)

    case "$OS" in
        linux)
            case "$ARCH" in
                x86_64) PROXY_FILE="alloydb-auth-proxy.linux.amd64" ;;
                aarch64) PROXY_FILE="alloydb-auth-proxy.linux.arm64" ;;
                *) echo "Unsupported Linux arch: $ARCH"; exit 1 ;;
            esac ;;
        darwin)
            case "$ARCH" in
                x86_64) PROXY_FILE="alloydb-auth-proxy.darwin.amd64" ;;
                arm64)  PROXY_FILE="alloydb-auth-proxy.darwin.arm64" ;;
                *) echo "Unsupported macOS arch: $ARCH"; exit 1 ;;
            esac ;;
        *)
            echo "Windows detected or unsupported OS."
            echo "Use start_proxy.ps1 instead (PowerShell auto-downloads the proxy):"
            echo "  .\\scripts\\infra\\start_proxy.ps1"
            exit 1 ;;
    esac

    # Fetch latest version tag from GitHub API
    LATEST=$(curl -s https://api.github.com/repos/GoogleCloudPlatform/alloydb-auth-proxy/releases/latest \
        | grep '"tag_name"' | head -1 | sed 's/.*"tag_name": *"\(.*\)".*/\1/')
    echo "Latest version: $LATEST"
    PROXY_URL="https://github.com/GoogleCloudPlatform/alloydb-auth-proxy/releases/download/${LATEST}/${PROXY_FILE}"
    echo "Downloading from: $PROXY_URL"
    curl -L -o "$PROXY_BIN" "$PROXY_URL"
    chmod +x "$PROXY_BIN"
    echo "Proxy installed at: $PROXY_BIN"
    echo ""
fi

# ---- Check gcloud auth ----
echo "Checking gcloud authentication..."
gcloud auth application-default print-access-token > /dev/null 2>&1 || {
    echo ""
    echo "ERROR: Not authenticated. Run:"
    echo "  gcloud auth application-default login"
    exit 1
}
echo "Authenticated."
echo ""

# ---- Print connection info ----
echo "Starting AlloyDB Auth Proxy..."
echo "Press Ctrl+C to stop."
echo ""
echo "Connection details for local tools:"
echo "  Host     : 127.0.0.1"
echo "  Port     : 5432"
echo "  Database : $DB_NAME"
echo "  User     : $DB_USER"
echo "  Password : \$(gcloud secrets versions access latest --secret=$SECRET_NAME)"
echo ""
echo "Quick connect (psql):"
echo "  PGPASSWORD=\$(gcloud secrets versions access latest --secret=$SECRET_NAME) \\"
echo "  psql -h 127.0.0.1 -p 5432 -U $DB_USER -d $DB_NAME"
echo ""
echo "For MCP Toolbox (tools.yaml):"
echo "  type: alloydb-postgres"
echo "  project: $PROJECT_ID"
echo "  region:  $REGION"
echo "  cluster: $CLUSTER_NAME"
echo "  instance: $INSTANCE_NAME"
echo ""

# ---- Start the proxy ----
"$PROXY_BIN" "$INSTANCE_URI" --port=5432
