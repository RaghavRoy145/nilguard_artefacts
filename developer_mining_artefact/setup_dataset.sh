#!/usr/bin/env bash
###############################################################################
# setup_dataset.sh — Clone a diverse corpus of C projects for the study.
#
# SELECTION CRITERIA (for an ICSE / FSE paper):
#   1. Domain diversity: systems, networking, databases, crypto, media,
#      language runtimes, utilities, security tools.
#   2. Scale: each project has 10k–500k+ LOC of C.
#   3. Maturity: actively maintained, widely deployed.
#   4. Independence from NilGuard's eval: we intentionally include some
#      projects from the paper (OpenSSL, Snort) for comparability AND
#      many projects NOT in the paper for generalisability.
#
# The corpus totals ~5–8 MLOC of C, well above the 1 MLOC threshold
# for empirical credibility at top venues.
###############################################################################

set -euo pipefail

DATASET_DIR="${1:-/Users/ragroy01/repos/semgrep_study/dataset}"
mkdir -p "$DATASET_DIR"

# Each entry: <name> <repo_url> <tag_or_commit>
# Using pinned versions for reproducibility.
declare -a PROJECTS=(
  # ---------- Systems ----------
  "redis        https://github.com/redis/redis.git                  7.2.4"
  "systemd      https://github.com/systemd/systemd.git              v255"
  "tmux         https://github.com/tmux/tmux.git                    3.4"
  "htop         https://github.com/htop-dev/htop.git                3.3.0"

  # ---------- Networking ----------
  "curl         https://github.com/curl/curl.git                    curl-8_6_0"
  "nginx        https://github.com/nginx/nginx.git                  release-1.25.4"
  "lighttpd     https://github.com/lighttpd/lighttpd1.4.git         lighttpd-1.4.73"
  "mosquitto    https://github.com/eclipse/mosquitto.git             v2.0.18"

  # ---------- Databases ----------
  "lmdb         https://github.com/LMDB/lmdb.git                   LMDB_0.9.31"

  # ---------- Crypto / Security ----------
  "openssl      https://github.com/openssl/openssl.git              openssl-3.2.1"
  "mbedtls      https://github.com/Mbed-TLS/mbedtls.git             v3.5.2"
  "libsodium    https://github.com/jedisct1/libsodium.git           1.0.19-RELEASE"

  # ---------- Media / Compression ----------
  "zstd         https://github.com/facebook/zstd.git                v1.5.5"
  "libpng       https://github.com/glennrp/libpng.git               v1.6.43"
  "libjpeg      https://github.com/libjpeg-turbo/libjpeg-turbo.git  3.0.2"

  # ---------- Language Runtimes ----------
  "lua          https://github.com/lua/lua.git                      v5.4.6"

  # ---------- Utilities ----------
  "jq           https://github.com/jqlang/jq.git                    jq-1.7.1"
  "git-src      https://github.com/git/git.git                      v2.44.0"

  # ---------- Security / IDS ----------
  "snort3       https://github.com/snort3/snort3.git                3.1.84.0"

  # ---------- From NilGuard eval (comparability) ----------
  "recutils     https://git.savannah.gnu.org/git/recutils.git       v1.8"
  "flex         https://github.com/westes/flex.git                  v2.6.4"
  "lxc          https://github.com/lxc/lxc.git                     lxc-5.0.3"
  "p11-kit      https://github.com/p11-glue/p11-kit.git             0.25.3"
  "x264         https://code.videolan.org/videolan/x264.git          master"

  # ---------- Additional well-known C projects ----------
  "coreutils    https://github.com/coreutils/coreutils.git          v9.4"
  "busybox      https://github.com/mirror/busybox.git               1_36_1"
  "iproute2     https://github.com/iproute2/iproute2.git            v6.7.0"
  "strace       https://github.com/strace/strace.git                v6.7"
)

echo "=========================================="
echo " NilGuard Empirical Study — Dataset Setup"
echo "=========================================="
echo "Target directory: $DATASET_DIR"
echo "Projects: ${#PROJECTS[@]}"
echo ""

for entry in "${PROJECTS[@]}"; do
    read -r name url ref <<< "$entry"
    dest="$DATASET_DIR/$name"

    if [ -d "$dest" ]; then
        echo "[SKIP] $name already cloned"
        continue
    fi

    echo "[CLONE] $name @ $ref ..."
    if git clone --depth 1 --branch "$ref" "$url" "$dest" 2>/dev/null; then
        echo "  ✓ $name cloned successfully"
    else
        # Fallback: clone without --branch (for repos where tag doesn't work)
        echo "  [RETRY] Cloning $name without specific ref..."
        git clone --depth 1 "$url" "$dest" 2>/dev/null || echo "  ✗ FAILED: $name"
    fi
done

echo ""
echo "=== Dataset Summary ==="
echo ""
total_c=0
for dir in "$DATASET_DIR"/*/; do
    name=$(basename "$dir")
    count=$(find "$dir" -name '*.c' -o -name '*.h' | wc -l)
    loc=$(find "$dir" -name '*.c' | xargs cat 2>/dev/null | wc -l)
    echo "  $name: $count files, ~${loc} LOC"
    total_c=$((total_c + loc))
done
echo ""
echo "Total C LOC: ~${total_c}"
echo "Done."
