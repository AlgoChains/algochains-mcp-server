# Build Plan — GPU Compute Rental Marketplace ("rent out the 5080 and charge for it")

> **Status:** Proposed · **Revenue lever:** GPU-hours margin + orchestration fee
> **Grounds on existing code:** `algoclaw/desktop_tower.py` (`dispatch_tower_job`),
> `cloud_saas/tenant_manager.py`, `cloud_saas/billing_engine.py`,
> `cloud_saas/connect_payouts.py`, `cloud_saas/spend_guard.py`,
> `auth/oauth_resource.py`, `multi_tenant/isolation.py`.
> **Hardware target:** Desktop tower `teespc-1` (RTX 5080 / CUDA 12.x) reachable via
> Tailscale at `100.89.114.31`.
> **Constraint:** MCP server is PUBLIC and callable by autonomous agents — so every GPU
> job is spend-capped, sandboxed, network-isolated, and fails closed.

---

## 1. What It Is

A marketplace that lets a customer (or their AI agent) **submit a compute job**, have it
run isolated on the RTX 5080 (or a federated node network), and be **billed by the
GPU-hour with a markup**.

- **Single-node (immediate, Phase 0):** Jobs run on `teespc-1` only; scheduling is
  queue-based (Redis FIFO).  Perfect for ML backtests, Optuna sweeps, and FinBERT
  inference that users already dispatch via `dispatch_tower_job`.
- **Multi-node / federated (Phase 2):** Additional RTX owners join via Headscale and
  contribute capacity.  AlgoChains orchestrates, routes, and pays operators via Stripe
  Connect (same model as Vast.ai).

---

## 2. Architecture (per job submission)

```
agent → MCP tool → [1 authZ+tenant] → [2 credit/spend-cap] → [3 job spec validate]
      → [4 queue dispatch] → [5 gVisor sandbox on tower] → [6 meter GPU-hours → Stripe]
      → [7 result egress + audit]
```

1. **AuthN/Z + tenant:** same path as managed-provisioning — OAuth JWT or `ac_live_*`
   key → `tenant_id` from `app_metadata` (never caller-supplied).
2. **Credit/spend-cap gate:** `spend_guard.py` — per-tenant GPU-hour prepaid credit;
   estimated cost (GPUh × rate) must be ≤ remaining credit.  Hard stop.
3. **Job spec validation:** allow-listed `job_type` enum (backtest, optuna_sweep,
   inference, notebook); no raw shell commands; max VRAM cap per tier; disk quota.
4. **Queue dispatch:** Redis job queue (`algochains:gpu_jobs:<tenant>`); dedicated
   queue-worker on `teespc-1` polls and launches.
5. **gVisor sandbox:** every job runs in `runsc` (gVisor) with `nvproxy` GPU passthrough.
   Default-deny egress via `iptables` — only whitelisted endpoints (Supabase, S3 result
   bucket, PyPI mirror).  CPU + VRAM limits enforced by cgroups.
6. **Meter GPU-hours:** emit Stripe Billing Meter `gpu_compute_hours` for
   `stripe_customer_id` on job completion; decrement prepaid credit.  Rate stored per
   tier (`GPU_RATE_USD_PER_HOUR` env, default `$2.50/GPUh`).
7. **Result egress + audit:** job output written to tenant-scoped S3 prefix
   (`s3://algochains-results/<tenant>/<job_id>/`); pre-signed URL returned; audit row
   appended (`gpu_jobs` table, immutable).

---

## 3. Sandboxing & Security (five-layer defence)

| Layer | Control | Mechanism |
|-------|---------|-----------|
| **L1 Runtime** | gVisor (runsc) kernel intercept | blocks direct syscalls; nvproxy passes CUDA ioctls |
| **L2 Network** | Default-deny egress | iptables allow-list: Supabase URL, S3 endpoint, PyPI mirror only |
| **L3 Tenant isolation** | Unique uid/gid per job + tmpfs scratch | no cross-tenant filesystem access; scrub on exit |
| **L4 Resource** | cgroup v2 VRAM + CPU quota | VRAM limit by tier; CPU throttle for queue fairness |
| **L5 Secret isolation** | No secrets on the compute node | Job receives only pre-signed S3 URLs and result bucket path; no API keys |

