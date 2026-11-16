"""Споживання CPU і пам'яті процесами під час навантаження (для локального запуску без Docker).

Процеси знаходяться за портами, які вони слухають. Скрипт запускає команду
навантаження й вимірює для кожного процесу час CPU та максимальний RSS:

    python proc_stats.py --label monolith --ports app=8001 -- python bench.py --base ... --label monolith

У Docker аналогічні дані дає команда `docker stats`.
"""
import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil


def pid_for_port(port: int):
    for c in psutil.net_connections(kind="inet"):
        if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port and c.pid:
            p = psutil.Process(c.pid)
            # nginx/uvicorn можуть мати батьківський процес — беремо його разом з дочірніми
            return p.parent() if p.parent() and p.parent().name() == p.name() else p
    raise SystemExit(f"не знайдено процес на порту {port}")


def tree(p):
    return [p] + p.children(recursive=True)


def cpu(p):
    total = 0.0
    for x in tree(p):
        try:
            t = x.cpu_times()
            total += t.user + t.system
        except psutil.NoSuchProcess:
            pass
    return total


def rss(p):
    total = 0
    for x in tree(p):
        try:
            total += x.memory_info().rss
        except psutil.NoSuchProcess:
            pass
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--ports", nargs="+", required=True, help="name=port")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd

    procs = {n: pid_for_port(int(p)) for n, p in (x.split("=") for x in a.ports)}
    idle_rss = {n: rss(p) for n, p in procs.items()}
    cpu0 = {n: cpu(p) for n, p in procs.items()}
    peak = dict(idle_rss)
    stop = threading.Event()

    def sampler():
        while not stop.wait(0.2):
            for n, p in procs.items():
                peak[n] = max(peak[n], rss(p))

    threading.Thread(target=sampler, daemon=True).start()
    t0 = time.perf_counter()
    rc = subprocess.call(cmd)
    wall = time.perf_counter() - t0
    stop.set()

    res = {"label": a.label, "wall_s": round(wall, 1), "processes": {
        n: {"cpu_s": round(cpu(p) - cpu0[n], 2), "idle_rss_mb": round(idle_rss[n] / 2**20, 1),
            "peak_rss_mb": round(peak[n] / 2**20, 1)} for n, p in procs.items()}}
    out = Path(__file__).parent / "results" / f"{a.label}-resources.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2))
    print(json.dumps(res, ensure_ascii=False, indent=2))
    sys.exit(rc)


if __name__ == "__main__":
    main()
