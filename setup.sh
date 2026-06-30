
#!/usr/bin/env bash
###############################################################################
# setup_dataset.sh — NilGuard Artifact: Evaluation Dataset Setup
#
# Clones the 8 large real-world C projects used in the NilGuard evaluation
# (Table I, Section VI), pinned to the exact commits/tags from the paper.
#
# The small-programs suite and LLM-generated tests ship with this repo
# under  tests/small_programs/  and  tests/llm_generated/.
#
# The safety-mining corpus (Table II, Section V) is maintained in a
# separate artifact and can be merged independently.
#
# Usage:
#   ./setup_dataset.sh [WORKSPACE_DIR]
#   ./setup_dataset.sh --help
#
# Requires: git ≥ 2.25, wget or curl (for Snort 2.9.13 tarball)
###############################################################################

set -euo pipefail

# ─── Colour helpers ──────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ─── Argument parsing ────────────────────────────────────────────────────────
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat << 'EOF'
Usage: ./setup_dataset.sh [WORKSPACE_DIR]

Clones the 8 large C projects from the NilGuard evaluation (Table I)
at their exact pinned commits/tags.

ARGUMENTS:
    WORKSPACE_DIR   Directory to clone into (default: ./dataset)

EXAMPLES:
    ./setup_dataset.sh               # default location
    ./setup_dataset.sh /data/nilguard

DISK SPACE:  ~3 GB (full clones needed for commit-pinned projects)

After cloning, each project must be configured before running NilGuard.
See README.md § Configuring Projects for per-project commands.

EOF
    exit 0
fi

WORK_DIR="${1:-./dataset}"
mkdir -p "$WORK_DIR"
WORK_DIR="$(cd "$WORK_DIR" && pwd)"

###############################################################################
#  Evaluation Corpus — 8 Large Real-World Projects (Table I, Section VI)
#
#  Derived from the PNF-FSE and EffFix benchmarks.
#  Four projects (Swoole, WavPack, inetutils, grub) were excluded because
#  they did not compile with Pulse-X.
#
#  7 projects are cloned from git; Snort 2.9.13 is downloaded as a tarball
#  because the Snort 2.x C source was never published in a public git repo.
#  (The snort3/snort3 GitHub repo is Snort 3 / Snort++, an unrelated C++
#  rewrite.)
#
#  Format:  name|repo_url|ref_type|ref_value|kloc
#     ref_type  "tag"    → git clone --depth 1 --branch <ref>
#               "commit" → full clone, then git checkout <ref>
###############################################################################
declare -a GIT_PROJECTS=(
    "flex|https://github.com/westes/flex.git|commit|d3de49f|23.9"
    "lxc|https://github.com/lxc/lxc.git|commit|72cc48f|62.4"
    "x264|https://code.videolan.org/videolan/x264.git|commit|d4099dd|64.6"
    "p11-kit|https://github.com/p11-glue/p11-kit.git|tag|0.23.16|76.2"
    "recutils|https://git.savannah.gnu.org/git/recutils.git|tag|v1.8|81.9"
    "openssl-1|https://github.com/openssl/openssl.git|tag|OpenSSL_1_0_1h|336.0"
    "openssl-3|https://github.com/openssl/openssl.git|tag|openssl-3.1.2|556.4"
)

# Snort 2.9.13 — tarball download (no public git repo for Snort 2.x)
SNORT_NAME="snort"
SNORT_VERSION="2.9.13"
SNORT_KLOC="378.0"
SNORT_TARBALL="snort-${SNORT_VERSION}.tar.gz"
SNORT_URL="https://www.snort.org/downloads/archive/snort/${SNORT_TARBALL}"
SNORT_URL_ALT="https://www.snort.org/downloads/snort/${SNORT_TARBALL}"

###############################################################################
#  Helpers
###############################################################################

