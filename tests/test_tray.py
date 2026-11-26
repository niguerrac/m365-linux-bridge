import multiprocessing

from m365_bridge.tray_support import (
    SingleInstance, apply_qpa_platform, choose_qpa_platform, wait_until,
)


def _try_lock(path, q):
    si = SingleInstance(path)
    q.put(si.acquire())


def test_single_instance_blocks_second_process(tmp_path):
    lock = tmp_path / "tray.lock"
    first = SingleInstance(lock)
    assert first.acquire()
    assert first.acquire()  # idempotente
    assert first.owner_pid() is not None

    ctx = multiprocessing.get_context("fork")
    q = ctx.Queue()
    p = ctx.Process(target=_try_lock, args=(lock, q))
    p.start(); p.join(10)
    assert q.get(timeout=5) is False

    first.release()
    p2 = ctx.Process(target=_try_lock, args=(lock, q))
    p2.start(); p2.join(10)
    assert q.get(timeout=5) is True


def test_stale_lockfile_does_not_block(tmp_path):
    lock = tmp_path / "tray.lock"
    lock.write_text("999999")  # PID muerto: sin flock activo no bloquea
    si = SingleInstance(lock)
    assert si.acquire()
    si.release()


def test_qpa_respects_user_value():
    assert choose_qpa_platform({"QT_QPA_PLATFORM": "offscreen", "DISPLAY": ":0"}) is None


def test_qpa_wayland_with_xcb_fallback():
    env = {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}
    assert choose_qpa_platform(env, lambda e: True) == "wayland;xcb"
    assert choose_qpa_platform({"WAYLAND_DISPLAY": "wayland-0"}, lambda e: True) == "wayland"


def test_qpa_falls_back_to_xcb_when_wayland_unreachable():
    assert choose_qpa_platform({"DISPLAY": ":0"}, lambda e: False) == "xcb"
    assert choose_qpa_platform({}, lambda e: False) is None


def test_apply_qpa_sets_env():
    env = {"DISPLAY": ":0"}
    assert apply_qpa_platform(env, lambda e: False) == "xcb"
    assert env["QT_QPA_PLATFORM"] == "xcb"


def test_wait_until_retries_then_succeeds():
    calls = {"n": 0}
    t = {"now": 0.0}

    def pred():
        calls["n"] += 1
        return calls["n"] >= 4

    def sleep(s):
        t["now"] += s

    assert wait_until(pred, timeout=10, interval=1, sleep=sleep, clock=lambda: t["now"])
    assert calls["n"] == 4


def test_wait_until_times_out():
    t = {"now": 0.0}

    def sleep(s):
        t["now"] += s

    assert not wait_until(lambda: False, timeout=3, interval=1, sleep=sleep, clock=lambda: t["now"])
