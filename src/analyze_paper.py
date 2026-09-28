#!/usr/bin/env python3
"""Auswertung der Papierwoche: Was haette wie getradet?

Liest paper-trades.jsonl (mit Kursverlauf 'path' ab Fix 16) und rechnet echte
Ausstiegsregeln ueber die beobachteten Kurspfade durch - TP, Stop, Trailing, Haltedauer.
Ohne Kursverlauf werden nur die Grundzahlen ausgegeben.

Aufruf:  python3 analyze_paper.py            (Textbericht)
         python3 analyze_paper.py --json     (Maschinenlesbar)
         python3 analyze_paper.py --since 7  (nur die letzten N Tage)
"""
import json, os, sys, statistics, datetime, itertools, time

TRADES = "paper-trades.jsonl"
PATHS = "paper-paths.jsonl"   # FIX17: vollstaendige Kurspfade (bis HOLD)
SIZE = 0.20          # Einsatz wie im geplanten Live-Betrieb (FIX19: vorher 0,05)
FEE = 0.02
GAS = 0.002


def load(since_days=None):
    rows = []
    cutoff = 0
    if since_days:
        cutoff = time.time() - since_days * 86400
    for line in open(TRADES):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if (r.get("ts") or 0) >= cutoff:
            rows.append(r)
    return rows


def load_paths(since_days=None, max_tokens=2000, max_points=120):
    """Kurspfade für die Auswertung - schnell und aussagekräftig genug.

    FIX21: Die Dateien sind mit den Kursverläufen sehr groß geworden. Deshalb:
    - Vorprüfung per Textsuche (spart das Parsen von Zeilen ohne realen Einstieg)
    - nur die neuesten `max_tokens` Token
    - Pfade auf `max_points` Punkte ausgedünnt (gleichmäßig, erster und letzter Punkt bleiben)
    2000 Token sind statistisch reichlich - die Laufzeit sinkt von Minuten auf Sekunden.
    """
    cutoff = time.time() - since_days * 86400 if since_days else 0
    rows = []
    for src in (PATHS, TRADES):
        if not os.path.exists(src):
            continue
        for line in open(src):
            if '"rebased": true' not in line and '"rebased":true' not in line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            p = r.get("path") or []
            m = r.get("mint")
            if not m or len(p) < 3 or (r.get("ts") or 0) < cutoff:
                continue
            if r.get("rebased") is not True:
                continue
            if len(p) > max_points:
                step = len(p) / max_points
                p = [p[int(i * step)] for i in range(max_points - 1)] + [p[-1]]
            rows.append({"mint": m, "ts": r.get("ts"), "symbol": r.get("symbol"), "path": p,
                         "entry_mcap": r.get("entry_mcap"), "peak_mcap": r.get("peak_mcap"),
                         "reserve_sol": r.get("reserve_sol"),
                         "reason": r.get("reason") or r.get("closed_reason")})
    rows.sort(key=lambda r: -(r.get("ts") or 0))
    best = {}
    for r in rows:
        if r["mint"] not in best:
            best[r["mint"]] = r
        if len(best) >= max_tokens * 2:
            break
    return list(best.values())[:max_tokens]


def simulate(path, tp, sl, trail, hold, be_floor=None):
    """Gibt (exit_ratio, reason, seconds) zurueck - ueber den echten Pfad."""
    peak = 1.0
    for t, r in path:
        peak = max(peak, r)
        if r >= tp:
            return tp, "TP", t
        if r <= sl:
            return r, "SL", t
        if trail and peak >= 1.20 and r <= peak * trail:
            return r, "TRAIL", t
        if (be_floor and peak >= 1.25 and r <= be_floor):
            return r, "BE", t
        if t >= hold:
            return r, "TIME", t
    return (path[-1][1] if path else 1.0), "OPEN", (path[-1][0] if path else 0)


def pnl_of(ratio):
    return (ratio - 1.0) * SIZE - SIZE * FEE - GAS


