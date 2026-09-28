# Ergebnisse

- **`results.md`** — der ausgewertete Bericht (109.358 Papiertrades, Summe −927,18 SOL).
- **`stats.json`** — dieselben Zahlen maschinenlesbar, inklusive Aufteilung nach
  Ausstiegsgrund, Marktkapitalisierung, Haltedauer und den Rohfeldern der Daten.
- **`trades_sample.jsonl`** — die **letzten 2.000 Trades** in Originalform.

## Felder in `trades_sample.jsonl`

| Feld | Bedeutung |
|---|---|
| `ts` | Zeitpunkt des Abschlusses (Unix-Sekunden) |
| `mint` | Mint-Adresse des Tokens (öffentlich, aus der Blockchain) |
| `symbol` | Tokenkürzel, wie es im Handelsstrom stand |
| `group` | `filter` = nach der Regelmenge gehandelt, `control` = Vergleichsgruppe |
| `filtered` | ob der Kandidat die Filter passiert hat |
| `entry_mcap` | Marktkapitalisierung beim simulierten Kauf (Mio. $) |
| `exit_mcap` | Marktkapitalisierung beim simulierten Verkauf |
| `peak_mcap` / `trough_mcap` | höchster / tiefster Stand während der Haltezeit |
| `reason` | Ausstiegsgrund: `TP`, `SL`, `TRAIL`, `TIME` |
| `hold_s` | Haltedauer in Sekunden |
| `pnl_sol` | Ergebnis des simulierten Trades in SOL (Einsatz 0,20 SOL, 2 % Gebühr, 0,002 SOL Gas) |

## Nachrechnen

```bash
python3 ../src/analyse_public.py     # Kennzahlen aus den Rohdaten
python3 ../src/analyze_paper.py      # Ausstiegsregeln auf den beobachteten Kurspfaden
```

Beide Skripte sind ausschließlich lesend und brauchen keinen Schlüssel und keine
Netzwerkverbindung.
