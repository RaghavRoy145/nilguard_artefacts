# Repair Evaluation Artifact

Reproduces the three experiments from Evaluation, comparing NilGuard against PNF-FSE across three datasets.

---

## Layout

```
repair_evaluation_artefact/
├── README.md                   ← you are here
├── setup.sh                    ← clones/downloads the 8 large projects into dataset/
├── small_datasets/
│   ├── llm_generated/          ← 20 LLM-generated C programs (Supplementary H)
│   │   ├── test01_simple_null.c
│   │   ├── ...
│   │   └── test20_cast_null.c
│   └── small_programs/         ← 54 NPE test file
│       └── small_npes.c
├── dataset/                    ← created by setup.sh
│   ├── flex/                      
│   ├── lxc/                      
│   ├── x264/                  
│   ├── p11-kit/             
│   ├── recutils/               
│   ├── openssl-1/              
│   ├── snort/                 
│   └── openssl-3/             
└── results/
    ├── nilguard/
    │   ├── large_programs/
    │   ├── llm_generated/
    │   └── small_programs/
    └── pnffse/
        ├── large_programs/
        ├── llm_generated/
        └── small_programs/
```

---

## Pre-Collected Results
 
The `results/` directory contains the outputs from both tools, pre-collected so that the experiments can be inspected without re-running the full analysis.
 
### NilGuard output format
 
NilGuard produces one JSON file per analysed source file. Each JSON file contains the detected NPEs, the transformation schema applied (SKIP, REPLACE, EVADE, etc.), and the location to apply it.
 
```
results/nilguard/large_programs/flex/
├── report.txt              ← summary of all NPEs and patches for the project from Pulse-X
├── src_buf.c.json          ← per-file: NPE locations, schemas, patch sites
├── src_ccl.c.json
└── ...
```
 
### PNF-FSE output format
 
PNF-FSE produces a `detail.txt` with the generated patches, a `report.csv` summary, a `metadata.txt`, and the `spec.c` specification file used during analysis. Results from previous runs are stored under `previous_run/`.
 
```
results/pnffse/large_programs/flex/
├── run_flex_analysis.sh    ← script to re-run this analysis
└── previous_run/
    ├── detail.txt          ← generated patches
    ├── metadata.txt        ← timing and configuration
    ├── report.csv          ← structured summary
    └── spec.c              ← specification file used
```
 
For the LLM-generated dataset, each test produces a `_detail.txt` (patches) and `_output.txt` (full analysis log):
 
```
results/pnffse/llm_generated/
├── run_all_tests.sh                  ← script to re-run all 20 tests
├── ...
```
 
### Re-running PNF-FSE
 
Each PNF-FSE results directory includes a script to reproduce the analysis. These scripts use the PNF-FSE Docker image (`yahuuuuui/fse24-prove_n_fix:ubuntu`), set up the container, build PNF-FSE, run the analysis, and copy results out.
 
```bash
# Re-run PNF-FSE on all 20 LLM-generated programs
cd results/pnffse/llm_generated
./run_all_tests.sh
 
# Example Re-run PNF-FSE on a specific large project
cd results/pnffse/large_programs/flex
./run_flex_analysis.sh
```
 
Requires Docker. The scripts are self-contained and will pull the image automatically.
 
---

## Prerequisites

**Tested environment:** Ubuntu 20.04, Intel x86 i5, 32 GB RAM.

**Disk space:** ~3 GB for the large project dataset, and ~50GB for NilGuard as it generates many temp files!

---

## Step 1: Build NilGuard

NilGuard is built on a fork of Infer's Pulse-X engine. Download and build it from source.

### 1a. Download

Download the NilGuard source as a zip from the anonymised repository:

> **https://anonymous.4open.science/r/infer-248E/**

Click **"ZIP"** and unzip:

```bash
unzip infer-248E.zip -d nilguard-src
cd nilguard-src
```

### 1b. Build

Follow the standard Infer build instructions from https://github.com/facebook/infer/blob/main/INSTALL.md.

In summary:

```bash
# Install system dependencies (Ubuntu/Debian)
sudo apt-get install -y \
    opam cmake pkg-config sqlite3 libsqlite3-dev \
    python3 default-jdk

# Set up opam and build
opam init --yes
opam switch create infer 4.14.0
eval $(opam env)
./build-infer.sh
```

After building, the analyser binary is at:

```
nilguard-src/infer/bin/infer
```

Set it for convenience:

```bash
export INFER_BIN="/path/to/nilguard-src/infer/bin/infer"
```

---

## Step 2: Set Up the Large Project Dataset

The `setup.sh` script clones the 8 evaluation projects at their exact pinned versions from Table I.

```bash
cd repair_evaluation_artefact
chmod +x setup.sh
./setup.sh
```

Seven projects are cloned from git. Snort 2.9.13 is downloaded as a tarball because the Snort 2.x C source was not published in a public git repository.

The script is idempotent — already-present projects are skipped.

---

## Step 3: Install System Dependencies

Before configuring the large projects, install the required build libraries.

### All projects at once (Ubuntu/Debian)

```bash
sudo apt-get install -y \
    build-essential autoconf automake libtool pkg-config perl m4 \
    bison flex help2man gettext texinfo nasm \
    libssl-dev libpcap-dev libpcre3-dev libdumbnet-dev zlib1g-dev \
    liblzma-dev libluajit-5.1-dev libnghttp2-dev libdaq-dev \
    libseccomp-dev libcap-dev libapparmor-dev libselinux1-dev \
    libgnutls28-dev liblz4-dev libtasn1-6-dev libffi-dev \
    libgcrypt20-dev uuid-dev
```