def main():
    args = sys.argv[1:]
    as_json = "--json" in args
    since = None
    if "--since" in args:
        try:
            since = float(args[args.index("--since") + 1])
        except Exception:
            pass

    rows = load(since)
    if not rows:
        print("Keine Papier-Trades vorhanden.")
        return

    mit_pfad = load_paths(since)
    print(f"Papier-Trades: {len(rows)}  ·  davon mit Kursverlauf: {len(mit_pfad)}")
    if rows:
        t0 = datetime.datetime.fromtimestamp(min(r["ts"] for r in rows)).strftime("%d.%m %H:%M")
        t1 = datetime.datetime.fromtimestamp(max(r["ts"] for r in rows)).strftime("%d.%m %H:%M")
        print(f"Zeitraum: {t0} – {t1}")

    # --- Grundzahlen der bisherigen Regel (TP1.6 / SL0.5 / HOLD1800) ---
    print("\n=== Grundzahlen (alle Gruppen) ===")
    for grp in ("filter", "control", "live"):
        g = [r for r in rows if r.get("group") == grp and r.get("pnl_sol") is not None]
        if not g:
            continue
        p = [r["pnl_sol"] for r in g]
        print(f"  {grp:8s} n={len(g):5d}  Trefferquote {100*len([x for x in p if x>0])/len(p):5.1f}%  "
              f"Summe {sum(p):+.4f} SOL  Ø {sum(p)/len(p):+.5f}  "
              f"best {max(p):+.4f}  schlecht {min(p):+.4f}")

    # --- Wie weit laufen die Token ueberhaupt? ---
    print("\n=== Kursentwicklung (Peak ggue. Einstieg) ===")
    for x in (1.2, 1.5, 2.0, 3.0, 5.0, 10.0):
        n = len([r for r in rows if (r.get("peak_mcap", 0) / max(r.get("entry_mcap", 1e-9), 1e-9)) >= x])
        print(f"  erreicht {x:4.1f}x: {n:5d}  ({100*n/len(rows):5.1f} %)")
    ver = [r for r in rows if (r.get("peak_mcap", 0) / max(r.get("entry_mcap", 1e-9), 1e-9)) >= 1.5]
    if ver:
        print(f"  -> nur {len(ver)} von {len(rows)} werden ueberhaupt 1,5x (das ist die Auswahl, "
              f"die zaehlt)")

    # --- Reserve beim Einstieg vs. Ergebnis ---
    if any("reserve_sol" in r for r in rows):
        print("\n=== Kurven-Reserve beim Einstieg vs. Ergebnis ===")
        buckets = [("0–1 SOL", 0, 1), ("1–3 SOL", 1, 3), ("3–10 SOL", 3, 10), (">10 SOL", 10, 1e9)]
        for name, lo, hi in buckets:
            g = [r for r in rows if isinstance(r.get("reserve_sol"), (int, float))
                 and lo <= r["reserve_sol"] < hi and r.get("pnl_sol") is not None]
            if not g:
                continue
            p = [r["pnl_sol"] for r in g]
            pk = statistics.median([r.get("peak_mcap", 0) / max(r.get("entry_mcap", 1e-9), 1e-9)
                                    for r in g])
            print(f"  {name:9s} n={len(g):5d}  Ø {sum(p)/len(p):+.5f} SOL  "
                  f"Median-Peak {pk:.2f}x")

    # --- Regelvergleich ueber die echten Pfade ---
    if len(mit_pfad) >= 30:
        print(f"\n=== Regelvergleich (ueber {len(mit_pfad)} echte Kurspfade) ===")
        combos = []
        for tp, sl, trail, hold in itertools.product(
                (1.20, 1.30, 1.50, 1.60, 2.00, 2.50, 3.00, 5.00),
                (0.40, 0.50, 0.60, 0.70, 0.80, 0.85, 0.90),
                (None, 0.50, 0.65, 0.80, 0.90),
                (300, 600, 1800, 3600)):
            tot = 0.0
            wins = 0
            hd = []
            for r in mit_pfad:
                ex, why, sec = simulate(r["path"], tp, sl, trail, hold)
                v = pnl_of(ex)
                tot += v
                wins += 1 if v > 0 else 0
                hd.append(sec)
            combos.append({"tp": tp, "sl": sl, "trail": trail, "hold": hold,
                           "sum": round(tot, 4), "avg": round(tot / len(mit_pfad), 5),
                           "winrate": round(100 * wins / len(mit_pfad), 1),
                           "hold_med": round(statistics.median(hd))})
        combos.sort(key=lambda c: -c["sum"])
        print("  beste 10 (Summe SOL bei 0,05 SOL Einsatz je Trade):")
        for c in combos[:10]:
            tr = "-" if c["trail"] is None else f"{c['trail']:.2f}"
            print(f"    TP {c['tp']:.1f} · SL {c['sl']:.2f} · Trail {tr:>4s} · "
                  f"Halten {c['hold']:5d}s -> {c['sum']:+.3f} SOL  "
                  f"(Ø {c['avg']:+.5f}, Treffer {c['winrate']:4.1f}%, "
                  f"Median-Haltedauer {c['hold_med']}s)")
        schlecht = combos[-3:]
        print("  schlechteste 3:")
        for c in schlecht:
            tr = "-" if c["trail"] is None else f"{c['trail']:.2f}"
            print(f"    TP {c['tp']:.1f} · SL {c['sl']:.2f} · Trail {tr:>4s} · "
                  f"Halten {c['hold']:5d}s -> {c['sum']:+.3f} SOL")
        if as_json:
            print(json.dumps({"trades": len(rows), "with_path": len(mit_pfad),
                              "best": combos[:10]}, ensure_ascii=False))
    else:
        print(f"\n(Regelvergleich braucht >=30 Trades mit Kursverlauf - "
              f"aktuell {len(mit_pfad)}. Der Verlauf wird erst seit Fix 16 geschrieben.)")


if __name__ == "__main__":
    import time
    main()
