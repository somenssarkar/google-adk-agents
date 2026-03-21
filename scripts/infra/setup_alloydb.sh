#!/bin/bash
# =============================================================================
# AlloyDB Cluster & Instance Setup Script
# =============================================================================
# Purpose: Automates AlloyDB infrastructure creation for the AI Tutoring Platform.
#          Creates: APIs, Secret Manager secret, VPC peering, AlloyDB cluster,
#          AlloyDB primary instance.
#
# Prerequisites (local):
#   - gcloud CLI installed and authenticated:
#       gcloud auth login
#       gcloud auth application-default login
#   - env.sh configured (cp env.sh.example env.sh, fill in non-secret values)
#
# Usage:
#   cp env.sh.example env.sh      # already done? skip
#   sh setup_alloydb.sh
#
# Password security:
#   - You are prompted for the DB password interactively (never stored in a file)
#   - The password is saved to Google Secret Manager under $SECRET_NAME
#   - Subsequent scripts retrieve it with: gcloud secrets versions access latest
#
# Estimated time: 15-20 minutes (cluster + instance creation takes ~10-15 min)
# =============================================================================

set -e  # Exit on any error

# ---- Load environment variables ----
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ ! -f "$SCRIPT_DIR/env.sh" ]; then
    echo "ERROR: env.sh not found."
    echo "  cp $SCRIPT_DIR/env.sh.example $SCRIPT_DIR/env.sh"
    echo "  then fill in your project values."
    exit 1
fi
source "$SCRIPT_DIR/env.sh"

echo ""
echo "============================================="
echo " AlloyDB Setup — AI Tutoring Platform"
echo "============================================="
echo " Project:   $PROJECT_ID"
echo " Region:    $REGION"
echo " Cluster:   $CLUSTER_NAME"
echo " Instance:  $INSTANCE_NAME"
echo " DB:        $DB_NAME"
echo " Network:   $NETWORK_NAME"
echo "============================================="
echo ""

# ---- Prompt for DB password securely ----
echo "Enter a strong password for the AlloyDB database user (postgres)."
echo "Rules: min 8 chars, mix of uppercase, lowercase, numbers, symbols."
echo "(Input is hidden)"
echo ""
read -s -p "Password: " DB_PASSWORD
echo ""
read -s -p "Confirm password: " DB_PASSWORD_CONFIRM
echo ""

if [ "$DB_PASSWORD" != "$DB_PASSWORD_CONFIRM" ]; then
    echo "ERROR: Passwords do not match. Please run the script again."
    exit 1
fi

