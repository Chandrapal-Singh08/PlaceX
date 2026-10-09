"""Benchmarks for the OS layer.  Run:  python demos/benchmark.py
  1) throughput vs. worker-pool size   (1 worker == "no pool")
  2) scheduling policy comparison: FCFS vs priority vs priority+aging (wait times)"""
import os, statistics, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from placex.ai.stub import KeywordBackend
from placex.config import Config
from placex.core import PlaceXCore
from placex.initdb import apply_schema
from placex import db

def fresh_core(workers, policy="priority_aging", latency=0.0, cap=500):
    cfg = Config(workers=workers, policy=policy, queue_capacity=cap, upload_dir=tempfile.mkdtemp(), aging_seconds=0.5)
    apply_schema(cfg.dsn, reset=True, seed=True)
    return PlaceXCore(cfg, ai=KeywordBackend(latency=latency), probe=lambda: (1, 1, 1)).start()

def bench_throughput(n_jobs=60, latency=0.05):
    print(f"(machine has {os.cpu_count()} CPU core(s): threads overlap WAITING, they cannot add CPU)")
    print(f"\n[1] Throughput: {n_jobs} RESUME_BUILD jobs, simulated AI latency {latency*1000:.0f} ms/call")
    print(f"{'workers':>8} {'total s':>9} {'jobs/s':>8} {'speed-up':>9} {'max parallel':>13}")
    base = None
    for w in (1, 2, 4, 8):
        k = fresh_core(w, latency=latency)
        u = k.repo.create_user("b@x.in", "Bench")
        payload = {"jd_id": 1, "profile": {"name": "B", "skills": ["Python"]}}
        t0 = time.perf_counter()
        ids = [k.submit(u["id"], "RESUME_BUILD", payload)["id"] for _ in range(n_jobs)]
        done = [k.wait(i, 120) for i in ids]
        dt = time.perf_counter() - t0
        assert all(j["status"] == "DONE" for j in done), "some jobs failed"
        base = base or dt
        print(f"{w:>8} {dt:>9.2f} {n_jobs/dt:>8.1f} {base/dt:>8.2f}x {k.stats.snapshot()['max_concurrent']:>13}")
        k.stop()

def bench_policies(n_low=30, n_urgent=100, work=0.04, gap=0.03):
    """1 worker. 30 low-priority jobs are queued, then urgent jobs keep ARRIVING faster than the
    worker can finish them (one per 30 ms vs 40 ms of work). That is the classic starvation setup."""
    import threading
    print(f"\n[2] Scheduling policies, 1 worker, {work*1000:.0f} ms/job: {n_low} low jobs (p=1) waiting, "
          f"then {n_urgent} urgent jobs (p=9) arriving every {gap*1000:.0f} ms")
    print(f"{'policy':>15} {'urgent mean wait':>17} {'low mean wait':>14} {'low MAX wait':>13}")
    for pol in ("fcfs", "priority", "priority_aging"):
        k = fresh_core(1, policy=pol)
        if pol == "priority_aging":
            k.queue.policy.aging_seconds = 0.1             # +1 priority point per 100 ms waited
        def fake(job, ctx):
            time.sleep(work); return {}
        k.pool.handlers["JOURNEY_GUIDE"] = fake
        u = k.repo.create_user("p@x.in", "P")
        k.queue.pause()
        low = [k.submit(u["id"], "JOURNEY_GUIDE", {}, priority=1)["id"] for _ in range(n_low)]
        k.queue.resume()
        high = []
        def stream():
            for _ in range(n_urgent):
                high.append(k.submit(u["id"], "JOURNEY_GUIDE", {}, priority=9)["id"]); time.sleep(gap)
        th = threading.Thread(target=stream); th.start(); th.join()
        for i in low + high: k.wait(i, 120)
        def waits(ids):
            with db.transaction() as c:
                rows = c.execute("SELECT EXTRACT(EPOCH FROM started_at - created_at) AS w FROM jobs WHERE id = ANY(%s)", (ids,)).fetchall()
            return [float(r["w"]) for r in rows]
        h, l = waits(high), waits(low)
        print(f"{pol:>15} {statistics.mean(h):>16.2f}s {statistics.mean(l):>13.2f}s {max(l):>12.2f}s")
        k.stop()

if __name__ == "__main__":
    bench_throughput()
    bench_policies()
