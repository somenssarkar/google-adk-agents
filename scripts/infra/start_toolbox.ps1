# start_toolbox.ps1 — Download and start MCP Toolbox for Databases (Windows)
#
# Starts the genai-toolbox server using mcp_toolbox/tools.yaml.
# Keep this terminal open while running the quiz_agent or testing MCP tools.
# The toolbox listens at http://127.0.0.1:5000/mcp
#
# Usage (run from repo root):
#   .\scripts\infra\start_toolbox.ps1
#
# Prerequisites:
#   - gcloud auth application-default login (ADC for AlloyDB connector)
#   - DB_PASSWORD env var set (or retrieved from Secret Manager automatically)
#   - mcp_toolbox/tools.yaml present (committed to git)

$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Configuration — update TOOLBOX_VERSION to the latest release from:
# https://github.com/googleapis/genai-toolbox/releases
# ---------------------------------------------------------------------------
$TOOLBOX_VERSION = "0.7.0"
$SCRIPT_DIR  = Split-Path -Parent $MyInvocation.MyCommand.Path   # scripts\infra\
$REPO_ROOT   = Split-Path -Parent (Split-Path -Parent $SCRIPT_DIR) # repo root
$TOOLS_FILE  = Join-Path $REPO_ROOT "mcp_toolbox\tools.yaml"
$BINARY_PATH = Join-Path $SCRIPT_DIR "toolbox.exe"
$DOWNLOAD_URL = "https://storage.googleapis.com/genai-toolbox/v${TOOLBOX_VERSION}/windows/amd64/toolbox.exe"

# ---------------------------------------------------------------------------
# Verify prerequisites
# ---------------------------------------------------------------------------
Write-Host "=== MCP Toolbox for Databases ===" -ForegroundColor Cyan
Write-Host "Version : v$TOOLBOX_VERSION"
Write-Host "Platform: windows/amd64"
Write-Host ""

if (-not (Test-Path $TOOLS_FILE)) {
    Write-Host "ERROR: tools.yaml not found at $TOOLS_FILE" -ForegroundColor Red
    Write-Host "Ensure you are running from the repo root:"
    Write-Host "  .\scripts\infra\start_toolbox.ps1"
    exit 1
}

# Resolve DB_PASSWORD — try env.ps1 first, then Secret Manager
if (-not $env:DB_PASSWORD) {
    Write-Host "DB_PASSWORD not set. Attempting to retrieve from Secret Manager..."
    $EnvFile = Join-Path $SCRIPT_DIR "env.ps1"
    if (Test-Path $EnvFile) { . $EnvFile }

    if (-not $env:DB_PASSWORD) {
        $PROJECT_ID  = if ($env:PROJECT_ID)  { $env:PROJECT_ID }  else { "genai-apac-demo-project" }
        $SECRET_NAME = if ($env:SECRET_NAME) { $env:SECRET_NAME } else { "alloydb-tutor-password" }
        try {
            $env:DB_PASSWORD = gcloud secrets versions access latest `
                --secret=$SECRET_NAME `
                --project=$PROJECT_ID 2>$null
        } catch { }
    }

    if (-not $env:DB_PASSWORD) {
        Write-Host "ERROR: Could not retrieve DB_PASSWORD." -ForegroundColor Red
        Write-Host "Set it manually:  `$env:DB_PASSWORD = '<your-password>'"
        exit 1
    }
    Write-Host "DB_PASSWORD retrieved successfully."
}

# Verify Application Default Credentials
try {
    gcloud auth application-default print-access-token 2>&1 | Out-Null
    Write-Host "ADC credentials : OK"
} catch {
    Write-Host "ERROR: Application Default Credentials not configured." -ForegroundColor Red
    Write-Host "Run: gcloud auth application-default login"
    exit 1
}

# ---------------------------------------------------------------------------
# Download binary if not already present
# ---------------------------------------------------------------------------
if (-not (Test-Path $BINARY_PATH)) {
    Write-Host ""
    Write-Host "Downloading toolbox binary..."
    Write-Host "  $DOWNLOAD_URL"
    Invoke-WebRequest -Uri $DOWNLOAD_URL -OutFile $BINARY_PATH -UseBasicParsing
    Write-Host "Saved to: $BINARY_PATH"
} else {
    Write-Host "Binary          : $BINARY_PATH (cached)"
}

# ---------------------------------------------------------------------------
# Start the server
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Starting MCP Toolbox server..." -ForegroundColor Green
Write-Host "  Tools file : $TOOLS_FILE"
Write-Host "  Endpoint   : http://127.0.0.1:5000/mcp"
Write-Host ""
Write-Host "Keep this terminal open while the quiz_agent is running."
Write-Host "Press Ctrl+C to stop."
Write-Host ""

& $BINARY_PATH `
    --tools-file $TOOLS_FILE `
    --address "127.0.0.1" `
    --port 5000