log_skip() { echo -e "    ${CYAN}[SKIP]${NC} $*"; }
log_warn() { echo -e "    ${YELLOW}[WARN]${NC} $*"; }

clone_project() {
    local name="$1" url="$2" ref_type="$3" ref_value="$4" dest="$5"

    if [ -d "$dest" ]; then
        log_skip "already present"
        return 0
    fi

    # Try shallow clone with tag
    if [ "$ref_type" = "tag" ]; then
        echo -n "    Shallow clone @ tag ${BOLD}$ref_value${NC} ... "
        if git clone --depth 1 --branch "$ref_value" "$url" "$dest" 2>/dev/null; then
            echo -e "${GREEN}✓${NC}"
            return 0
        fi
        echo -e "${YELLOW}tag not found, falling back to full clone${NC}"
    fi

    # Full clone + checkout (required for short commit hashes)
    echo -n "    Full clone ... "
    if ! git clone "$url" "$dest" 2>/dev/null; then
        echo -e "${RED}✗ clone failed${NC}"
        return 1
    fi
    echo -e "${GREEN}✓${NC}"

    echo -n "    Checkout ${BOLD}$ref_value${NC} ... "
    if (cd "$dest" && git checkout "$ref_value" -- 2>/dev/null); then
        echo -e "${GREEN}✓${NC}"
        return 0
    else
        echo -e "${RED}✗ checkout failed${NC}"
        log_warn "commit $ref_value not found — left at HEAD"
        return 1
    fi
}

download_snort() {
    local dest="$WORK_DIR/$SNORT_NAME"

    if [ -d "$dest" ]; then
        log_skip "already present"
        return 0
    fi

    local tarball_path="$WORK_DIR/$SNORT_TARBALL"

    # Download tarball
    if [ ! -f "$tarball_path" ]; then
        echo -n "    Downloading ${BOLD}${SNORT_TARBALL}${NC} ... "
        if command -v wget >/dev/null 2>&1; then
            if wget -q -O "$tarball_path" "$SNORT_URL" 2>/dev/null || \
               wget -q -O "$tarball_path" "$SNORT_URL_ALT" 2>/dev/null; then
                echo -e "${GREEN}✓${NC}"
            else
                echo -e "${RED}✗${NC}"
                rm -f "$tarball_path"
                echo ""
                echo -e "    ${YELLOW}Snort 2.9.13 could not be downloaded automatically.${NC}"
                echo -e "    ${YELLOW}The snort.org archive may require authentication.${NC}"
                echo ""
                echo -e "    To set up manually:"
                echo -e "      1. Download snort-${SNORT_VERSION}.tar.gz from https://www.snort.org/downloads"
                echo -e "         (navigate to 'Snort 2 → View Snort Previous Releases')"
                echo -e "      2. Place it in: ${CYAN}${WORK_DIR}/${NC}"
                echo -e "      3. Re-run this script"
                return 1
            fi
        elif command -v curl >/dev/null 2>&1; then
            if curl -sL -o "$tarball_path" "$SNORT_URL" 2>/dev/null || \
               curl -sL -o "$tarball_path" "$SNORT_URL_ALT" 2>/dev/null; then
                echo -e "${GREEN}✓${NC}"
            else
                echo -e "${RED}✗${NC}"
                rm -f "$tarball_path"
                echo ""
                echo -e "    ${YELLOW}Download failed. See manual instructions above.${NC}"
                return 1
            fi
        else
            echo -e "${RED}✗ neither wget nor curl found${NC}"
            return 1
        fi
    else
        echo -e "    Tarball already downloaded"
    fi

    # Verify the tarball is actually a gzip file (not an HTML error page)
    if ! file "$tarball_path" | grep -qi 'gzip\|tar'; then
        echo -e "    ${RED}Downloaded file is not a valid tarball (possibly an auth page)${NC}"
        rm -f "$tarball_path"
        echo -e "    ${YELLOW}Download snort-${SNORT_VERSION}.tar.gz manually from snort.org${NC}"
        return 1
    fi

    # Extract
    echo -n "    Extracting ... "
    mkdir -p "$dest"
    if tar -xzf "$tarball_path" -C "$dest" --strip-components=1 2>/dev/null; then
        echo -e "${GREEN}✓${NC}"
        rm -f "$tarball_path"
        return 0
    else
        echo -e "${RED}✗ extraction failed${NC}"
        rm -rf "$dest"
        return 1
    fi
}

