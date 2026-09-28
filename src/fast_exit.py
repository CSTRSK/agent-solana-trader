#!/usr/bin/env python3
"""On-Chain-Ausstieg: Kurs der pump.fun-Kurve in Echtzeit verfolgen.

Warum: der Ausstieg wird bisher per RPC-Abfrage alle 2 s geprueft. Bis der Stop das
merkt, ist der Kurs schon durchgefallen - der -10-%-Stop fuellt real bei -22 %
(Median ueber 8.500 Stop-Ausstiege). Statt zu fragen, hoert dieser Watcher per
`accountSubscribe` direkt auf das Kurven-Konto: jede Aenderung (jeder Kauf/Verkauf)
kommt als Push, typischerweise in 50-300 ms.

Modi:
  fast_exit.py --test <mint>     Nur beobachten und ausgeben (kein Handel). Zeigt
                                 Reaktionszeit und Kurs im Vergleich zum 2-s-Raster.
  fast_exit.py --daemon          Ueberwacht die offene Live-Position
                                 (live-position.json) und verkauft beim Ausloesen.

Regeln im Daemon: Stop (LIVE_SL), Break-even-Floor, Trailing, Haltezeit - dieselben
Werte wie im Bot (Umgebungsvariablen der Unit, sonst Standard).
"""
import base64, json, os, struct, sys, time, asyncio

WS_ENDPUNKTE = [
    "wss://api.mainnet-beta.solana.com",
    "wss://solana-rpc.publicnode.com",
]
PUMP_PROG = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
DIR = os.environ.get("SNIPER_DIR", ".")
LIVE_POS = os.path.join(DIR, "live-position.json")
LOGF = os.path.join(DIR, "fast-exit.jsonl")

# Regeln (identisch zur Unit)
LIVE_SL = float(os.environ.get("LIVE_SL", "0.90"))
BE_AT = float(os.environ.get("BE_AT", "1.20"))
BE_FLOOR = float(os.environ.get("BE_FLOOR", "0.98"))
TRAIL = float(os.environ.get("TRAIL", "0.90"))
LIVE_HOLD = int(os.environ.get("LIVE_HOLD", "3600"))
CHECK_S = float(os.environ.get("CHECK_S", "1"))


def curve_pda(mint):
    from solders.pubkey import Pubkey
    pda, _ = Pubkey.find_program_address(
        [b"bonding-curve", bytes(Pubkey.from_string(mint))], Pubkey.from_string(PUMP_PROG))
    return str(pda)


def mcap_sol_from_data(data: bytes):
    """Market Cap in SOL aus den Kurvendaten.

    Layout: 8 Byte Diskriminator, dann vTokens, vSol, realTokens, realSol, supply (je u64).
    vSol steht in Lamports, die Tokenmengen in Roh-Einheiten (6 Nachkommastellen) - die
    Einheiten kuerzen sich im Verhaeltnis, am Ende muss aber von Lamports in SOL
    umgerechnet werden (Faktor 1e9).
    """
    if len(data) < 48:
        return None
    vt, vs, rt, rs, sup = struct.unpack_from("<QQQQQ", data, 8)
    if not vt or not sup:
        return None
    mcap = (vs / vt) * sup / 1e9
    if mcap <= 0 or mcap > 1e6:      # Schutz gegen Fehldeutungen
        return None
    return mcap


def log(obj):
    try:
        with open(LOGF, "a") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    except Exception:
        pass


async def beobachte(mint, entry_mcap=None, laufzeit=None, still=False):
    """Hört auf das Kurven-Konto. Gibt bei jedem Update (ts, mcap, ratio) an den callback."""
    import websockets
    pda = curve_pda(mint)
    t_start = time.time()
    letzte = 0.0
    while True:
        for ep in WS_ENDPUNKTE:
            try:
                async with websockets.connect(ep, ping_interval=20, close_timeout=5) as ws:
                    await ws.send(json.dumps({
                        "jsonrpc": "2.0", "id": 1, "method": "accountSubscribe",
                        "params": [pda, {"encoding": "base64", "commitment": "processed"}]}))
                    if not still:
                        print(f"verbunden: {ep} · Kurve {pda[:8]}… (Konto-Abo aktiv)", flush=True)
                    while True:
                        nachricht = await asyncio.wait_for(ws.recv(), timeout=90)
                        d = json.loads(nachricht)
                        params = (d.get("params") or {}).get("result")
                        if not params:
                            continue
                        wert = params.get("value", {}) or {}
                        daten = (wert.get("data") or [None])[0]
                        if not daten:
                            continue
                        mcap = mcap_sol_from_data(base64.b64decode(daten))
                        if mcap is None:
                            continue
                        t = time.time()
                        abstand = t - letzte if letzte else 0.0
                        letzte = t
                        ratio = (mcap / entry_mcap) if entry_mcap else None
                        yield t, mcap, ratio, abstand
                        if laufzeit and (t - t_start) > laufzeit:
                            return
            except asyncio.CancelledError:
                return
            except Exception as e:
                if not still:
                    print(f"  WS-Fehler ({ep}, {type(e).__name__}) → neuer Versuch in 3 s",
                          flush=True)
                await asyncio.sleep(3)
                if laufzeit and (time.time() - t_start) > laufzeit:
                    return
                continue