### Per-project dependencies

| Project | Key packages |
|---|---|
| flex | `autoconf automake libtool m4 bison help2man gettext` |
| lxc | `autoconf automake libtool libseccomp-dev libcap-dev libapparmor-dev libselinux1-dev libgnutls28-dev liblz4-dev` |
| x264 | `nasm` (or `yasm`) |
| p11-kit | `autoconf automake libtool libtasn1-6-dev libffi-dev` |
| recutils | `autoconf automake libtool gettext texinfo libgcrypt20-dev uuid-dev help2man` |
| openssl-1 | `perl` |
| snort | `libpcap-dev libpcre3-dev libdumbnet-dev libdaq-dev zlib1g-dev liblzma-dev libssl-dev libluajit-5.1-dev libnghttp2-dev` |
| openssl-3 | `perl` |

---

## Step 4: Configure Large Projects

Each project must be configured so that `make` produces a working build. NilGuard intercepts `` invocations during `make`, which is why a working build is required.

### flex

The checked-out source includes a pre-generated `configure` script.

```bash
cd dataset/flex
./configure CC=gcc
```

### lxc

No pre-generated `configure` script. Run `autogen.sh` first.

```bash
cd dataset/lxc
./autogen.sh
./configure CC=gcc
```

### x264

Custom `configure` script (not autotools).

```bash
cd dataset/x264
./configure
```

### p11-kit

No pre-generated `configure` script. Run `autogen.sh` first.

```bash
cd dataset/p11-kit
./autogen.sh
./configure CC=gcc
```

### recutils

No pre-generated `configure` script. Run `bootstrap` first (not `autogen.sh`).

```bash
cd dataset/recutils
./bootstrap
./configure CC=gcc
```

### openssl-1 (OpenSSL 1.0.1h)

OpenSSL uses its own `config` script (not autotools).

```bash
cd dataset/openssl-1
CC=gcc ./config
```

### snort (Snort 2.9.13)

The tarball includes a pre-generated `configure` script.

```bash
cd dataset/snort
./configure CC=gcc
```

### openssl-3 (OpenSSL 3.1.2)

OpenSSL uses its own `config` script (not autotools).

```bash
cd dataset/openssl-3
CC=gcc ./config
```

---

## Step 5: Run NilGuard

### Large projects

Run NilGuard by intercepting the `make` build for each configured project:

```bash
cd dataset/<project>
$INFER_BIN --keep-going --pulse-only -- make
```

`--pulse-only` restricts analysis to the Pulse-X engine (ISL-based). `--keep-going` continues past individual file errors. Results are written to `infer-out/` inside the project directory.

For debug output (error traces, IL triples):

```bash
$INFER_BIN --keep-going --debug --pulse-only -- make
```

**Important:** Infer only analyses files that `make` actually compiles. If a project is already built, `make` is a no-op and Infer sees nothing. Always run `make clean` before re-analysis:

```bash
make clean
$INFER_BIN --keep-going --pulse-only -- make
```

### Small datasets

For single-file programs, compile directly with `cc -c` instead of `make`:

```bash
# All LLM-generated programs
for f in small_datasets/llm_generated/test*.c; do
    echo "=== $(basename $f) ==="
    $INFER_BIN --keep-going --pulse-only -- cc -c "$f"
done

# Small programs suite
$INFER_BIN --keep-going --pulse-only -- cc -c small_datasets/small_programs/small_npes.c
```

---

## Reproducing the Experiments

### Experiment 1 — Repair Safety and Correctness (§ VI-B)

Run NilGuard on all three datasets as described above. Patches are manually classified as **C** (globally safe), **CḠ** (locally but not globally safe), **I** (incorrect), or **N** (no patch).

**Expected (Table I):** NilGuard produces 31.9% safe patches (vs. PNF-FSE 3.2%) with an incorrect rate of 1.2% (vs. 7.3%).

### Experiment 2 — Pareto Optimality (§ VI-C)

Cost profiles (D, W) are emitted in `infer-out/` alongside each patch. D counts inserted guards; W counts captured bystander writes.

**Expected:** Median cost (1, 0) across 114 safe repairs.

### Experiment 3 — False Positive Patch Rate (§ VI-D)

For each reported NPE, compare the surrounding code against the valid ok-triples (documented in the companion technical report) to determine whether the report is a false positive.

**Expected:** PNF-FSE 92% false-positive patches (159/173) vs. NilGuard 48.3% (74/153).

---

## Evaluation Dataset (Table I)

Derived from the PNF-FSE and EffFix benchmarks. Four projects (Swoole, WavPack, inetutils, grub) were excluded because they did not compile with Pulse-X. Total: 1,579.4 kLoC.

| Project | kLoC | Ref | Source |
|---|---|---|---|
| flex | 23.9 | `d3de49f` | github.com/westes/flex (git) |
| lxc | 62.4 | `72cc48f` | github.com/lxc/lxc (git) |
| x264 | 64.6 | `d4099dd` | code.videolan.org/videolan/x264 (git) |
| p11-kit | 76.2 | `0.23.16` | github.com/p11-glue/p11-kit (git) |
| recutils | 81.9 | `v1.8` | git.savannah.gnu.org/git/recutils (git) |
| OpenSSL 1.0.1h | 336.0 | `OpenSSL_1_0_1h` | github.com/openssl/openssl (git) |
| Snort 2.9.13 | 378.0 | `2.9.13` | snort.org/downloads (tarball) |
| OpenSSL 3.1.2 | 556.4 | `openssl-3.1.2` | github.com/openssl/openssl (git) |

---