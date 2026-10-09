import threading, time
import pytest
from placex.os_layer.policies import FCFS, StrictPriority, PriorityAging, ShortestJobFirst, make_policy
from placex.os_layer.queue import BoundedJobQueue, QueuedJob, QueueFull

def J(i, pri=5, typ="PREP_PLAN", age=0.0):
    return QueuedJob(i, pri, typ, enqueued_at=time.monotonic() - age)

def drain(q):
    out = []
    while len(q):
        out.append(q.get(timeout=0.1).job_id)
    return out

def test_fcfs_order():
    q = BoundedJobQueue(10, FCFS())
    for i in (1, 2, 3): q.put(J(i, pri=i))
    assert drain(q) == [1, 2, 3]

def test_strict_priority_order_and_tiebreak():
    q = BoundedJobQueue(10, StrictPriority())
    q.put(J(1, 2)); q.put(J(2, 9)); q.put(J(3, 9)); q.put(J(4, 5))
    assert drain(q) == [2, 3, 4, 1]

def test_sjf_prefers_short_jobs():
    q = BoundedJobQueue(10, ShortestJobFirst())
    q.put(J(1, typ="RESUME_ANALYSIS")); q.put(J(2, typ="JOURNEY_GUIDE")); q.put(J(3, typ="PREP_PLAN"))
    assert drain(q) == [2, 3, 1]

def test_aging_prevents_starvation():
    pol = PriorityAging(aging_seconds=1.0)
    q = BoundedJobQueue(10, pol)
    q.put(J(1, pri=0, age=20))      # waited 20s -> effective priority 20
    q.put(J(2, pri=10, age=0))
    assert q.get(timeout=0.1).job_id == 1
    # ...but strict priority would have starved job 1
    q2 = BoundedJobQueue(10, StrictPriority())
    q2.put(J(1, pri=0, age=20)); q2.put(J(2, pri=10))
    assert q2.get(timeout=0.1).job_id == 2

def test_bounded_raises_when_full_and_force_bypasses():
    q = BoundedJobQueue(2, FCFS())
    q.put(J(1)); q.put(J(2))
    with pytest.raises(QueueFull): q.put(J(3))
    q.put(J(3), force=True)
    assert len(q) == 3

def test_blocking_put_waits_for_space():
    q = BoundedJobQueue(1, FCFS())
    q.put(J(1))
    threading.Timer(0.1, lambda: q.get(timeout=1)).start()
    t0 = time.monotonic()
    q.put(J(2), block=True, timeout=2)
    assert 0.05 < time.monotonic() - t0 < 1.5

def test_get_times_out_when_empty():
    assert BoundedJobQueue(1, FCFS()).get(timeout=0.05) is None

def test_pause_blocks_consumers_resume_releases():
    q = BoundedJobQueue(5, FCFS()); q.put(J(1)); q.pause()
    assert q.get(timeout=0.1) is None
    threading.Timer(0.1, q.resume).start()
    assert q.get(timeout=2).job_id == 1

def test_close_wakes_blocked_consumers():
    q = BoundedJobQueue(5, FCFS()); res = []
    t = threading.Thread(target=lambda: res.append(q.get(timeout=5))); t.start()
    time.sleep(0.1); q.close(); t.join(2)
    assert res == [None] and not t.is_alive()

def test_many_producers_many_consumers_no_loss_no_duplicates():
    q = BoundedJobQueue(8, make_policy("priority_aging", 1))
    N_PROD, PER = 8, 250
    got, lock = [], threading.Lock()
    def producer(p):
        for k in range(PER):
            q.put(J(p * PER + k, pri=k % 10), block=True, timeout=30)
    def consumer():
        while True:
            it = q.get(timeout=1)
            if it is None: return
            with lock: got.append(it.job_id)
    ps = [threading.Thread(target=producer, args=(p,)) for p in range(N_PROD)]
    cs = [threading.Thread(target=consumer) for _ in range(6)]
    for t in ps + cs: t.start()
    for t in ps + cs: t.join(60)
    assert len(got) == N_PROD * PER and len(set(got)) == N_PROD * PER
