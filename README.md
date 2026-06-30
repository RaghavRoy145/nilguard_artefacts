# NilGuard — Artifact Repository

**NilGuard: Minimising Patches for Null Pointer Errors with Incorrectness Separation Logic**

This repository contains the complete artifact for reproducing the evaluation of NilGuard, organised into two independent sub-artifacts.

---

## Structure

```
nilguard-artefacts/
├── README.md                          ← you are here
├── repair_evaluation_artefact/        ← Experiments 1–3 (Section VI)
│   ├── README.md
│   ├── ...
└── developer_mining_artefact/         ← Safety mining study (Section V)
    └── README.md
│   ├── ...
```

## Repair Evaluation Artifact (Section VI)

Reproduces the three experiments comparing NilGuard against PNF-FSE across three datasets: 54 small programs (in a single C file), 20 LLM-generated programs, and 8 large real-world C projects.

See [`repair_evaluation_artefact/README.md`](repair_evaluation_artefact/README.md) for full setup and reproduction instructions.

## Developer Mining Artifact (Section V)

Reproduces the local-vs-global safety correlation study across 28 open-source C projects (~5.0 MLOC), demonstrating that 99.4% of locally safe developer NPE patches are also globally safe.

See [`developer_mining_artefact/README.md`](developer_mining_artefact/README.md) for full setup and reproduction instructions.

---

## Citation

```bibtex
@inproceedings{nilguard2026,
  title     = {{NilGuard}: Minimising Patches for Null Pointer Errors
               with Incorrectness Separation Logic},
  author    = {<authors>},
  booktitle = {Proceedings of <venue>},
  year      = {2026}
}
```