###############################################################################
#  Main
###############################################################################

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║     NilGuard Artifact — Evaluation Dataset Setup       ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Workspace : ${CYAN}$WORK_DIR${NC}"
echo -e "  Projects  : 8 (7 git + 1 tarball)"
echo ""

# Check deps
if ! command -v git >/dev/null 2>&1; then
    echo -e "${RED}[ERR]${NC}  git is not installed."
    exit 1
fi
if ! command -v wget >/dev/null 2>&1 && ! command -v curl >/dev/null 2>&1; then
    echo -e "${RED}[ERR]${NC}  wget or curl is required (for Snort tarball download)."
    exit 1
fi

succeeded=0
failed=0
failed_list=""
total=8
i=0

# ─── Git-cloned projects ────────────────────────────────────────────────────
for entry in "${GIT_PROJECTS[@]}"; do
    IFS='|' read -r name url ref_type ref_value kloc <<< "$entry"
    ((i++)) || true

    echo -e "  ${GREEN}[$i/$total]${NC} ${BOLD}$name${NC}  (~${kloc} kLoC)"

    if clone_project "$name" "$url" "$ref_type" "$ref_value" "$WORK_DIR/$name"; then
        ((succeeded++)) || true
    else
        ((failed++)) || true
        failed_list+="  - $name ($ref_value)\n"
    fi
    echo ""
done

# ─── Snort 2.9.13 (tarball) ─────────────────────────────────────────────────
((i++)) || true
echo -e "  ${GREEN}[$i/$total]${NC} ${BOLD}$SNORT_NAME${NC}  (~${SNORT_KLOC} kLoC)  [tarball: Snort ${SNORT_VERSION}]"

if download_snort; then
    ((succeeded++)) || true
else
    ((failed++)) || true
    failed_list+="  - snort (${SNORT_VERSION})\n"
fi
echo ""

# ─── Summary ────────────────────────────────────────────────────────────────
echo -e "${BOLD}=== Evaluation Corpus Summary ===${NC}"
echo ""
total_c=0
for dir in "$WORK_DIR"/*/; do
    [ -d "$dir" ] || continue
    name=$(basename "$dir")
    file_count=$(find "$dir" \( -name '*.c' -o -name '*.h' \) 2>/dev/null | wc -l)
    loc=$(find "$dir" -name '*.c' 2>/dev/null -exec cat {} + 2>/dev/null | wc -l)
    printf "  %-20s %6d files   ~%d C LOC\n" "$name" "$file_count" "$loc"
    total_c=$((total_c + loc))
done
echo ""
printf "  ${BOLD}%-20s            ~%d C LOC${NC}\n" "TOTAL" "$total_c"
echo ""

echo -e "  Cloned : ${GREEN}$succeeded${NC}"
echo -e "  Failed : ${RED}$failed${NC}"

if [ -n "$failed_list" ]; then
    echo ""
    echo -e "  ${RED}Failed projects:${NC}"
    echo -e "$failed_list"
fi

echo ""
echo -e "  ${YELLOW}Next: install dependencies and configure each project.${NC}"
echo -e "  ${YELLOW}See README.md § Configuring Projects.${NC}"
echo ""

cat > "$WORK_DIR/setup_summary.txt" << EOF
NilGuard Evaluation Dataset Setup
Generated : $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Workspace : $WORK_DIR
Cloned    : $succeeded
Failed    : $failed
EOF
