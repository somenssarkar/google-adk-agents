#!/bin/bash
# =============================================================================
# AlloyDB AI — Vertex AI IAM Setup
# =============================================================================
# Purpose: Grants the AlloyDB service agent access to Vertex AI so that
#          google_ml.embedding() can generate embeddings directly in SQL.
#
# Run this AFTER setup_alloydb.sh.
#
# Usage:
#   sh setup_iam.sh
#
# What this enables in SQL (after setup_database.sql):
#   SELECT google_ml.embedding('text-embedding-005', 'your query text');
# =============================================================================

set -e

# ---- Load environment variables ----
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ ! -f "$SCRIPT_DIR/env.sh" ]; then
    echo "ERROR: env.sh not found. Run setup_alloydb.sh first."
    exit 1
fi
source "$SCRIPT_DIR/env.sh"

echo ""
echo "============================================="
echo " AlloyDB AI — Vertex AI IAM Setup"
echo "============================================="
echo " Project: $PROJECT_ID"
echo ""

# ---- Step 1: Get the project number ----
echo "[1/3] Getting project number..."
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" \
    --format="value(projectNumber)")
echo "      Project number: $PROJECT_NUMBER"
echo ""

# ---- Step 2: Construct the AlloyDB service agent email ----
ALLOYDB_SA="service-${PROJECT_NUMBER}@gcp-sa-alloydb.iam.gserviceaccount.com"
echo "[2/3] AlloyDB service agent: $ALLOYDB_SA"
echo ""

# ---- Step 3: Grant Vertex AI User role ----
echo "[3/3] Granting Vertex AI User role..."
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$ALLOYDB_SA" \
    --role="roles/aiplatform.user" \
    --condition=None \
    --quiet

echo ""
echo "============================================="
echo " IAM Setup Complete!"
echo "============================================="
echo ""
echo " AlloyDB can now call Vertex AI for in-database embeddings."
echo ""
echo " After running setup_database.sh, verify with:"
echo "   SELECT google_ml.embedding('text-embedding-005', 'test');"
echo ""
echo " Next step:"
echo "   sh setup_database.sh"
echo "============================================="
