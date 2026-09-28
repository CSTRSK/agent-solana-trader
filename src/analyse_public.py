#!/usr/bin/env python3
"""Schnelle Auswertung des Papier-Snipers (streamend, ohne die 333-MB-Pfaddatei
am Stueck zu laden). Ergebnis: /tmp/paper_report.md und /tmp/paper_stats.json
"""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict

TRADES = "paper-trades.jsonl"

n = 0
summe = 0.0
pnls = []
nach_grund = defaultdict(lambda: {"n": 0, "sum": 0.0, "win": 0, "halte": []})
nach_gruppe = defaultdict(lambda: {"n": 0, "sum": 0.0, "win": 0})
nach_mcap = defaultdict(lambda: {"n": 0, "sum": 0.0, "win": 0})
halte_bucket = defaultdict(lambda: {"n": 0, "sum": 0.0, "win": 0})
felder = Counter()
extreme = []
for zeile in open(TRADES, encoding="utf-8"):
    try:
        r = json.loads(zeile)
    except Exception:
        continue
    for k in r:
        felder[k] += 1
    if r.get("group") != "filter":
        continue
    p = r.get("pnl_sol")
    if p is None:
        continue
    n += 1
    summe += p
    pnls.append(p)
    g = r.get("group", "?")
    nach_gruppe[g]["n"] += 1
    nach_gruppe[g]["sum"] += p
    nach_gruppe[g]["win"] += 1 if p > 0 else 0
    gr = r.get("reason") or "?"
    nach_gruppe[gr]["n"] = nach_gruppe[gr]["n"]       # noqa - absichtlich leer
    nach_grund[gr]["n"] += 1
    nach_grund[gr]["sum"] += p
    nach_grund[gr]["win"] += 1 if p > 0 else 0
    if r.get("hold_s") is not None:
        nach_grund[gr]["halte"].append(r["hold_s"])
    em = r.get("entry_mcap")
    if em is not None:
        b = ("<30" if em < 30 else "30-50" if em < 50 else "50-75" if em < 75 else
             "75-100" if em < 100 else "100-150" if em < 150 else ">=150")
        nach_mcap[b]["n"] += 1
        nach_mcap[b]["sum"] += p
        nach_mcap[b]["win"] += 1 if p > 0 else 0
    hs = r.get("hold_s")
    if hs is not None:
        b = "<=10s" if hs <= 10 else "11-30s" if hs <= 30 else "31-60s" if hs <= 60 else ">60s"
        halte_bucket[b]["n"] += 1
        halte_bucket[b]["sum"] += p
        halte_bucket[b]["win"] += 1 if p > 0 else 0
    if len(extreme) < 5 or p > extreme[0][1]:
        extreme.append((r.get("symbol"), p, r.get("reason"), r.get("hold_s")))
        extreme.sort(key=lambda x: x[1])
        extreme = extreme[-5:]

siege = sum(1 for p in pnls if p > 0)
null = sum(1 for p in pnls if p == 0)
verluste = sum(1 for p in pnls if p < 0)
stats = {
    "n": n, "summe_sol": round(summe, 2),
    "siege": siege, "verluste": verluste, "null": null,
    "trefferquote": round(100 * siege / n, 2),
    "avg": round(summe / n, 6),
    "median": round(statistics.median(pnls), 6),
    "groesster_gewinn": round(max(pnls), 4), "groesster_verlust": round(min(pnls), 4),
    "summe_gewinne": round(sum(p for p in pnls if p > 0), 2),
    "summe_verluste": round(sum(p for p in pnls if p < 0), 2),
    "std": round(statistics.pstdev(pnls), 5),
    "felder": dict(felder.most_common(12)),
    "nach_grund": {k: {"n": v["n"], "sum": round(v["sum"], 2), "win": v["win"],
                       "halte_med": round(statistics.median(v["halte"])) if v["halte"] else None}
                   for k, v in sorted(nach_grund.items(), key=lambda x: -x[1]["n"])},
    "nach_mcap": {k: {"n": v["n"], "sum": round(v["sum"], 2),
                      "winrate": round(100 * v["win"] / v["n"], 1)}
                  for k, v in nach_mcap.items()},
    "nach_haltezeit": {k: {"n": v["n"], "sum": round(v["sum"], 2),
                           "winrate": round(100 * v["win"] / v["n"], 1)}
                       for k, v in halte_bucket.items()},
    "extreme": extreme,
}

zeilen = [f"# Papier-Sniper — Ergebnisse", "",
          f"- Trades (Gruppe filter): **{n}**", f"- Summe: **{stats['summe_sol']:+.2f} SOL**",
          f"- Treffer/Verluste: {siege} / {verluste} (Trefferquote {stats['trefferquote']} %)",
          f"- Erwartungswert je Trade: **{stats['avg']:+.6f} SOL**",
          f"- Median je Trade: {stats['median']:+.6f} SOL",
          f"- groesster Gewinn {stats['groesster_gewinn']:+.4f} SOL, groesster Verlust "
          f"{stats['groesster_verlust']:+.4f} SOL",
          f"- Summe aller Gewinne {stats['summe_gewinne']:+.2f} SOL gegen Summe aller "
          f"Verluste {stats['summe_verluste']:+.2f} SOL", "",
          "## Ausstiegsgrund", "", "| Grund | n | Summe SOL | Treffer | Median Haltedauer |",
          "|---|---:|---:|---:|---:|"]
for k, v in stats["nach_grund"].items():
    zeilen.append(f"| {k} | {v['n']} | {v['sum']:+.2f} | {v['win']} | {v['halte_med']} s |")
zeilen += ["", "## Nach Einstiegs-Marktkapital (Mio. $)", "", "| Bereich | n | Summe SOL | Trefferquote |",
           "|---|---:|---:|---:|"]
for k, v in stats["nach_mcap"].items():
    zeilen.append(f"| {k} | {v['n']} | {v['sum']:+.2f} | {v['winrate']} % |")
zeilen += ["", "## Nach Haltedauer", "", "| Bereich | n | Summe SOL | Trefferquote |", "|---|---:|---:|---:|"]
for k, v in stats["nach_haltezeit"].items():
    zeilen.append(f"| {k} | {v['n']} | {v['sum']:+.2f} | {v['winrate']} % |")
zeilen += ["", "## Felder in den Rohdaten (Haeufigkeit)", "",
           ", ".join(f"`{k}` ({v})" for k, v in stats["felder"].items())]

json.dump(stats, open("/tmp/paper_stats.json", "w"), indent=1, ensure_ascii=False)
open("/tmp/paper_report.md", "w", encoding="utf-8").write("\n".join(zeilen) + "\n")
print("\n".join(zeilen))
