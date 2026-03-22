# =============================================================================
# AlloyDB Auth Proxy — Windows PowerShell
# =============================================================================
# Purpose: Starts the AlloyDB Auth Proxy so your local machine can connect
#          to AlloyDB as if it were 127.0.0.1:5432.
#
# Prerequisites:
#   - gcloud CLI authenticated (gcloud auth application-default login)
#   - alloydb-auth-proxy.exe present in this directory
#     Download from:
#     https://storage.googleapis.com/alloydb-auth-proxy/v1.13.0/alloydb-auth-proxy.windows.amd64.exe
#     Rename to: alloydb-auth-proxy.exe
#     Place in:  scripts\infra\
#
# Usage (from repo root in PowerShell):
#   .\scripts\infra\start_proxy.ps1
#
# Keep this window open while running the ingestion script.
# Press Ctrl+C to stop the proxy.
# =============================================================================

$ErrorActionPreference = "Stop"

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile    = Join-Path $ScriptDir "env.ps1"
$ProxyExe   = Join-Path $ScriptDir "alloydb-auth-proxy.exe"

# ---- Download proxy if not present ----
if (-not (Test-Path $ProxyExe)) {
    Write-Host "AlloyDB Auth Proxy not found. Downloading latest version from GitHub..." -ForegroundColor Yellow
    try {
        $release  = Invoke-RestMethod -Uri "https://api.github.com/repos/GoogleCloudPlatform/alloydb-auth-proxy/releases/latest"
        $version  = $release.tag_name
        # File naming: v1.14.1+ uses alloydb-auth-proxy-x64.exe (older used windows.amd64.exe)
        $asset    = $release.assets | Where-Object { $_.name -match "x64\.exe$" -or $_.name -match "windows\.amd64\.exe$" } | Select-Object -First 1
        if (-not $asset) { throw "No Windows x64 asset found in release $version" }
        Write-Host "Downloading $($asset.name) ($version)..."
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $ProxyExe
        Write-Host "Saved to: $ProxyExe" -ForegroundColor Green
    } catch {
        Write-Host "Auto-download failed: $_" -ForegroundColor Red
        Write-Host ""
        Write-Host "Manual download:"
        Write-Host "  1. Go to: github.com/GoogleCloudPlatform/alloydb-auth-proxy/releases/latest"
        Write-Host "  2. Download: alloydb-auth-proxy-x64.exe"
        Write-Host "  3. Rename to: alloydb-auth-proxy.exe"
        Write-Host "  4. Place in: $ScriptDir"
        exit 1
    }
}

# ---- Source environment variables ----
if (-not (Test-Path $EnvFile)) {
    Write-Error "ERROR: env.ps1 not found at $EnvFile"
    Write-Host "Create it from env.ps1.example: cp scripts\infra\env.ps1.example scripts\infra\env.ps1"
    exit 1
}
. $EnvFile

$InstanceUri = "projects/$env:PROJECT_ID/locations/$env:REGION/clusters/$env:CLUSTER_NAME/instances/$env:INSTANCE_NAME"

Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " AlloyDB Auth Proxy — Local Dev (Windows)"   -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " Instance  : $InstanceUri"
Write-Host " Tunneling : 127.0.0.1:5432 -> AlloyDB"
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""

# ---- Check gcloud auth ----
Write-Host "Checking gcloud authentication..."
try {
    $token = gcloud auth application-default print-access-token 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Not authenticated" }
    Write-Host "Authenticated." -ForegroundColor Green
} catch {
    Write-Host ""
    Write-Host "ERROR: Not authenticated. Run:" -ForegroundColor Red
    Write-Host "  gcloud auth application-default login"
    exit 1
}

Write-Host ""
Write-Host "Starting proxy on 127.0.0.1:5432 ..."
Write-Host "Press Ctrl+C to stop." -ForegroundColor Yellow
Write-Host ""
Write-Host "Once running, connect from the ingestion script or psql:"
Write-Host "  Host: 127.0.0.1  Port: 5432  DB: $env:DB_NAME  User: $env:DB_USER"
Write-Host ""

# ---- Start proxy ----
& $ProxyExe $InstanceUri --port=5432