**gVisor + nvproxy setup on teespc-1:**
```bash
# Install gVisor
curl -fsSL https://gvisor.dev/archive.key | sudo gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
sudo add-apt-repository "deb [arch=amd64 signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main"
sudo apt-get update && sudo apt-get install runsc

# Configure Docker to use gVisor runtime
sudo runsc install
sudo systemctl restart docker

# Enable nvproxy (GPU passthrough)
# In container run: --runtime=runsc --gpus=all (Docker) or
# Kubernetes: runtimeClassName: gvisor with nvidia device plugin
```

---

## 4. Networking / Federation (Tailscale + Headscale)

**Current (single node):** `teespc-1` is already on Tailscale at `100.89.114.31`.
The queue worker polls from the same machine — no new networking needed for Phase 0.

**Phase 2 — multi-node operator network:**

```
Headscale control plane (VPS, ~$6/mo)
    │
    ├── teespc-1 (RTX 5080, AlgoChains-owned)
    ├── operator-node-A (RTX 4090, third-party)
    └── operator-node-B (A100, third-party)

Tailscale ACL:
  - Orchestrator → nodes: 6379 (Redis), 2376 (Docker API)
  - Nodes → orchestrator: 443 (result upload pre-signed S3)
  - Nodes ↔ nodes: DENIED
  - Public internet → nodes: DENIED (exit via Tailscale only)
```

Operators join via `tailscale up --login-server=https://headscale.algochains.io`.
Capacity registration: `register_gpu_node(tailscale_ip, vram_gb, cuda_version)`.

---

## 5. Operator Payouts (Stripe Connect)

Modelled on `connect_payouts.py` — same mechanic as creator revenue share:

| Actor | Receives |
|-------|---------|
| Job submitter (tenant) | Billed `GPUh × (rate × markup)` |
| Node operator | Paid `GPUh × rate × (1 − platform_cut)` via Stripe Connect transfer |
| AlgoChains | Keeps markup + platform_cut |

Default split: operator keeps **70%** of raw GPU-hour cost; AlgoChains keeps **30%** (markup
varies by tier — starter: 2×, pro: 1.5×, enterprise: 1.2×).

Operator onboarding: `create_gpu_operator_link(operator_id, email)` — identical to
`create_creator_onboarding_link`, wraps BillingEngine Stripe Connect onboarding URL.

---

## 6. MCP Tool Surface (new)

| Tool | Tier | Gate | Purpose |
|------|------|------|---------|
| `estimate_gpu_job_cost(job_spec)` | READ_ONLY | tenant | wallclock + cost estimate before submitting |
| `submit_gpu_job(job_spec, ttl_hours?)` | WRITE_SAFE | tenant + credit + cap | queue a sandboxed GPU job |
| `get_gpu_job_status(job_id)` | READ_ONLY | tenant | poll status, progress %, ETA |
| `get_gpu_job_result(job_id)` | READ_ONLY | tenant | pre-signed S3 URL to output (24h TTL) |
| `cancel_gpu_job(job_id, owner_token)` | DESTRUCTIVE | owner | kill running job, free slot |
| `list_gpu_jobs()` | READ_ONLY | tenant | paginated job history, cost-to-date |
| `get_gpu_credit_balance()` | READ_ONLY | tenant | remaining prepaid GPU-hour credit |
| `register_gpu_node(tailscale_ip, vram_gb, cuda_version)` | WRITE_SAFE | operator | contribute a node to the network |
| `create_gpu_operator_link(operator_id, email)` | WRITE_SAFE | operator | Stripe Connect onboarding for node operators |
| `get_gpu_operator_earnings(operator_id)` | READ_ONLY | operator | GPU-hour payout history |

All read tools fail closed. `submit_gpu_job` fails closed if cost can't be estimated or
credit can't be confirmed. `cancel_gpu_job` is owner-gated.

---

## 7. Allowed Job Types (Phase 0 allow-list)

