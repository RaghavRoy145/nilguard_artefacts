#!/bin/bash
set -e

IMAGE="yahuuuuui/fse24-prove_n_fix:ubuntu"
CONTAINER_NAME="snort-analysis"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT_DIR="${SCRIPT_DIR}/snort-analysis-results/${TIMESTAMP}"

SNORT_DIR="snort"
SNORT_ARCHIVE="/home/vorashil/Projects/EffFix-artifact/effFix-benchmark/archives/snort.tar.gz"
DAQ_VERSION="2.0.7"

echo "=== ProveNFix Snort 2.9.13 Analysis ==="
echo "Using existing spec_snort-2.9.13.c on main branch"
echo ""
echo "Snort archive: ${SNORT_ARCHIVE}"
echo "DAQ version: ${DAQ_VERSION}"
echo "Container: ${CONTAINER_NAME}"
echo "Results: ${OUTPUT_DIR}"
echo ""

mkdir -p "$OUTPUT_DIR"

# Pull image
echo "[1/7] Pulling Docker image..."
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
echo "[2/7] Creating container..."
docker run -d --name "$CONTAINER_NAME" "$IMAGE" tail -f /dev/null

# Use main branch
echo "[3/7] Using main branch..."
docker exec "$CONTAINER_NAME" bash -c '
    cd /home/infer_TempFix
    git stash
    git checkout main
    echo "Branch: $(git branch --show-current)"
'

echo ""
echo "[4/7] Building ProveNFix..."
docker exec "$CONTAINER_NAME" bash -c '
    cd /home/infer_TempFix
    ./compile
'

# Install Snort dependencies and download DAQ
echo ""
echo "[5/7] Installing dependencies and downloading DAQ..."
docker exec "$CONTAINER_NAME" bash -c "
    apt-get update
    apt-get install -y libpcap-dev libpcre3-dev libdumbnet-dev bison flex zlib1g-dev liblzma-dev libssl-dev libnghttp2-dev

    cd /home

    # Download and build DAQ
    if [ ! -d \"daq-${DAQ_VERSION}\" ]; then
        curl -L https://www.snort.org/downloads/archive/snort/daq-${DAQ_VERSION}.tar.gz -o daq-${DAQ_VERSION}.tar.gz
        tar -xzf daq-${DAQ_VERSION}.tar.gz
    fi
    cd daq-${DAQ_VERSION}
    # Touch autotools files to prevent regeneration
    touch aclocal.m4 configure Makefile.am Makefile.in config.h.in
    ./configure
    make
    make install
    ldconfig
"

# Copy Snort archive from host
echo ""
echo "[6/7] Copying and extracting Snort archive..."
docker cp "${SNORT_ARCHIVE}" "${CONTAINER_NAME}:/home/snort.tar.gz"
docker exec "$CONTAINER_NAME" bash -c "
    cd /home
    if [ ! -d \"${SNORT_DIR}\" ]; then
        tar -xzf snort.tar.gz
    fi
"

# Configure Snort
echo ""
echo "[7/8] Configuring Snort..."
docker exec "$CONTAINER_NAME" bash -c "
    cd /home/${SNORT_DIR}
    export PATH=/usr/local/bin:\$PATH
    export LD_LIBRARY_PATH=/usr/local/lib:\$LD_LIBRARY_PATH
    ldconfig
    ./configure --enable-sourcefire --disable-open-appid
"

echo ""
echo "[8/8] Copying spec_snort-2.9.13.c and running TempFix..."
docker exec "$CONTAINER_NAME" bash -c "
    cd /home/${SNORT_DIR}
    cp /home/infer_TempFix/spec_snort-2.9.13.c spec.c
    echo \"Using spec.c with \$(wc -l < spec.c) lines\"
    rm -f /home/infer_TempFix/TempFix-out/detail.txt
    rm -f /home/infer_TempFix/TempFix-out/report.csv
"
START_TIME=$(date +%s)
docker exec "$CONTAINER_NAME" bash -c "
    cd /home/${SNORT_DIR}
    /home/infer_TempFix/infer/bin/tempFix
"
END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))
echo "start_time=$(date -d @${START_TIME} '+%Y-%m-%d %H:%M:%S')" > "${OUTPUT_DIR}/metadata.txt"
echo "end_time=$(date -d @${END_TIME} '+%Y-%m-%d %H:%M:%S')" >> "${OUTPUT_DIR}/metadata.txt"
echo "analysis_seconds=${ELAPSED}" >> "${OUTPUT_DIR}/metadata.txt"
echo ">>> ProveNFix analysis took ${ELAPSED} seconds"

# Disable set -e so script continues to prompt even if errors occurred
set +e

# Save results
docker cp "${CONTAINER_NAME}:/home/infer_TempFix/TempFix-out/detail.txt" "${OUTPUT_DIR}/detail.txt" 2>/dev/null || true
docker cp "${CONTAINER_NAME}:/home/infer_TempFix/TempFix-out/report.csv" "${OUTPUT_DIR}/report.csv" 2>/dev/null || true
docker cp "${CONTAINER_NAME}:/home/${SNORT_DIR}/spec.c" "${OUTPUT_DIR}/spec.c" 2>/dev/null || true
docker cp "${CONTAINER_NAME}:/home/${SNORT_DIR}/infer-out/logs" "${OUTPUT_DIR}/infer-logs.txt" 2>/dev/null || true

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
    docker exec -it "$CONTAINER_NAME" bash -c "cd /home/${SNORT_DIR} && bash"
fi
