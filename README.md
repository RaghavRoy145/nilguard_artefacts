# NilGuard — Artifact

**NILGUARD: Minimising Patches for Null Pointer Errors with Incorrectness Separation Logic**

NilGuard is a novel Automated Program Repair tool built on Incorrectness Separation Logic (ISL). It formalises NPE removal as a multi-objective optimisation problem that simultaneously minimises guard overhead and bystander writes, producing Pareto-optimal patches that are locally safe by construction.

---

## Repository Layout

```
nilguard-artifact/
├── README.md                       ← you are here
├── setup_dataset.sh                ← clones/downloads eval projects at pinned versions
├── nilguard/                       ← NilGuard / Pulse-X fork source
├── tests/
│   ├── small_programs/             ← 54 NPE test cases (ships with repo)
│   └── llm_generated/              ← 20 LLM-generated C programs (ships with repo)
├── dataset/                        ← created by setup_dataset.sh
│   ├── flex/                          23.9 kLoC  @ d3de49f
│   ├── lxc/                           62.4 kLoC  @ 72cc48f
│   ├── x264/                          64.6 kLoC  @ d4099dd
│   ├── p11-kit/                       76.2 kLoC  @ 0.23.16
│   ├── recutils/                      81.9 kLoC  @ v1.8
│   ├── openssl-1/                    336.0 kLoC  @ OpenSSL_1_0_1h
│   ├── snort/                        378.0 kLoC  @ 2.9.13 (tarball)
│   └── openssl-3/                   556.4 kLoC  @ openssl-3.1.2
├── safety_mining/                  ← separate artifact (merged independently)
└── results/                        ← reproduction outputs
```

---

## Quick Start

```bash
# 1. Clone the artifact
git clone https://github.com/<org>/nilguard-artifact.git
cd nilguard-artifact

# 2. Clone the evaluation dataset
chmod +x setup_dataset.sh
./setup_dataset.sh

# 3. Install deps, configure a project, run NilGuard
sudo apt-get install -y build-essential perl
cd dataset/openssl-1
CC=gcc ./config
$INFER_BIN --keep-going --pulse-only -- make
```

---

## Prerequisites

**Tested environment:** Ubuntu 20.04, Intel x86 i5, 32 GB RAM.

**Disk space:** ~3 GB for the evaluation dataset.

### NilGuard / Pulse-X

NilGuard is built on a fork of Infer's Pulse-X engine. Once built, the binary lives at:

```
<INFER_DIR>/infer/bin/infer
```

```bash
cd nilguard
opam switch create nilguard 4.14.0
eval $(opam env)
opam install . --deps-only
make
export INFER_BIN="$(pwd)/infer/bin/infer"
cd ..
```

NilGuard works by intercepting compiler invocations during `make`. It wraps each `gcc` call, analyses the compilation unit with Pulse-X, and writes results to `infer-out/`. This is why every project must be configured (so that `make` invokes `gcc`) before analysis.

---

## Setting Up the Dataset

```bash
chmod +x setup_dataset.sh
./setup_dataset.sh                # clones into ./dataset/
./setup_dataset.sh /data/nilguard # custom path
```

Seven projects are cloned from git at their pinned commits/tags. Snort 2.9.13 is downloaded as a tarball from snort.org because the Snort 2.x C source was never published in a public git repository (the `snort3/snort3` GitHub repo is Snort++, an unrelated C++ rewrite).

The script is idempotent — already-present projects are skipped.

---

## Installing System Dependencies

### All eval projects at once (Ubuntu/Debian)

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

## Configuring Projects

After cloning and installing dependencies, each project must be configured so that `make` produces a working build. The commands below match what is present in each project at its pinned version.

### flex

The checked-out source includes a pre-generated `configure` script.

```bash
cd dataset/flex
./configure CC=gcc
```

### lxc

The checked-out source has `autogen.sh` but no pre-generated `configure` script. Run `autogen.sh` first to generate it.

```bash
cd dataset/lxc
./autogen.sh
./configure CC=gcc
```

### x264