| `job_type` | What runs | VRAM cap |
|-----------|----------|---------|
| `backtest` | AlgoChains backtest engine (Rust or Python) | 8 GB |
| `optuna_sweep` | Optuna + XGBoost/LightGBM hyperparameter sweep | 12 GB |
| `inference` | FinBERT / Kronos / user-supplied ONNX model | 16 GB |
| `notebook` | Papermill-executed Jupyter notebook | 8 GB |

No raw `bash`, `exec`, or arbitrary Python entrypoints in Phase 0.
Phase 1 adds: custom Docker image jobs (image digest allow-list only).

---

## 8. New Modules & Migration

- `cloud_saas/gpu_rental.py` — job submission, queue dispatch, result polling,
  cost metering; gVisor launcher integration; fail-closed.
- `cloud_saas/gpu_federation.py` — Headscale node registry; Tailscale ACL enforcement;
  capacity routing (cheapest available node that satisfies VRAM requirement).
- Migration `20260533_gpu_rental.sql`:
  - `gpu_nodes` (operator, tailscale_ip, vram_gb, cuda_version, status, registered_at)
  - `gpu_jobs` (append-only, tenant, node_id, job_type, spec_hash, status, gpu_seconds,
    cost_usd, result_s3_key, ttl_at)
  - `gpu_credits` (tenant prepaid balance ledger — mirrors `infra_credits`)
  - `gpu_operator_payouts` (operator, period, gpu_seconds, payout_usd, stripe_transfer_id)
  - RLS: service-role only; `current_tenant_id()` row-level policies.

---

## 9. Phased Delivery

1. **Phase 0 — single node, AlgoChains subscribers only:**
   `estimate_gpu_job_cost` + `submit_gpu_job` + `get_gpu_job_status` + `get_gpu_job_result`
   for allow-listed job types on `teespc-1`; prepaid credits; hard cap; Stripe meter; TTL.
   gVisor sandbox mandatory. Redis FIFO queue.

2. **Phase 1 — custom Docker images + operator payouts:**
   Allow digest-pinned custom images (audit layer manifest); `cancel_gpu_job`;
   Stripe Connect operator payouts; `create_gpu_operator_link`.

3. **Phase 2 — federated node network:**
   Headscale, `register_gpu_node`, capacity router, Tailscale ACL enforcement;
   multi-cloud GPU nodes (RunPod / Lambda Labs API as demand overflow).

---

## 10. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Malicious code in submitted job | gVisor syscall intercept + default-deny egress + allow-list job types |
| Cross-tenant data leak | Per-job uid + tmpfs + tenant-scoped S3 prefix; no shared state |
| GPU VRAM exhaustion (agent loop) | cgroup VRAM limit + per-tenant job concurrency cap (1 concurrent, queue rest) |
| Runaway bill (GPU-hours) | Prepaid credit hard stop; per-job cost estimate gate |
| Node compromise (BYOG) | Nodes never hold platform secrets; read-only Tailscale ACL from node; result upload via pre-signed S3 URL only |
| CUDA escape (CVE class) | nvproxy isolates CUDA ioctls; gVisor blocks everything else; no privileged container |
| Exfiltration via job output | Job output scanned for credential patterns before pre-sign (regex allow-list on output filenames) |

> Keep strictly OFF the trading/order/risk/auth path (CLAUDE.md). It is a separate
> SaaS surface; existing trading guardrails are untouched.

---

## 11. Grounding in Existing Code

`dispatch_tower_job` in `algoclaw/desktop_tower.py` is the direct ancestor of
`submit_gpu_job`. The migration path is:

1. Add tenant auth + credit gate in front of the existing dispatch call.
2. Wrap the tower subprocess launch in `runsc` (gVisor).
3. Replace direct result-file polling with S3 upload + pre-signed URL return.
4. Emit Stripe Billing Meter event on job completion.

Phase 0 reuses the existing `teespc-1` queue worker with minimal changes to
`desktop_tower.py`; no new infrastructure beyond a Redis job queue (already present
at `127.0.0.1:6380` per CLAUDE.md) and an S3 bucket.
