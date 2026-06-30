#!/bin/bash
# Note: not using set -e to ensure script always reaches the attach prompt

IMAGE="yahuuuuui/fse24-prove_n_fix:ubuntu"
CONTAINER_NAME="p11kit-analysis"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT_DIR="${SCRIPT_DIR}/p11kit-analysis-results/${TIMESTAMP}"

echo "=== ProveNFix p11-kit Analysis ==="
echo "Using spec_p11.c on main branch"
echo ""
echo "Container: ${CONTAINER_NAME}"
echo "Results: ${OUTPUT_DIR}"
echo ""

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

# Download and configure p11-kit
echo ""
echo "[5/6] Downloading and configuring p11-kit (version 0.23.16)..."
docker exec "$CONTAINER_NAME" bash -c '
    cd /home
    if [ ! -d "p11-kit" ]; then
        git clone https://github.com/p11-glue/p11-kit.git
    fi
    cd p11-kit
    git checkout 0.23.16
    echo "Checked out version: $(git describe --tags 2>/dev/null || git rev-parse --short HEAD)"

    ./autogen.sh
    ./configure

    # Verify Makefile was created
    if [ -f Makefile ]; then
        echo "SUCCESS: Makefile created ($(wc -l < Makefile) lines)"
    else
        echo "ERROR: Makefile not created"
        echo "Configure may have failed - check if autotools are installed"
        exit 1
    fi
'

echo ""
echo "[6/6] Copying spec_p11.c and running TempFix..."
docker exec "$CONTAINER_NAME" bash -c '
    cd /home/p11-kit
    cp /home/infer_TempFix/spec_p11.c spec.c
    echo "Using spec.c with $(wc -l < spec.c) lines"
    # Clear old results
    rm -f /home/infer_TempFix/TempFix-out/detail.txt
    rm -f /home/infer_TempFix/TempFix-out/report.csv
'
START_TIME=$(date +%s)
docker exec "$CONTAINER_NAME" bash -c '
    cd /home/p11-kit
    /home/infer_TempFix/infer/bin/tempFix
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
docker cp "${CONTAINER_NAME}:/home/p11-kit/spec.c" "${OUTPUT_DIR}/spec.c" 2>/dev/null || true

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
    docker exec -it "$CONTAINER_NAME" bash -c "cd /home/p11-kit && bash"
fi
