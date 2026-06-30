#!/bin/bash
set -e

IMAGE="yahuuuuui/fse24-prove_n_fix:ubuntu"
CONTAINER_NAME="openssl-analysis"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT_DIR="${SCRIPT_DIR}/openssl-analysis-results/${TIMESTAMP}"

echo "=== ProveNFix OpenSSL Analysis ==="
echo "Container name: ${CONTAINER_NAME}"
echo "Results will be saved to: ${OUTPUT_DIR}"
echo ""

# Create output directory
mkdir -p "$OUTPUT_DIR"

# Pull the latest image
echo "[1/7] Pulling Docker image..."
docker pull "$IMAGE"

# Check if container already exists
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "Container '${CONTAINER_NAME}' already exists."
    read -p "Remove it and start fresh? (y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        docker rm -f "$CONTAINER_NAME"
    else
        echo "Attaching to existing container..."
        docker start "$CONTAINER_NAME" 2>/dev/null || true
        docker exec -it "$CONTAINER_NAME" bash
        exit 0
    fi
fi

# Start container in detached mode
echo "[2/7] Creating container '${CONTAINER_NAME}'..."
docker run -d --name "$CONTAINER_NAME" "$IMAGE" tail -f /dev/null

# Stay on main branch for bug detection
echo "[3/7] Using main branch..."
docker exec "$CONTAINER_NAME" bash -c '
    cd /home/infer_TempFix
    git stash
    git checkout main
    echo "Current branch: $(git branch --show-current)"
'

echo ""
echo "[4/7] Rebuilding ProveNFix (this may take a while)..."
docker exec "$CONTAINER_NAME" bash -c '
    cd /home/infer_TempFix
    ./compile
'

echo ""
echo "[5/7] Downloading and configuring OpenSSL 3.0.0..."
docker exec "$CONTAINER_NAME" bash -c '
    cd /home
    if [ ! -d "openssl-openssl-3.0.0" ]; then
        curl -L https://github.com/openssl/openssl/archive/refs/tags/openssl-3.0.0.tar.gz -o openssl-3.0.0.tar.gz
        tar -xzf openssl-3.0.0.tar.gz
    fi
    cd openssl-openssl-3.0.0
    ./Configure --prefix=/usr/local/ssl --openssldir=/usr/local/ssl \
        "-Wl,-rpath,\$(LIBRPATH)"
'

echo ""
echo "[6/7] Copying spec_openssl.c and running TempFix..."
docker exec "$CONTAINER_NAME" bash -c '
    cd /home/openssl-openssl-3.0.0
    cp /home/infer_TempFix/spec_openssl.c spec.c
    echo "Using spec.c with $(wc -l < spec.c) lines"
    rm -f /home/infer_TempFix/TempFix-out/detail.txt
    rm -f /home/infer_TempFix/TempFix-out/report.csv
'
START_TIME=$(date +%s)
docker exec "$CONTAINER_NAME" bash -c '
    cd /home/openssl-openssl-3.0.0
    /home/infer_TempFix/infer/bin/tempFix
'
END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))
echo "start_time=$(date -d @${START_TIME} '+%Y-%m-%d %H:%M:%S')" > "${OUTPUT_DIR}/metadata.txt"
echo "end_time=$(date -d @${END_TIME} '+%Y-%m-%d %H:%M:%S')" >> "${OUTPUT_DIR}/metadata.txt"
echo "analysis_seconds=${ELAPSED}" >> "${OUTPUT_DIR}/metadata.txt"
echo ">>> ProveNFix analysis took ${ELAPSED} seconds"

echo ""
echo "[7/7] Copying results to host..."
docker cp "${CONTAINER_NAME}:/home/infer_TempFix/TempFix-out/detail.txt" "${OUTPUT_DIR}/detail.txt" 2>/dev/null || echo "Warning: detail.txt not found"
docker cp "${CONTAINER_NAME}:/home/infer_TempFix/TempFix-out/report.csv" "${OUTPUT_DIR}/report.csv" 2>/dev/null || echo "Warning: report.csv not found"
docker cp "${CONTAINER_NAME}:/home/openssl-openssl-3.0.0/spec.c" "${OUTPUT_DIR}/spec.c" 2>/dev/null || echo "Warning: spec.c not found"
docker cp "${CONTAINER_NAME}:/home/openssl-openssl-3.0.0/infer-out/logs" "${OUTPUT_DIR}/infer-logs.txt" 2>/dev/null || true

echo ""
echo "=== Analysis Complete ==="
echo ""
echo "Results saved to: ${OUTPUT_DIR}"
ls -la "${OUTPUT_DIR}"
echo ""

if [ -f "${OUTPUT_DIR}/report.csv" ]; then
    echo ">>> Analysis Summary (from report.csv):"
    echo "----------------------------------------"
    echo "Files analyzed: $(wc -l < "${OUTPUT_DIR}/report.csv")"
    echo "Files with issues: $(awk -F',' '$7 != "0" || $8 != "0" {count++} END {print count+0}' "${OUTPUT_DIR}/report.csv")"
    echo "----------------------------------------"
fi

if [ -f "${OUTPUT_DIR}/detail.txt" ]; then
    DETAIL_LINES=$(wc -l < "${OUTPUT_DIR}/detail.txt")
    DETAIL_SIZE=$(stat -c%s "${OUTPUT_DIR}/detail.txt")
    if [ "$DETAIL_SIZE" -gt 10 ]; then
        BUG_COUNT=$(grep -c "Future-condition checking" "${OUTPUT_DIR}/detail.txt" 2>/dev/null || echo "0")
        echo ">>> Bugs found: ${BUG_COUNT}"
        echo ">>> detail.txt: ${DETAIL_LINES} lines, ${DETAIL_SIZE} bytes"
        echo "----------------------------------------"
        head -100 "${OUTPUT_DIR}/detail.txt"
        if [ "$DETAIL_LINES" -gt 100 ]; then
            echo ""
            echo "... (${DETAIL_LINES} total lines, showing first 100)"
        fi
        echo "----------------------------------------"
    else
        echo ">>> detail.txt is empty (no bugs found matching the spec)"
    fi
else
    echo ">>> detail.txt not found"
fi

echo ""
echo "Container '${CONTAINER_NAME}' is still running."
echo "To attach: docker exec -it ${CONTAINER_NAME} bash"
echo "To stop:   docker stop ${CONTAINER_NAME}"
echo "To remove: docker rm -f ${CONTAINER_NAME}"
echo ""

read -p "Attach to container now? (y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    docker exec -it "$CONTAINER_NAME" bash -c 'cd /home/openssl-openssl-3.0.0 && bash'
fi