async def test(mint, sekunden=90, entry_mcap=None):
    print(f"Test-Beobachtung {sekunden} s · Mint {mint[:12]}…")
    n = 0
    abstaende = []
    async for t, mcap, ratio, ab in beobachte(mint, entry_mcap, laufzeit=sekunden):
        n += 1
        if ab:
            abstaende.append(ab)
        if n <= 6 or n % 10 == 0:
            r = f"{ratio:.3f}x" if ratio else "-"
            print(f"  [{time.strftime('%H:%M:%S', time.gmtime(t))}] Kurs {mcap:10.2f} SOL "
                  f"· {r} · Abstand {ab*1000:6.0f} ms", flush=True)
    if abstaende:
        abstaende.sort()
        mitte = abstaende[len(abstaende) // 2]
        print(f"\nUpdates: {n} in {sekunden} s · Abstand Median {mitte*1000:.0f} ms "
              f"(schnellster {min(abstaende)*1000:.0f} ms)")
        print(f"Zum Vergleich: das 2-s-Raster merkt eine Bewegung im Schnitt nach "
              f"~1000 ms, spaetestens nach 2000 ms.")
    else:
        print(f"Updates: {n} (keine Abstaende messbar)")


def ausstiegsgrund(entry, last, peak, t0, jetzt):
    """Dieselben Regeln wie im Bot - nur eben in Millisekunden statt Sekunden."""
    if not entry or not last:
        return None, 0.0
    r = last / entry
    if r <= LIVE_SL:
        return "SL", r
    h = peak / entry if entry else 0.0
    if h >= BE_AT:
        if r <= BE_FLOOR:
            return "BE", r
        if r <= h * TRAIL:
            return "TRAIL", r
    if (jetzt - t0) >= LIVE_HOLD:
        return "TIME", r
    return None, r


async def daemon():
    """Ueberwacht die offene Live-Position und verkauft on-chain ausgeloest."""
    import subprocess
    print("fast_exit Daemon: wartet auf eine offene Live-Position", flush=True)
    while True:
        pos = None
        try:
            pos = json.load(open(LIVE_POS))
        except Exception:
            pos = None
        if not pos or not pos.get("mint") or pos.get("closed"):
            await asyncio.sleep(CHECK_S)
            continue
        mint = pos["mint"]
        entry = float(pos.get("entry") or 0)
        peak = float(pos.get("peak") or 0) or entry
        t0 = float(pos.get("t0") or time.time())
        print(f"Position erkannt: {mint[:12]}… · Einstieg {entry:.2f} · Regeln aktiv",
              flush=True)
        try:
            async for t, mcap, ratio, ab in beobachte(mint, entry, still=True):
                peak = max(peak, mcap)
                grund, r = ausstiegsgrund(entry, mcap, peak, t0, t)
                if grund:
                    print(f"  [FAST-EXIT] {grund} bei {r:.3f}x (Kurs {mcap:.2f}, "
                          f"Reaktion {ab*1000:.0f} ms) → Verkauf", flush=True)
                    log({"ts": round(t), "mint": mint, "reason": grund,
                         "ratio": round(r, 3), "mcap": round(mcap, 2),
                         "react_ms": round(ab * 1000)})
                    subprocess.run([sys.executable, os.path.join(DIR, "solana-snipe.py"),
                                    "sell", mint], cwd=DIR, timeout=180)
                    break
        except asyncio.CancelledError:
            return
        except Exception as e:
            print("  (Wachfehler:", type(e).__name__, ") → neuer Versuch", flush=True)
        await asyncio.sleep(3)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] not in ("--test", "--daemon"):
        print(__doc__); sys.exit(1)
    if args[0] == "--daemon":
        asyncio.run(daemon())
    else:
        if len(args) < 2:
            print("Mint fehlt"); sys.exit(1)
        mint = args[1]
        sek = int(args[2]) if len(args) > 2 else 90
        entry = None
        try:
            curl = json.load(open(os.path.join(DIR, "live-position.json")))
            if curl.get("mint") == mint:
                entry = float(curl.get("entry") or 0) or None
        except Exception:
            pass
        asyncio.run(test(mint, sek, entry))
