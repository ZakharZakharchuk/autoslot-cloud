"""Зведення результатів: медіана трьох прогонів, таблиця results/summary.md і графіки results/*.png.

    python summarize.py
"""
import json
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

R = Path(__file__).parent / "results"
RUNS = ["r1", "r2", "r3"]
LABELS = {"monolith": "Моноліт", "microservices": "Мікросервіси"}
plt.rcParams.update({"font.family": "serif", "font.serif": ["Liberation Serif", "Times New Roman", "DejaVu Serif"],
                     "font.size": 11})


def load(label):
    return [json.loads((R / f"{label}-{r}.json").read_text())["scenarios"] for r in RUNS]


def med(runs, scen, level, key):
    vals = [x[scen][level][key] if level else x[scen][key] for x in runs]
    return round(statistics.median(vals), 1)


def main():
    data = {k: load(k) for k in LABELS}
    rows = []
    for scen, levels in (("services", ["1", "10", "50"]), ("availability", ["1", "10", "50"]), ("booking", ["1", "10"])):
        for lv in levels:
            m, s = data["monolith"], data["microservices"]
            rows.append((scen, lv, med(m, scen, lv, "rps"), med(s, scen, lv, "rps"),
                         med(m, scen, lv, "p50_ms"), med(s, scen, lv, "p50_ms"),
                         med(m, scen, lv, "p95_ms"), med(s, scen, lv, "p95_ms")))
    nd = (med(data["monolith"], "notify-delay", None, "p50_ms"), med(data["microservices"], "notify-delay", None, "p50_ms"))

    md = ["| Сценарій | Паралельність | RPS моноліт | RPS мікросервіси | p50 моноліт, мс | p50 мікросервіси, мс | p95 моноліт, мс | p95 мікросервіси, мс |",
          "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    md += [f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} | {r[6]} | {r[7]} |" for r in rows]
    md += ["", f"Затримка появи підтвердження (p50): моноліт {nd[0]} мс, мікросервіси {nd[1]} мс."]
    (R / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (R / "summary.json").write_text(json.dumps({"rows": rows, "notify_delay_p50": nd}, ensure_ascii=False, indent=1))
    print("\n".join(md))

    # Графік 1: пропускна здатність при паралельності 10
    names = {"services": "Каталог", "availability": "Вільні слоти", "booking": "Запис"}
    sel = [r for r in rows if r[1] == "10"]
    x = range(len(sel))
    fig, ax = plt.subplots(figsize=(7, 3.6))
    ax.bar([i - 0.2 for i in x], [r[2] for r in sel], 0.4, label=LABELS["monolith"], color="#4d4d4d")
    ax.bar([i + 0.2 for i in x], [r[3] for r in sel], 0.4, label=LABELS["microservices"], color="#a6a6a6")
    for i, r in enumerate(sel):
        ax.text(i - 0.2, r[2], f"{r[2]:.0f}", ha="center", va="bottom", fontsize=9)
        ax.text(i + 0.2, r[3], f"{r[3]:.0f}", ha="center", va="bottom", fontsize=9)
    ax.set_xticks(list(x), [names[r[0]] for r in sel])
    ax.set_ylabel("запитів за секунду")
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(R / "throughput-c10.png", dpi=200)

    # Графік 2: медіанна затримка при одному клієнті
    sel = [r for r in rows if r[1] == "1"]
    fig, ax = plt.subplots(figsize=(7, 3.6))
    ax.bar([i - 0.2 for i in x], [r[4] for r in sel], 0.4, label=LABELS["monolith"], color="#4d4d4d")
    ax.bar([i + 0.2 for i in x], [r[5] for r in sel], 0.4, label=LABELS["microservices"], color="#a6a6a6")
    for i, r in enumerate(sel):
        ax.text(i - 0.2, r[4], f"{r[4]:.1f}", ha="center", va="bottom", fontsize=9)
        ax.text(i + 0.2, r[5], f"{r[5]:.1f}", ha="center", va="bottom", fontsize=9)
    ax.set_xticks(list(x), [names[r[0]] for r in sel])
    ax.set_ylabel("p50, мс")
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(R / "latency-c1.png", dpi=200)

    # Графік 3: розподіл CPU між компонентами мікросервісної версії (якщо є дані proc_stats.py)
    if not all((R / f"microservices-{r}-resources.json").exists() for r in RUNS):
        return
    res = [json.loads((R / f"microservices-{r}-resources.json").read_text())["processes"] for r in RUNS]
    comps = list(res[0].keys())
    cpu = {c: statistics.median(x[c]["cpu_s"] for x in res) for c in comps}
    total = sum(cpu.values())
    order = sorted(comps, key=lambda c: -cpu[c])
    fig, ax = plt.subplots(figsize=(7, 3.0))
    ax.barh(order[::-1], [cpu[c] / total * 100 for c in order[::-1]], color="#7f7f7f")
    for i, c in enumerate(order[::-1]):
        ax.text(cpu[c] / total * 100 + 0.5, i, f"{cpu[c] / total * 100:.0f}%", va="center", fontsize=9)
    ax.set_xlabel("частка процесорного часу під час тесту, %")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(R / "cpu-by-component.png", dpi=200)
    print("CPU, с:", {c: round(v, 1) for c, v in cpu.items()})


if __name__ == "__main__":
    main()
