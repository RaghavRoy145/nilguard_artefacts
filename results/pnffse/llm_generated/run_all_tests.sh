#!/bin/bash
# Note: not using set -e to ensure script always reaches cleanup/summary
###############################################################################
# run_pnffse_llm.sh — Run PNF-FSE on all 20 LLM-generated NPE test programs
#
# Sets up the Docker container once, then iterates over each test file,
# producing <testname>_detail.txt and <testname>_output.txt in the results dir.
#
# Usage:
#   ./run_pnffse_llm.sh [TEST_DIR] [OUTPUT_DIR]
#
# Defaults:
#   TEST_DIR   = tests/llm_generated/
#   OUTPUT_DIR = results/pnffse/llm_generated/
###############################################################################

IMAGE="yahuuuuui/fse24-prove_n_fix:ubuntu"
CONTAINER_NAME="pnffse-llm-batch"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TEST_DIR="${1:-${SCRIPT_DIR}}"
OUTPUT_DIR="${2:-${SCRIPT_DIR}}"

# ─── Colour helpers ──────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║   PNF-FSE Batch Analysis — LLM-Generated Programs      ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Test dir   : ${CYAN}${TEST_DIR}${NC}"
echo -e "  Output dir : ${CYAN}${OUTPUT_DIR}${NC}"
echo -e "  Container  : ${CYAN}${CONTAINER_NAME}${NC}"
echo ""

# ─── Validate test directory ─────────────────────────────────────────────────
if [ ! -d "$TEST_DIR" ]; then
    echo -e "${RED}[ERR]${NC} Test directory not found: $TEST_DIR"
    exit 1
fi

TEST_FILES=("$TEST_DIR"/test*.c)
if [ ${#TEST_FILES[@]} -eq 0 ]; then
    echo -e "${RED}[ERR]${NC} No test*.c files found in $TEST_DIR"
    exit 1
fi

echo -e "  Test files : ${#TEST_FILES[@]}"
echo ""

mkdir -p "$OUTPUT_DIR"

# ─── Container setup (once) ──────────────────────────────────────────────────

echo -e "${BOLD}━━━ Container Setup ━━━${NC}"
echo ""

echo "[1/3] Pulling Docker image..."
docker pull "$IMAGE"

# Handle existing container
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "Container '${CONTAINER_NAME}' already exists."
    read -p "Remove it and start fresh? (y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        docker rm -f "$CONTAINER_NAME"
    else
        echo "Exiting."
        exit 1
    fi
fi

echo "[2/3] Creating container..."
docker run -d --name "$CONTAINER_NAME" "$IMAGE" tail -f /dev/null

echo "[3/3] Building PNF-FSE..."
docker exec "$CONTAINER_NAME" bash -c '
    cd /home/infer_TempFix
    git stash 2>/dev/null
    git checkout main
    echo "Branch: $(git branch --show-current)"
    ./compile
'

# Create working directory in container
docker exec "$CONTAINER_NAME" mkdir -p /home/llm_test

echo ""
echo -e "${BOLD}━━━ Running Analysis ━━━${NC}"
echo ""

# ─── Iterate over test files ─────────────────────────────────────────────────

TOTAL_START=$(date +%s)
succeeded=0
failed=0
no_bugs=0
total=${#TEST_FILES[@]}
i=0

for test_file in "${TEST_FILES[@]}"; do
    filename=$(basename "$test_file")
    testname="${filename%.c}"
    ((i++)) || true

    echo -e "${GREEN}[$i/$total]${NC} ${BOLD}${testname}${NC}"

    # Copy source file into container
    docker cp "$test_file" "${CONTAINER_NAME}:/home/llm_test/${filename}"

    # Copy spec.c and clean previous results
    docker exec "$CONTAINER_NAME" bash -c '
        cd /home/llm_test
        cp /home/infer_TempFix/spec.c spec.c
        rm -rf infer-out
        rm -f /home/infer_TempFix/TempFix-out/detail.txt
        rm -f /home/infer_TempFix/TempFix-out/report.csv
    '

    # Run analysis and capture full output
    FILE_START=$(date +%s)
    docker exec "$CONTAINER_NAME" bash -c "
        cd /home/llm_test
        /home/infer_TempFix/infer/bin/infer run --pulse -- clang -c ${filename}
        python3 /home/infer_TempFix/TempFixDataAnalysis.py
    " > "${OUTPUT_DIR}/${testname}_output.txt" 2>&1
    FILE_END=$(date +%s)
    FILE_ELAPSED=$((FILE_END - FILE_START))

    # Extract detail.txt
    if docker cp "${CONTAINER_NAME}:/home/infer_TempFix/TempFix-out/detail.txt" \
        "${OUTPUT_DIR}/${testname}_detail.txt" 2>/dev/null; then

        DETAIL_SIZE=$(stat -c%s "${OUTPUT_DIR}/${testname}_detail.txt" 2>/dev/null || echo "0")
        if [ "$DETAIL_SIZE" -gt 10 ]; then
            BUG_COUNT=$(grep -c "Future-condition checking" "${OUTPUT_DIR}/${testname}_detail.txt" 2>/dev/null || echo "0")
            echo -e "    ${GREEN}✓${NC} ${BUG_COUNT} bug(s) found  (${FILE_ELAPSED}s)"
            ((succeeded++)) || true
        else
            echo -e "    ${YELLOW}○${NC} no bugs found  (${FILE_ELAPSED}s)"
            ((no_bugs++)) || true
        fi
    else
        # No detail.txt produced — create empty placeholder
        echo "" > "${OUTPUT_DIR}/${testname}_detail.txt"
        echo -e "    ${YELLOW}○${NC} no detail.txt produced  (${FILE_ELAPSED}s)"
        ((no_bugs++)) || true
    fi

    # Clean up source file from container
    docker exec "$CONTAINER_NAME" rm -f "/home/llm_test/${filename}"
done

TOTAL_END=$(date +%s)
TOTAL_ELAPSED=$((TOTAL_END - TOTAL_START))

# ─── Summary ─────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║                   Analysis Complete                    ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Total time : ${TOTAL_ELAPSED}s"
echo -e "  Bugs found : ${GREEN}${succeeded}${NC} / ${total}"
echo -e "  No bugs    : ${YELLOW}${no_bugs}${NC} / ${total}"
echo ""
echo -e "  Results in : ${CYAN}${OUTPUT_DIR}${NC}"
echo ""
echo "  Output files:"
ls "${OUTPUT_DIR}"/*.txt 2>/dev/null | while read -r f; do
    printf "    %-50s %s\n" "$(basename "$f")" "$(wc -l < "$f") lines"
done

echo ""

# Write metadata
cat > "${OUTPUT_DIR}/metadata.txt" << EOF
PNF-FSE LLM Batch Analysis
Generated     : $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Total time    : ${TOTAL_ELAPSED}s
Test files    : ${total}
Bugs found in : ${succeeded}
No bugs in    : ${no_bugs}
Image         : ${IMAGE}
EOF

echo "Container '${CONTAINER_NAME}' is still running."
echo "  Attach : docker exec -it ${CONTAINER_NAME} bash"
echo "  Remove : docker rm -f ${CONTAINER_NAME}"
echo ""