The checked-out source includes a custom `configure` script (not autotools).

```bash
cd dataset/x264
./configure
```

### p11-kit

The checked-out source has `autogen.sh` but no pre-generated `configure` script. Run `autogen.sh` first to generate it.

```bash
cd dataset/p11-kit
./autogen.sh
./configure CC=gcc
```

### recutils

The checked-out source has a `bootstrap` script (not `autogen.sh`) and no pre-generated `configure` script. Run `bootstrap` first to generate it.

```bash
cd dataset/recutils
./bootstrap
./configure CC=gcc
```

### openssl-1 (OpenSSL 1.0.1h)

OpenSSL uses its own `config` script (not autotools `configure`).

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

OpenSSL uses its own `config` script (not autotools `configure`).

```bash
cd dataset/openssl-3
CC=gcc ./config
```

---

## Running NilGuard

Once a project is configured, run NilGuard by intercepting the build:

```bash
cd dataset/<project>
$INFER_BIN --keep-going --pulse-only -- make
```

`--pulse-only` restricts analysis to the Pulse-X engine (ISL-based). `--keep-going` continues past individual file errors. Results are written to `infer-out/`.

For debug output (error traces, IL triples):

```bash
$INFER_BIN --keep-going --debug --pulse-only -- make
```

For the in-repo test suites, compile individual files directly:

```bash
# Small programs
$INFER_BIN --pulse-only -- gcc -c tests/small_programs/nilguard_tests.c

# Single LLM-generated test
$INFER_BIN --pulse-only -- gcc -c tests/llm_generated/test01_simple_null.c
```

**Important:** Infer only analyses files that `make` actually compiles. If a project is already built, `make` is a no-op and Infer sees nothing. Always run `make clean` before re-analysis:

```bash
make clean
$INFER_BIN --keep-going --pulse-only -- make
```

---

## Reproducing the Experiments

The paper reports three experiments across three datasets. The baseline is PNF-FSE (the FSE version of ProveNFix), run with its default configuration. The two tools use different underlying analysers and find different NPE sets; each tool's patches are evaluated against its own detections.

### Experiment 1 — Repair Safety and Correctness (§ VI-B)

```bash
for project in flex lxc x264 p11-kit recutils openssl-1 snort openssl-3; do
    echo "=== $project ==="
    cd dataset/$project
    make clean
    $INFER_BIN --keep-going --pulse-only -- make 2>&1 | tee ../../results/${project}.log
    cd ../..
done
```

Patches are manually classified as **C** (globally safe), **CḠ** (locally but not globally safe), **I** (incorrect), or **N** (no patch).

**Expected (Table I):** NilGuard produces 31.9% safe patches (vs. PNF-FSE 3.2%) with an incorrect rate of 1.2% (vs. 7.3%).

### Experiment 2 — Pareto Optimality (§ VI-C)

Cost profiles (D, W) are emitted in `infer-out/` alongside each patch. D counts inserted guards; W counts captured bystander writes.

**Expected:** Median cost (1, 0) across 114 safe repairs. 91.7% of small-program patches and 90% of LLM-generated patches achieve (1, 0).

### Experiment 3 — False Positive Patch Rate (§ VI-D)

For each reported NPE, compare the surrounding code against the valid ok-triples (documented in the companion technical report) to determine whether the report is a false positive.

**Expected:** PNF-FSE 92% false-positive patches (159/173) vs. NilGuard 48.3% (74/153).

### Safety Mining Study (§ V)

The safety-mining corpus and tooling are maintained in a separate artifact. See the mining artifact's own README for setup and reproduction instructions.

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

### Small Programs (in-repo)

54 NPE test cases covering baseline logic errors, deep pointer aliasing, nested data structures, library API misuse, complex control flow, function pointers, and structural variations. Located at `tests/small_programs/`.

### LLM-Generated Programs (in-repo)

20 small C programs generated by Claude Sonnet 4 (`claude-sonnet-4-20250514`, temperature 1.0). Each contains exactly one unguarded null pointer dereference. Located at `tests/llm_generated/`.
