# PlaceX: Database + OS layers (foundation)

Real, tested foundation for the PlaceX placement guide. The AI is a swappable stub
(keyword matching) so the whole pipeline already works end to end; spaCy / Sentence
Transformers / LLMs plug in later without touching the DB or OS code.

## Run it

```bash
# 1. PostgreSQL 14+ running, then create a user + database once:
#    CREATE USER placex WITH PASSWORD 'placex' SUPERUSER;   CREATE DATABASE placex OWNER placex;
pip install -r requirements.txt
export PLACEX_DSN=postgresql://placex:placex@localhost:5432/placex   # optional, this is the default

python -m placex.initdb --reset          # schema + sample seed data
python demos/demo_flow.py                # all 4 modules, end to end
python -m pytest -q                      # 43 tests (creates its own placex_test database)
python demos/race_demo.py                # mutex demo
python demos/benchmark.py                # pool size + scheduling policy benchmarks
uvicorn placex.api:app --reload          # HTTP API at http://127.0.0.1:8000/docs
```

## Architecture

```
HTTP (api.py) -> PlaceXCore.submit() -> INSERT jobs (READY) + BoundedJobQueue.put()
                                              |  policy picks next (FCFS / priority / aging / SJF)
                                       WorkerPool threads -> claim_job() (atomic SQL) -> handler
                                              |                                         |
                              ResourceMonitor pauses queue                 AI stub + repo.* (one transaction)
                              when RAM/CPU is high                         -> reports / roadmaps / resumes
```

## Where each concept lives (for your viva)

| Concept | File | What to say |
|---|---|---|
| Producer-consumer, bounded buffer | `os_layer/queue.py` | One mutex, two condition variables (`not_full`, `not_empty`). Full queue = backpressure (HTTP 503). |
| Mutex / race condition | `os_layer/worker.py` (`PoolStats`), `demos/race_demo.py` | All shared counters sit behind one lock. Demo: 13,992 of 16,000 updates lost without it, 0 with it. |
| CPU scheduling + starvation | `os_layer/policies.py` | 4 swappable policies. Aging adds +1 priority per N seconds waited, so nothing starves. |
| Process lifecycle | `jobs` table + `worker.py` | READY -> RUNNING -> DONE / FAILED / CANCELLED, retry with exponential backoff. DB `CHECK`s forbid illegal states. |
| Atomic claim / MVCC | `repo.claim_job` | `UPDATE ... WHERE id=(SELECT ... FOR UPDATE SKIP LOCKED)`: 20 racing threads, exactly 1 winner (tested). |
| Row-level locking | `handlers.resume_analysis` | `SELECT ... FOR UPDATE` on the resume. Found by a failing concurrency test (see below). |
| Memory management | `os_layer/memory.py` | LRU+TTL cache; model registry loads heavy models exactly once (double-checked locking). |
| Admission control | `os_layer/monitor.py` | psutil sampling; pause at 85% RAM, resume at 75% (hysteresis, so it does not flap). |
| File system | `os_layer/filestore.py` | Content-hash dedupe, per-user quota, size/type/magic-byte checks, atomic write (tmp + fsync + rename), path-traversal safe. |
| Crash recovery | `repo.recover_jobs`, `core.start` | RUNNING jobs left by a dead process return to READY and are re-queued on boot. |
| Timeouts | `JobContext.check()` | Cooperative: Python threads cannot be killed safely. |

## Database (15 tables, 3NF)

`users, companies, job_descriptions, skills, resumes, resume_skills, jd_skills, analysis_reports,
hiring_rounds, resources, roadmaps, roadmap_items, jobs, activity_logs, user_sessions`

- **Junction tables** make matched vs missing skills a single `LEFT JOIN` (`fn_skill_gap`), and the ATS
  score an importance-weighted SQL function (`fn_ats_score`).
- **Triggers:** every job state change is audited into `activity_logs`; `updated_at` and roadmap
  `completed_at` are maintained by the database.
- **View:** `v_student_dashboard` (best/latest ATS, roadmap progress %, pending jobs).
- **Constraints/indexes:** CHECK, UNIQUE, FK cascades, partial index on READY jobs.
- Your proposal said 8-10 tables; this is 14 including 2 junctions. Trim if your faculty wants exactly 8-10.

## Measured results (1-core sandbox)

| Test | Result |
|---|---|
| Tests | 43 passed (DB constraints/triggers/functions, queue, policies, workers, retry, recovery, API) |
| Race demo | no mutex: 13,992 / 16,000 updates lost; with mutex: 0 |
| Throughput, 60 jobs, 50 ms simulated AI latency | 1 worker 9.7 s, 2 workers 5.1 s (1.9x), 4 workers 3.4 s (2.9x) |
| Policies (urgent stream vs low backlog) | FCFS: urgent wait 1.89 s. Strict priority: urgent 0.63 s but low jobs wait up to 5.65 s (starved). Aging: urgent 1.58 s, low max 2.36 s |

Throughput plateaus at 4 workers **only because this sandbox has 1 CPU core**. Threads overlap *waiting*
(DB, files, API calls); they cannot create CPU. On your laptop re-run `benchmark.py` and report your own numbers.

### A real bug the tests caught
With 40 concurrent analyses of one resume, 13 jobs failed with `UniqueViolation`: two transactions both did
delete-then-insert on `resume_skills` under READ COMMITTED. Fixed by locking the resume row (`FOR UPDATE`),
and the burst test now passes. Worth including in your report: it shows why the DB and OS layers must be designed together.

## Plugging in the real AI later

Implement the three methods of `ai/base.py::AIBackend` (`extract_skills`, `suggest_improvements`,
`build_resume`) and pass it: `PlaceXCore(ai=MyBackend())`. Notes:
- spaCy / transformer inference is **CPU-bound**: the GIL means threads will not parallelise it. Run it in a
  `ProcessPoolExecutor` from inside the handler, and load the model once via `core.models.get(name, loader)`.
- LLM API calls (Groq/OpenAI) are I/O-bound, so the current thread pool is the right fit.

## Known limitations (be upfront about these)

- Sample companies, rounds and JDs in `seed.sql` are **placeholders**. Replace with verified data.
- Passwords use PBKDF2; API sessions are revocable, hashed bearer tokens. User-specific endpoints enforce session ownership.
- Existing accounts with no password hash require an administrator-provisioned password/reset flow before they can sign in.
- Skill extraction is keyword-based (case-insensitive, alias-aware); PDFs need `pip install pypdf`.
- Queue state is in-process; one server process per database. Multi-process would need DB-driven claiming (the `claim_job` SQL already supports it).
- Timeouts are cooperative and a handler must call `ctx.check()`.

## Suggested team split

1. **DB:** `sql/`, `repo.py`, `test_database.py`
2. **Scheduler/queue:** `os_layer/queue.py`, `policies.py`, `test_queue.py`, policy benchmark
3. **Workers/monitor/memory/files:** `worker.py`, `monitor.py`, `memory.py`, `filestore.py`, `test_os_layer.py`
4. **API/UI + AI stub:** `api.py`, `ai/`, `tasks/handlers.py`, frontend
