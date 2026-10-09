import os, threading, time
import pytest
from placex.os_layer.filestore import FileStore, UploadRejected
from placex.os_layer.memory import LRUCache, ModelRegistry
from placex.os_layer import monitor
from placex.os_layer.monitor import ResourceMonitor
from placex.os_layer.policies import FCFS
from placex.os_layer.queue import BoundedJobQueue, QueuedJob

# ----------------------------------------------------------- file system
def test_filestore_rules(tmp_path):
    fs = FileStore(tmp_path, max_bytes=100, quota_bytes=150)
    a = fs.save_upload(1, "cv.txt", b"hello world", 0)
    assert os.path.exists(a["path"]) and not a["deduped"]
    b = fs.save_upload(1, "same-content-other-name.txt", b"hello world", 11)
    assert b["deduped"] and b["path"] == a["path"]                 # dedupe by content hash
    with pytest.raises(UploadRejected) as e: fs.save_upload(1, "x.exe", b"abc", 0)
    assert e.value.code == 415
    with pytest.raises(UploadRejected) as e: fs.save_upload(1, "big.txt", b"x" * 101, 0)
    assert e.value.code == 413
    with pytest.raises(UploadRejected): fs.save_upload(1, "fake.pdf", b"not a pdf", 0)   # magic bytes
    with pytest.raises(UploadRejected) as e: fs.save_upload(1, "q.txt", b"y" * 60, 100)  # quota
    assert "quota" in str(e.value)

def test_filestore_blocks_path_traversal(tmp_path):
    fs = FileStore(tmp_path / "root", 1000, 10_000)
    r = fs.save_upload(1, "../../../etc/passwd.txt", b"data", 0)
    assert str((tmp_path / "root").resolve()) in r["path"]
    assert fs.delete(str(tmp_path / "outside.txt")) is False

# ----------------------------------------------------------------- memory
def test_lru_eviction_and_ttl():
    c = LRUCache(max_items=2)
    c.put("a", 1); c.put("b", 2); c.get("a"); c.put("c", 3)
    assert c.get("b") is None and c.get("a") == 1 and c.get("c") == 3
    t = LRUCache(max_items=5, ttl=0.05); t.put("k", 1); time.sleep(0.1)
    assert t.get("k") is None

def test_model_loaded_exactly_once_under_contention():
    reg, loads = ModelRegistry(), []
    def loader():
        time.sleep(0.05); loads.append(1); return object()
    out = []
    ts = [threading.Thread(target=lambda: out.append(reg.get("spacy", loader))) for _ in range(16)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(loads) == 1 and len({id(o) for o in out}) == 1

# ---------------------------------------------------------------- monitor
def test_monitor_admission_control_with_hysteresis(cfg):
    q = BoundedJobQueue(5, FCFS()); events = []
    readings = iter([(10, 50, 1), (10, 90, 1), (10, 80, 1), (10, 70, 1)])   # cpu, mem, rss
    m = ResourceMonitor(cfg, q, on_event=lambda a, d: events.append(a), probe=lambda: next(readings))
    m.sample_once();  assert not q.paused                     # 50% -> fine
    m.sample_once();  assert q.paused and m.throttled         # 90% -> pause
    m.sample_once();  assert q.paused                         # 80% (between low 75 / high 85) -> stay paused
    m.sample_once();  assert not q.paused                     # 70% -> resume
    assert events == ["admission_paused", "admission_resumed"]

def test_macos_memory_probe_uses_compressed_and_reclaimable_memory(monkeypatch):
    class Result:
        stdout = "System-wide memory free percentage: 31%\n"

    def run(command, **kwargs):
        assert command == ["memory_pressure", "-Q"]
        assert kwargs["check"] is True
        assert kwargs["timeout"] == 2
        return Result()

    monkeypatch.setattr(monitor.sys, "platform", "darwin")
    monkeypatch.setattr(monitor.subprocess, "run", run)

    assert monitor._memory_percent() == 69.0

def test_monitor_resumes_when_macos_pressure_is_below_low_watermark(cfg):
    q = BoundedJobQueue(5, FCFS())
    q.pause()
    m = ResourceMonitor(cfg, q, probe=lambda: (10, 69, 1))
    m.throttled = True

    sample = m.sample_once()

    assert sample["mem"] == 69
    assert not q.paused
    assert not m.throttled