if [ ${#DB_PASSWORD} -lt 8 ]; then
    echo "ERROR: Password must be at least 8 characters."
    exit 1
fi

echo ""

# ---- Step 1: Set the active project ----
echo "[1/7] Setting active project..."
gcloud config set project "$PROJECT_ID" --quiet
echo "      Project set to: $PROJECT_ID"
echo ""

# ---- Step 2: Enable required APIs ----
echo "[2/7] Enabling required APIs (this may take 1-2 minutes)..."
gcloud services enable \
    alloydb.googleapis.com \
    compute.googleapis.com \
    servicenetworking.googleapis.com \
    secretmanager.googleapis.com \
    aiplatform.googleapis.com \
    --quiet
echo "      APIs enabled."
echo ""

# ---- Step 3: Store password in Secret Manager ----
echo "[3/7] Storing DB password in Google Secret Manager..."

# Check if the secret already exists
if gcloud secrets describe "$SECRET_NAME" --quiet 2>/dev/null; then
    echo "      Secret '$SECRET_NAME' exists — adding a new version..."
    echo -n "$DB_PASSWORD" | gcloud secrets versions add "$SECRET_NAME" --data-file=-
else
    echo "      Creating secret '$SECRET_NAME'..."
    echo -n "$DB_PASSWORD" | gcloud secrets create "$SECRET_NAME" \
        --replication-policy="automatic" \
        --data-file=-
fi

echo "      Password stored. Retrieve anytime with:"
echo "      gcloud secrets versions access latest --secret=$SECRET_NAME"
echo ""
unset DB_PASSWORD
unset DB_PASSWORD_CONFIRM

# ---- Step 4: Set up VPC Private Services Access ----
echo "[4/7] Setting up VPC Private Services Access..."
echo "      (Required by AlloyDB for private IP connectivity)"

EXISTING_RANGE=$(gcloud compute addresses list \
    --global \
    --filter="name=$IP_RANGE_NAME" \
    --format="value(name)" 2>/dev/null || true)

if [ -z "$EXISTING_RANGE" ]; then
    echo "      Creating private IP range ($IP_RANGE_NAME)..."
    gcloud compute addresses create "$IP_RANGE_NAME" \
        --global \
        --purpose=VPC_PEERING \
        --prefix-length=20 \
        --network="$NETWORK_NAME" \
        --description="AlloyDB PSA range for tutor platform" \
        --quiet
else
    echo "      Private IP range already exists. Skipping."
fi

EXISTING_PEERING=$(gcloud services vpc-peerings list \
    --network="$NETWORK_NAME" \
    --format="value(peering)" 2>/dev/null || true)

if [ -z "$EXISTING_PEERING" ]; then
    echo "      Creating VPC peering connection..."
    gcloud services vpc-peerings connect \
        --service=servicenetworking.googleapis.com \
        --ranges="$IP_RANGE_NAME" \
        --network="$NETWORK_NAME" \
        --quiet
else
    echo "      VPC peering already exists. Skipping."
fi

echo "      VPC Private Services Access configured."
echo ""

# ---- Step 5: Create AlloyDB Cluster ----
echo "[5/7] Creating AlloyDB cluster: $CLUSTER_NAME..."
echo "      NOTE: First cluster in a project activates the free trial"
echo "      (30 days, 8 vCPU at no cost). This takes 5-10 minutes..."

EXISTING_CLUSTER=$(gcloud alloydb clusters list \
    --region="$REGION" \
    --filter="name~$CLUSTER_NAME" \
    --format="value(name)" 2>/dev/null || true)

if [ -z "$EXISTING_CLUSTER" ]; then
    DB_PASS=$(gcloud secrets versions access latest --secret="$SECRET_NAME")
    gcloud alloydb clusters create "$CLUSTER_NAME" \
        --region="$REGION" \
        --password="$DB_PASS" \
        --network="$NETWORK_NAME" \
        --quiet
    unset DB_PASS
    echo "      Cluster created."
else
    echo "      Cluster '$CLUSTER_NAME' already exists. Skipping."
fi
echo ""

# ---- Step 6: Create AlloyDB Primary Instance ----
echo "[6/7] Creating AlloyDB primary instance: $INSTANCE_NAME..."
echo "      This takes another 5-10 minutes..."

EXISTING_INSTANCE=$(gcloud alloydb instances list \
    --cluster="$CLUSTER_NAME" \
    --region="$REGION" \
    --filter="name~$INSTANCE_NAME" \
    --format="value(name)" 2>/dev/null || true)

if [ -z "$EXISTING_INSTANCE" ]; then
    gcloud alloydb instances create "$INSTANCE_NAME" \
        --cluster="$CLUSTER_NAME" \
        --region="$REGION" \
        --instance-type=PRIMARY \
        --cpu-count=2 \
        --quiet
    echo "      Primary instance created."
else
    echo "      Instance '$INSTANCE_NAME' already exists. Skipping."
fi
echo ""

# ---- Step 7: Retrieve and display connection details ----
echo "[7/7] Retrieving connection details..."
INSTANCE_IP=$(gcloud alloydb instances describe "$INSTANCE_NAME" \
    --cluster="$CLUSTER_NAME" \
    --region="$REGION" \
    --format="value(ipAddress)" 2>/dev/null || echo "PENDING")

# Save IP to env.sh for use by subsequent scripts
if ! grep -q "INSTANCE_IP" "$SCRIPT_DIR/env.sh"; then
    echo "" >> "$SCRIPT_DIR/env.sh"
    echo "# AlloyDB instance IP (auto-populated by setup_alloydb.sh)" >> "$SCRIPT_DIR/env.sh"
    echo "export INSTANCE_IP=\"$INSTANCE_IP\"" >> "$SCRIPT_DIR/env.sh"
fi

echo ""
echo "============================================="
echo " AlloyDB Setup Complete!"
echo "============================================="
echo ""
echo " Connection Details:"
echo "   Instance IP : $INSTANCE_IP"
echo "   User        : $DB_USER"
echo "   Password    : stored in Secret Manager ($SECRET_NAME)"
echo ""
echo " Retrieve password:"
echo "   gcloud secrets versions access latest --secret=$SECRET_NAME"
echo ""
echo " Next Steps — run in order:"
echo "   1. sh setup_iam.sh          (grant Vertex AI access to AlloyDB)"
echo "   2. sh setup_database.sh     (create DB, extensions, schema)"
echo "============================================="
