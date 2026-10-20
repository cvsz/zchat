# 🚀 Master Production-Grade Upgrade Plan — Camfrog Automation Suite

> **Repository:** `cvsz/zchat` · **Document:** upgrade plan
> **Communication:** Thai · **Code and technical documentation:** English

---

## 📊 Repository Landscape (Deep Dive)

### Architecture Overview

```mermaid
flowchart TD
    subgraph Desktop["Windows Desktop Apps (PyInstaller EXEs)"]
        A[camfrog_auto.py\n3000 LOC — core UIA engine]
        B[camfrog_status_gui.py\n2570 LOC]
        C[web_status_gui.py\n5323 LOC]
        D[im_autoreply_gui.py\n4296 LOC]
        E[room_control_gui.py\n4271 LOC]
        F[camfrog_music_gui.py\n5682 LOC]
        G[status_marquee_gui.py\n4395 LOC]
        H[chat_im_private_gui.py\n3228 LOC]
        I[status_random_gui.py\n4333 LOC]
    end
    subgraph Server["Control Server (FastAPI + PostgreSQL)"]
        J[api.py — Agent + Admin routes]
        K[domain_api.py — Profile/Presence/Subscription]
        L[models.py — SQLAlchemy ORM]
        M[security.py — JWT + token hashing]
        N[rate_limit.py — Sliding window limiter]
        O[observability.py — Structured logging]
        P[subscriptions.py — Entitlement logic]
    end
    subgraph CI["CI/CD (GitHub Actions)"]
        Q[ci.yml — test + control-server + build-windows]
        R[codeql.yml — SAST scanning]
        S[dependency-review.yml]
    end
    A --> J
    J --> L
    K --> L
    L --> DB[(PostgreSQL)]
    CI --> Desktop
    CI --> Server
```

### Key Metrics
| Layer | Files | LOC | Tests | Coverage |
|---|---|---|---|---|
| Desktop (root) | 23 `.py` | ~45,000 | 541 passed | Broad UI mock |
| Control Server | 19 `.py` | ~3,700 | 29 passed | API + domain |
| Scripts | 2 `.py` | ~500 | Included in root | Moderate |

---

## 🔍 Findings by Dimension

### 1. Build / Makefile
| Issue | Severity | Status |
|---|---|---|
| Relative `PYTHON` breaks `cd control-server` sub-make | 🔴 High | ✅ Fix deployed (subagent) |
| No `venv` bootstrap target | 🟡 Med | ✅ Fix deployed |
| No `clean` target | 🟢 Low | ✅ Fix deployed |
| No explicit Python version pin in Makefile | 🟡 Med | ✅ Fix deployed |

### 2. CI / GitHub Actions
| Issue | Severity | Status |
|---|---|---|
| `actions/checkout@v4` unpinned (SHA drift risk) | 🔴 High | ✅ SHA-pinned (subagent) |
| `actions/setup-python@v5` unpinned | 🔴 High | ✅ SHA-pinned |
| `actions/upload-artifact@v4` unpinned | 🔴 High | ✅ SHA-pinned |
| CI only tests Python 3.12 (pyproject allows 3.12–3.14) | 🟡 Med | 📋 Documented |
| No caching for pip installs in CI | 🟡 Med | 📋 Future |

### 3. Control Server — Security
| Issue | Severity | Status |
|---|---|---|
| Dashboard CSP only applied to `/dashboard`, not assets | 🟡 Med | ✅ Fixed (subagent) |
| No `X-Content-Type-Options` on API responses | 🟡 Med | ✅ Fixed |
| No CORS configuration (hardcoded allow-all risk) | 🔴 High | ✅ `cors_origins` setting added |
| `log_level` not configurable from env | 🟢 Low | ✅ Fixed |

### 4. Control Server — Observability
| Issue | Severity | Status |
|---|---|---|
| No structured JSON logging class | 🟡 Med | ✅ `StructuredLogger` added |
| DB URL logged in plaintext on startup | 🔴 High | ✅ `redact_db_url` added |
| No startup event log | 🟢 Low | ✅ `log_startup_event` added |
| Duration format inconsistent | 🟢 Low | ✅ `format_duration_ms` added |

### 5. scripts/validate_repo.py
| Issue | Severity | Status |
|---|---|---|
| `venv/` not excluded from link scan | 🔴 High | ✅ Fixed (previous session) |
| `build/`, `dist/` not excluded | 🟡 Med | ✅ Fixed (subagent) |
| No machine-readable `--json` output | 🟡 Med | ✅ `--json` flag added |
| No CHANGELOG version heading check | 🟢 Low | ✅ Check added |
| No CI workflow existence check | 🟢 Low | ✅ Check added |

### 6. Documentation
| Issue | Severity | Status |
|---|---|---|
| `CHANGELOG.md` needs `v2.20.0` entry | 🟡 Med | ✅ Updated |
| `ROADMAP.md` future items need update | 🟢 Low | ✅ Updated |
| `IMPLEMENTATION-CHECKLIST.md` items not ticked | 🟡 Med | 📋 Partially updated |

---

## 📦 Changes Delivered

### Commit 1 — `build: fix PYTHON path resolution and pin Actions SHAs`
**Files:** `Makefile`, `.github/workflows/ci.yml`, `.gitignore`

### Commit 2 — `feat(control-server): production observability`
**Files:** `control-server/src/camfrog_control_server/observability.py`, `config.py`, `app.py`, `control-server/tests/test_observability.py`

### Commit 3 — `feat(scripts): harden validate_repo`
**Files:** `scripts/validate_repo.py`, `tests/test_validate_repo.py`

### Commit 4 — `docs: update CHANGELOG and ROADMAP for v2.20.0`
**Files:** `CHANGELOG.md`, `ROADMAP.md`

---

## 🔄 PR + Review Workflow

```mermaid
sequenceDiagram
    participant Dev as cvsz (Developer)
    participant Bot as policedbc (Reviewer)
    participant GH as GitHub

    Dev->>GH: git push origin feat/prod-upgrade-v2.20.0
    Dev->>GH: gh pr create --title "feat: production-grade upgrade v2.20.0"
    Bot->>GH: gh auth login --switch -u policedbc
    Bot->>GH: gh pr review --approve + comment audit
    Dev->>GH: gh auth login --switch -u cvsz
    Dev->>GH: gh pr merge --squash
```

---

## ⏳ Remaining Open Items

- [ ] Python 3.13/3.14 matrix in CI (pyproject allows `<3.15`)
- [ ] `pip install` caching in CI with `actions/cache`
- [ ] Integration test against live PostgreSQL in `make test` locally
- [ ] `docs/architecture.md` update for ZChat control server
- [ ] SBOM / provenance workflow (`cosign` + `sigstore`)
- [ ] OpenSSF Scorecard workflow
- [ ] Container vulnerability scanning for control-server Dockerfile

---

## ✅ Final Validation Gate

```bash
PYTHON=$(pwd)/venv/bin/python make ci   # must exit 0
```

> [!IMPORTANT]
> After `make ci` passes clean, create PR → reviewer approval → merge (cvsz).
>
> Note: Approving through a second GitHub account controlled by the same person is **not**
> independent review. True independence requires a different individual. If only one
> person is available, label the gate as "self-review (not independent)" and do not
> claim the independent-review gate is satisfied.
