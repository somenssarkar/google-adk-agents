#!/bin/bash
# =============================================================================
# AlloyDB Database Schema Setup
# =============================================================================
# Purpose: Creates the tutor_db database, enables extensions (pgvector,
#          alloydb_scann, google_ml_integration), and creates the problems
#          table with indexes.
#
# Run this AFTER setup_alloydb.sh and setup_iam.sh.
#
# Usage:
#   sh setup_database.sh
#
# Security: Password is retrieved from Google Secret Manager — never typed
#           on the command line or stored in a file.
#
# Prerequisites:
#   - setup_alloydb.sh completed (cluster + instance running, INSTANCE_IP in env.sh)
#   - setup_iam.sh completed (Vertex AI IAM granted)
#   - psql installed:
#       Windows (local): install from https://www.postgresql.org/download/windows/
#                        (choose "Command Line Tools" only — no need for server)
#       Cloud Shell:     pre-installed
# =============================================================================

set -e

# ---- Load environment variables ----
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ ! -f "$SCRIPT_DIR/env.sh" ]; then
    echo "ERROR: env.sh not found. Run setup_alloydb.sh first."
    exit 1
fi
source "$SCRIPT_DIR/env.sh"

# ---- Verify INSTANCE_IP is set ----
if [ -z "$INSTANCE_IP" ] || [ "$INSTANCE_IP" = "PENDING" ]; then
    echo "ERROR: INSTANCE_IP not set in env.sh."
    echo "  Run setup_alloydb.sh first, or manually set INSTANCE_IP in env.sh:"
    echo "    export INSTANCE_IP=\$(gcloud alloydb instances describe $INSTANCE_NAME \\"
    echo "        --cluster=$CLUSTER_NAME --region=$REGION --format='value(ipAddress)')"
    exit 1
fi

# ---- Check psql is available ----
if ! command -v psql &> /dev/null; then
    echo "ERROR: psql not found."
    echo ""
    echo "Install PostgreSQL client tools:"
    echo "  Windows: https://www.postgresql.org/download/windows/"
    echo "           (Select 'Command Line Tools' during installation)"
    echo "  macOS:   brew install libpq && brew link --force libpq"
    echo "  Linux:   sudo apt-get install -y postgresql-client"
    exit 1
fi

echo ""
echo "============================================="
echo " AlloyDB Database Schema Setup"
echo "============================================="
echo " Instance IP : $INSTANCE_IP"
echo " Database    : $DB_NAME"
echo " User        : $DB_USER"
echo " Password    : retrieved from Secret Manager"
echo "============================================="
echo ""

# ---- Retrieve password from Secret Manager ----
echo "[1/3] Retrieving password from Secret Manager..."
DB_PASSWORD=$(gcloud secrets versions access latest --secret="$SECRET_NAME")
echo "      Password retrieved."
echo ""

# ---- Step 2: Create the database ----
echo "[2/3] Creating database '$DB_NAME' (if not exists)..."
DB_EXISTS=$(PGPASSWORD="$DB_PASSWORD" psql \
    -h "$INSTANCE_IP" \
    -U "$DB_USER" \
    -d postgres \
    -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME';" 2>/dev/null || echo "")

if [ "$DB_EXISTS" = "1" ]; then
    echo "      Database '$DB_NAME' already exists. Skipping creation."
else
    PGPASSWORD="$DB_PASSWORD" psql \
        -h "$INSTANCE_IP" \
        -U "$DB_USER" \
        -d postgres \
        -c "CREATE DATABASE $DB_NAME;"
    echo "      Database '$DB_NAME' created."
fi
echo ""

# ---- Step 3: Run the schema SQL file ----
echo "[3/3] Running setup_database.sql against '$DB_NAME'..."
echo "      (Creates extensions, problems table, and indexes)"
echo ""
PGPASSWORD="$DB_PASSWORD" psql \
    -h "$INSTANCE_IP" \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    -f "$SCRIPT_DIR/setup_database.sql"

unset DB_PASSWORD

echo ""
echo "============================================="
echo " Database Setup Complete!"
echo "============================================="
echo ""
echo " What was created:"
echo "   Database   : $DB_NAME"
echo "   Extensions : vector, alloydb_scann, google_ml_integration"
echo "   Table      : problems (pgvector VECTOR(768))"
echo "   Indexes    : (subject, difficulty) B-tree + problem_text GIN"
echo ""
echo " Verify AlloyDB AI (run in psql):"
echo "   SELECT google_ml.embedding('text-embedding-005', 'test query');"
echo ""
echo " Connect interactively:"
echo "   PGPASSWORD=\$(gcloud secrets versions access latest --secret=$SECRET_NAME) \\"
echo "   psql -h $INSTANCE_IP -U $DB_USER -d $DB_NAME"
echo ""
echo " Next step:"
echo "   Run the dataset ingestion pipeline (Phase 2, Step 2.3a)"
echo "============================================="
