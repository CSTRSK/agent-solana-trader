# Agent Solana Trader — Papierbetrieb

**English:** An autonomous agent that watches new pump.fun tokens, applies a rule set, and
simulates every buy and sell on paper (no real funds). This repository documents the method
and publishes the measured results: **109,358 paper trades, −927.18 SOL, 13.01 % win rate,
expectancy −0.008478 SOL per trade.** The ruleset as tested loses money.

---

## Was das ist

Ein Agent beobachtet den Handelsstrom neuer Token auf pump.fun, bildet Kandidaten nach festen
Regeln (Marktkapitalisierung, Liquidität, Reserve, Whale-Anteil) und führt für jeden Kandidaten
einen **simulierten** Kauf und Verkauf durch — vollständig auf Papier. Es fließt kein echtes Geld:
Der Dienst läuft mit `LIVE=0` und `PAPER=1`, die Live-Ausführung ist fail-closed und wird in
diesem Repository **bewusst nicht** veröffentlicht.

Ziel war eine einzige Frage: **Trägt diese Regelmenge unter realitätsnahen Bedingungen Geld —
oder nicht?**

Antwort nach 109.358 Papiertrades: **Nein, in dieser Form nicht.**

## Ergebnis

| Kennzahl | Wert |
|---|---:|
| Trades | 109.358 |
| Summe | **−927,18 SOL** |
| Erwartungswert je Trade | **−0,008478 SOL** |
| Median je Trade | −0,006931 SOL |
| Trefferquote | **13,01 %** (14.223 Gewinne / 95.134 Verluste) |
| Summe aller Gewinne | +1.623,95 SOL |
| Summe aller Verluste | −2.551,14 SOL |
| größter Gewinn | +16,44 SOL |
| größter Verlust | −0,196 SOL |

### Nach Ausstiegsgrund

| Grund | n | Summe SOL | Treffer | Median Haltedauer |
|---|---:|---:|---:|---:|
| SL (Stop) | 36.158 | **−2.059,54** | 0 | 19 s |
| TIME (Zeitausstieg) | 60.343 | −133,79 | 3.264 | 600 s |
| TRAIL (Trailing) | 11.067 | **+747,24** | 9.169 | 36 s |
| TP (Ziel) | 1.790 | **+518,90** | 1.790 | 15 s |

Die Kernaussage steckt hier drin: **Der Stop verliert mehr, als Ziel und Trailing zusammen
hereinholen.** Der durchschnittliche Stop-Verlust beträgt 0,057 SOL (28 % des Einsatzes von
0,20 SOL) bei einem Median von nur 19 Sekunden Haltedauer — die Kurse fallen in Sekunden, ein
Stop auf dem Papier ist keine Garantie für einen Stop im Markt.

### Nach Einstiegs-Marktkapitalisierung (Mio. $)

| Bereich | n | Summe SOL | Trefferquote |
|---|---:|---:|---:|
| < 30 | 66.441 | −259,10 | 9,7 % |
| 30–50 | 32.697 | −451,40 | 18,1 % |
| 50–75 | 6.149 | −164,03 | 20,6 % |
| 75–100 | 999 | −18,17 | 24,7 % |
| 100–150 | 523 | −12,99 | 24,5 % |
| ≥ 150 | 2.549 | −21,48 | 7,3 % |

Jeder Bereich ist negativ. Am wenigsten schlecht sind 75–150 Mio. $, am schlechtesten die ganz
kleinen (< 30 Mio. $, 9,7 %) und die ganz großen (≥ 150 Mio. $).

### Nach Haltedauer

| Bereich | n | Summe SOL | Trefferquote |
|---|---:|---:|---:|
| ≤ 10 s | 329 | +54,28 | 59,3 % |
| 11–30 s | 32.590 | −1.116,99 | 14,5 % |
| 31–60 s | 7.903 | +200,75 | 39,3 % |
| > 60 s | 68.536 | −65,22 | 9,1 % |

## Methode

- Kandidaten über den Handelsstrom neuer pump.fun-Token, Mindest- und Höchstgrenzen der
  Marktkapitalisierung, Reserve- und Whale-Filter; die vollständige Regelmenge samt aller
  Schwellen steht in [`src/paper-sniper.service`](src/paper-sniper.service) (60 Parameter,
  unverändert aus dem Messlauf).
- Einsatz 0,20 SOL je Trade, angenommen 2 % Gebühr und 0,002 SOL Gas.
- Kurspfade werden im Betrieb mitgeschrieben (`paper-paths.jsonl`); die Ausstiegsregeln werden
  auf den beobachteten Pfaden nachgerechnet. Die Beobachtungspunkte sind ausgedünnt
  (`SAMPLE_EVERY=20`), um die Dateien beherrschbar zu halten.
- Auswertung: [`src/analyze_paper.py`](src/analyze_paper.py) und
  [`src/analyse_public.py`](src/analyse_public.py) (streamend, ohne die großen Pfaddateien
  komplett zu laden).

## Grenzen — bitte vor dem Zitieren lesen

- **Papier heißt Papier.** Es gibt keine echten Orders, deshalb keine Slippage jenseits der
  angenommenen 2 % Gebühr und keine Teilausführungen. Ein Stop auf dem Papier trifft exakt,
  im Markt trifft er durch ein fallendes Buch hindurch.
- **Rug-Pulls in einem Tick** sind nur so genau modelliert, wie die Beobachtungspunkte es
  zulassen (alle 20 Zyklen).
- **Keine belastbare Kontrollgruppe** (7 Kandidaten), die Zahlen sind eine Messreihe, kein
  Vergleich gegen Zufall.
- **Viele Parameter** wurden über mehrere Iterationen angepasst. Damit ist die
  Überanpassungsgefahr real; die Ergebnisse sind eine Beschreibung des Messlaufs, keine
  Prognose.
- Nur öffentliche RPC-Endpunkte, entsprechend begrenzte Datenqualität (Rate-Limits,
  gelegentliche Lücken).
- Der Lauf ist eine Momentaufnahme eines sich schnell ändernden Marktumfelds.

## Reproduzieren

```bash
pip install -r requirements.txt
SNIPER_DIR=$PWD PAPER=1 LIVE=0 python3 src/paper-sniper.py     # Papierbetrieb
python3 src/analyse_public.py                                  # Kennzahlen aus den Rohdaten
python3 src/analyze_paper.py                                   # Ausstiegsregeln nachrechnen
```

## Geheimnisse

Dieses Repository enthält **keine** privaten Schlüssel, keine Seed-Phrase, keine
Wallet-Passphrase, keine API-Schlüssel, keine absoluten Pfade und keine Serveradressen.
Geprüft wurde vor dem Commit automatisiert über acht Muster; die drei Treffer der
Adressprüfung sind öffentliche Mint-Adressen (USDC, Wrapped SOL) und das pump.fun-Programm.
Die Live-Ausführung mit Wallet-Entschlüsselung ist bewusst nicht enthalten.

## Rohdaten

Die vollständigen Rohdaten umfassen 36 MB Trades und 333 MB Kurspfade und werden nicht
mitgeliefert. Im Repository liegen die Kennzahlen als JSON und eine Stichprobe der
**letzten 2.000 Trades** in Originalform.

## Lizenz

MIT, siehe [LICENSE](LICENSE).
