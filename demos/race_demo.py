"""Race condition demo: one shared counter, 8 threads, WITHOUT and WITH a mutex.
Run:  python demos/race_demo.py

`time.sleep(0)` marks the vulnerable window: it just yields the CPU between the READ and the
WRITE. Real code gets descheduled at arbitrary points, so the same bug appears there, only
rarely and unpredictably (which is exactly why races are dangerous)."""
import threading, time

THREADS, PER = 8, 2_000

def run(use_lock: bool):
    counter, lock = 0, threading.Lock()
    def work():
        nonlocal counter
        for _ in range(PER):
            if use_lock:
                with lock:                  # critical section: only one thread at a time
                    tmp = counter
                    time.sleep(0)
                    counter = tmp + 1
            else:
                tmp = counter               # 1. read
                time.sleep(0)               # 2. another thread runs here and updates counter
                counter = tmp + 1           # 3. write back a STALE value -> update lost
    ts = [threading.Thread(target=work) for _ in range(THREADS)]
    t0 = time.perf_counter()
    [t.start() for t in ts]; [t.join() for t in ts]
    return counter, time.perf_counter() - t0

if __name__ == "__main__":
    expected = THREADS * PER
    for label, flag in (("WITHOUT mutex", False), ("WITH mutex   ", True)):
        got, dt = run(flag)
        print(f"{label}: expected {expected:,}  got {got:,}  lost updates {expected - got:,}  ({dt:.2f}s)")
