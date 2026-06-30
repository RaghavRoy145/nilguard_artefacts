#!/bin/bash
# Note: not using set -e to ensure script always reaches the attach prompt

IMAGE="yahuuuuui/fse24-prove_n_fix:ubuntu"
CONTAINER_NAME="small-npes-analysis"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT_DIR="${SCRIPT_DIR}/small-npes-analysis-results/${TIMESTAMP}"

# Source file to analyze (external to infer_TempFix repo)
SOURCE_FILE="/home/vorashil/Projects/infer_RaghavRoy/examples/c_npe/small-progs/hand-crafted/small_npes.c"

echo "=== ProveNFix small_npes.c Analysis ==="
echo "Single file NPE analysis"
echo ""
echo "Container: ${CONTAINER_NAME}"
echo "Source: ${SOURCE_FILE}"
echo "Results: ${OUTPUT_DIR}"
echo ""

# Check source file exists
if [ ! -f "$SOURCE_FILE" ]; then
    echo "ERROR: Source file not found: ${SOURCE_FILE}"
    exit 1
fi

mkdir -p "$OUTPUT_DIR"

# Pull image
echo "[1/6] Pulling Docker image..."
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

# Start container
echo "[2/6] Creating container..."
docker run -d --name "$CONTAINER_NAME" "$IMAGE" tail -f /dev/null

# Use main branch
echo "[3/6] Using main branch..."
docker exec "$CONTAINER_NAME" bash -c '
    cd /home/infer_TempFix
    git stash
    git checkout main
    echo "Branch: $(git branch --show-current)"
'

echo ""
echo "[4/6] Building ProveNFix..."
docker exec "$CONTAINER_NAME" bash -c '
    cd /home/infer_TempFix
    ./compile
'

# Set up single file project
echo ""
echo "[5/6] Setting up single file project..."

# First create the directory in the container
docker exec "$CONTAINER_NAME" mkdir -p /home/small_npes

# Copy the source file into the container
docker cp "$SOURCE_FILE" "${CONTAINER_NAME}:/home/small_npes/small_npes.c"

# Copy the root spec.c from infer_TempFix (the comprehensive one)
docker exec "$CONTAINER_NAME" bash -c '
    cd /home/small_npes
    cp /home/infer_TempFix/spec.c spec.c
    echo "Copied root spec.c"
    echo "Source file: $(ls -la small_npes.c 2>/dev/null || echo "NOT FOUND")"
    echo "spec.c lines: $(wc -l < spec.c)"
'

echo ""
echo "[6/6] Running Infer Pulse analysis..."
docker exec "$CONTAINER_NAME" bash -c '
    cd /home/small_npes
    # Clear old results
    rm -rf infer-out
    rm -f /home/infer_TempFix/TempFix-out/detail.txt
    rm -f /home/infer_TempFix/TempFix-out/report.csv
'
START_TIME=$(date +%s)
docker exec "$CONTAINER_NAME" bash -c '
    cd /home/small_npes
    /home/infer_TempFix/infer/bin/infer run --pulse -- clang -c small_npes.c
    python3 /home/infer_TempFix/TempFixDataAnalysis.py
'
END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))
echo "start_time=$(date -d @${START_TIME} '+%Y-%m-%d %H:%M:%S')" > "${OUTPUT_DIR}/metadata.txt"
echo "end_time=$(date -d @${END_TIME} '+%Y-%m-%d %H:%M:%S')" >> "${OUTPUT_DIR}/metadata.txt"
echo "analysis_seconds=${ELAPSED}" >> "${OUTPUT_DIR}/metadata.txt"
echo ">>> ProveNFix analysis took ${ELAPSED} seconds"

# Save results
docker cp "${CONTAINER_NAME}:/home/infer_TempFix/TempFix-out/detail.txt" "${OUTPUT_DIR}/detail.txt" 2>/dev/null || true
docker cp "${CONTAINER_NAME}:/home/infer_TempFix/TempFix-out/report.csv" "${OUTPUT_DIR}/report.csv" 2>/dev/null || true
docker cp "${CONTAINER_NAME}:/home/small_npes/spec.c" "${OUTPUT_DIR}/spec.c" 2>/dev/null || true
docker cp "${CONTAINER_NAME}:/home/small_npes/small_npes.c" "${OUTPUT_DIR}/small_npes.c" 2>/dev/null || true
docker cp "${CONTAINER_NAME}:/home/small_npes/Makefile" "${OUTPUT_DIR}/Makefile" 2>/dev/null || true

echo ""
echo "=== Analysis Complete ==="
echo ""
echo "Results saved to: ${OUTPUT_DIR}"
ls -la "${OUTPUT_DIR}"
echo ""

# Summary
if [ -f "${OUTPUT_DIR}/detail.txt" ]; then
    DETAIL_LINES=$(wc -l < "${OUTPUT_DIR}/detail.txt")
    DETAIL_SIZE=$(stat -c%s "${OUTPUT_DIR}/detail.txt")
    if [ "$DETAIL_SIZE" -gt 10 ]; then
        BUG_COUNT=$(grep -c "Future-condition checking" "${OUTPUT_DIR}/detail.txt" 2>/dev/null || echo "0")
        echo ">>> Bugs found: ${BUG_COUNT}"
        echo ">>> detail.txt: ${DETAIL_LINES} lines"
        echo "----------------------------------------"
        head -50 "${OUTPUT_DIR}/detail.txt"
        if [ "$DETAIL_LINES" -gt 50 ]; then
            echo ""
            echo "... (showing first 50 of ${DETAIL_LINES} lines)"
        fi
        echo "----------------------------------------"
    else
        echo ">>> No bugs found"
    fi
else
    echo ">>> detail.txt not found"
fi

echo ""
echo "Container '${CONTAINER_NAME}' is still running."
echo "To attach: docker exec -it ${CONTAINER_NAME} bash"
echo "To remove: docker rm -f ${CONTAINER_NAME}"
echo ""

read -p "Attach to container now? (y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    docker exec -it "$CONTAINER_NAME" bash -c "cd /home/small_npes && bash"
fi
