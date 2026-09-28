#!/usr/bin/env python3
"""Solana-Sniper: erkennt pump.fun-Launches live, kauft bei Momentum (Trigger)
und verkauft nach Stop / Break-even / Trailing. Papier-Simulation optional.
Ueberarbeitet nach Review: asynchroner Subprozess, WS-Reconnect, On-Chain-
Bestaetigung vor Statuswechsel, schlanker Zustand, Passphrase per Umgebung.
  --summary   aggregiert die Papier-Trades und gibt JSON aus
"""
import asyncio, json, os, sys, time
import websockets

DIR = os.environ.get("SNIPER_DIR", os.environ.get("SNIPER_DIR", "."))
TRADES = os.path.join(DIR, "paper-trades.jsonl")
CANDS = os.path.join(DIR, "paper-candidates.jsonl")
STATE = os.path.join(DIR, "paper-state.json")
PATHS = os.path.join(DIR, "paper-paths.jsonl")   # FIX17: vollstaendige Kurspfade
LIVESTATE = os.path.join(DIR, "live-position.json")
WS = "wss://pumpportal.fun/api/data"

RUN_S = int(os.environ.get("RUN_S", "172800"))
BUY_SOL = float(os.environ.get("BUY_SOL", "0.2"))
TP = float(os.environ.get("TP", "1.60"))
SL = float(os.environ.get("SL", "0.65"))
HOLD = int(os.environ.get("HOLD", "600"))
SAMPLE_EVERY = int(os.environ.get("SAMPLE_EVERY", "25"))
MIN_MCAP = float(os.environ.get("MIN_MCAP", "15"))
MAX_MCAP = float(os.environ.get("MAX_MCAP", "500"))
MAX_TRACK = int(os.environ.get("MAX_TRACK", "400"))
FEE = 0.02
GAS = 0.002

RPC = os.environ.get("RPC_URL", "https://api.mainnet-beta.solana.com")
POLL_S = float(os.environ.get("POLL_S", "3"))
CHECK_S = float(os.environ.get("CHECK_S", "1"))
PAPER = os.environ.get("PAPER", "0") == "1"
PAPER_TRAIL = float(os.environ.get("PAPER_TRAIL", "0"))        # FIX18: Trailing-Abstand
PAPER_TRAIL_FROM = float(os.environ.get("PAPER_TRAIL_FROM", "1.20"))  # FIX18: ab hier aktiv
OBSERVE_S = int(os.environ.get("OBSERVE_S", "1800"))            # FIX18: Aufzeichnungsfenster

LIVE = os.environ.get("LIVE", "0") == "1"
TRIGGER_X = float(os.environ.get("TRIGGER_X", "2.0"))
TRIGGER_WIN = int(os.environ.get("TRIGGER_WIN", "600"))
LIVE_SOL = float(os.environ.get("LIVE_SOL", "0.05"))
LIVE_TP = float(os.environ.get("LIVE_TP", "0"))
LIVE_SL = float(os.environ.get("LIVE_SL", "0.80"))
BE_AT = float(os.environ.get("BE_AT", "1.25"))
BE_FLOOR = float(os.environ.get("BE_FLOOR", "0.98"))
TRAIL = float(os.environ.get("TRAIL", "0.65"))
LIVE_HOLD = int(os.environ.get("LIVE_HOLD", "3600"))
SNIPE = os.path.join(DIR, "solana-snipe.py")

os.makedirs(DIR, exist_ok=True)


def now():
    return time.time()


def jline(path, obj):
    with open(path, "a") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


class Tracker:
    """Ein beobachteter Token (Kursverfolgung + optionale Papier-Position)."""

    def __init__(self, mint, symbol, entry_mcap, filtered, group, curve=None,
                 reserve=0.0):  # FIX16
        self.mint, self.symbol = mint, symbol
        self.entry = entry_mcap
        self.curve = curve
        self.filtered, self.group = filtered, group
        self.path = []              # FIX16: [Sekunden seit Start, Kursverhaeltnis]
        self.reserve = reserve      # FIX16: echte SOL-Reserve der Kurve beim Einstieg
        self.out = False            # FIX17: Pfad bereits dauerhaft abgelegt?
        self.entry0 = entry_mcap    # FIX19: Kurs zum Erstellungszeitpunkt
        self.rebased = False        # FIX19: Einstieg auf ersten Messkurs gesetzt?
        self.t0 = now()
        self.peak = entry_mcap
        self.trough = entry_mcap
        self.last = entry_mcap
        self.closed = False

    def on_trade(self, mcap):
        if mcap <= 0:
            return
        # FIX19: realistischer Einstieg - der Erstellungs-Kurs ist nicht kaufbar,
        # der erste messbare Kurs (ab ~5 s) ist es.
        if (not self.rebased and self.entry0 > 0
                and (now() - self.t0) >= 5.0):
            self.entry = mcap
            self.peak = mcap
            self.trough = mcap
            self.path = []
            self.rebased = True
        self.last = mcap
        self.peak = max(self.peak, mcap)
        self.trough = min(self.trough, mcap)
        # FIX16: Kursverlauf (alle 5 s, max 400 Punkte) fuer die Auswertung
        if self.entry > 0:
            t_rel = now() - self.t0
            # FIX21: feines Raster nur in der kritischen Phase, danach groeber
        _gap = 2.0 if (now() - self.t0) < 60 else 10.0
        if not self.path or now() - self.t0 - self.path[-1][0] >= _gap:
                self.path.append([round(t_rel, 1), round(mcap / self.entry, 3)])
                if len(self.path) > 400:
                    del self.path[0]

    def check(self, t):
        """Papier-Ausstieg (nur wenn PAPER an ist)."""
        if self.closed or not PAPER or self.entry <= 0:
            return None
        r = self.last / self.entry
        if r >= TP:
            return "TP"
        if r <= SL:
            return "SL"
        if PAPER_TRAIL and self.entry > 0:
            hoch = self.peak / self.entry
            if hoch >= PAPER_TRAIL_FROM and r <= hoch * PAPER_TRAIL:
                return "TRAIL"          # FIX18: 10 % unter dem Hoch raus
        if t - self.t0 >= HOLD:
            return "TIME"
        return None

    def close(self, reason):
        self.closed = True
        self.reason = reason        # FIX17: Ausstiegsgrund fuer den Pfad-Datensatz
        # Ehrliche Fuellung: bei einem Ziel-Ausstieg wird zum Ziel verkauft, nicht zum
        # zufaellig naechsten Messpunkt (der bei schnellen Pumpen weit darueber liegt und
        # die Papierbilanz aufgeblaeht hat).
        fill = self.last
        if reason == "TP" and TP and self.entry:
            fill = min(self.last, self.entry * TP)
        ratio = (fill / self.entry) if self.entry else 1.0
        pnl = (ratio - 1.0) * BUY_SOL - BUY_SOL * FEE - GAS
        rec = {
            "ts": round(self.t0), "mint": self.mint, "symbol": self.symbol,
            "group": self.group, "filtered": self.filtered,
            "entry_mcap": round(self.entry, 2), "exit_mcap": round(fill, 2),
            "peak_mcap": round(self.peak, 2), "trough_mcap": round(self.trough, 2),
            "reason": reason, "hold_s": round(now() - self.t0, 1),
            "pnl_sol": round(pnl, 6),
            "reserve_sol": round(getattr(self, "reserve", 0.0), 4),   # FIX16
            "entry_creation": round(getattr(self, "entry0", 0.0), 2),  # FIX19
            "rebased": bool(getattr(self, "rebased", False)),         # FIX19
            # Der volle Kursverlauf steht in paper-paths.jsonl (Fix 21: nicht mehr doppelt
            # hier ablegen - die Datei war dadurch auf >150 MB gewachsen und jede Auswertung
            # brauchte Minuten).
        }
        jline(TRADES, rec)
        return rec


def save_position(live):
    """Offene Live-Position persistieren (ueberlebt Neustart/Disconnect)."""
    try:
        if live.get("mint") and not live.get("closed"):
            json.dump(live, open(LIVESTATE, "w"))
        elif os.path.exists(LIVESTATE):
            os.remove(LIVESTATE)
    except Exception:
        pass


def load_position():
    try:
        p = json.load(open(LIVESTATE))
        if p.get("mint"):
            print(f"  [LIVE] offene Position aus Zustand uebernommen: {p.get('sym')} "
                  f"(Einkauf {p.get('entry'):.1f} MC)")
            return p
    except Exception:
        pass
    return {"done": False, "mint": None, "entry": 0.0, "t0": 0.0, "sym": "",
            "closed": False, "peak": 0.0, "sells": 0}


def read_mcaps(curves):
    """Bonding-Curve-Konten in 100er-Batches lesen → Market Cap in SOL.
    Layout: u64 vTokens@8, u64 vSol@16 (Little Endian)."""
    import base64, struct, urllib.request
    out = {}
    for i in range(0, len(curves), 100):
        part = curves[i:i + 100]
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getMultipleAccounts",
                           "params": [part, {"encoding": "base64"}]}).encode()
        # Endpunkte rotieren: verteilt die Last, der kostenlose RPC wirft sonst 429
        eps = [e for e in (RPC, os.environ.get("RPC_URL_2",
                            "https://solana-rpc.publicnode.com"),
                           os.environ.get("RPC_URL_3",
                            "https://solana.api.onfinality.io/public")) if e]
        start_i = (i // 100) % len(eps)
        res = None
        for ep in eps[start_i:] + eps[:start_i]:
            try:
                res = json.loads(urllib.request.urlopen(
                    urllib.request.Request(ep, body, {"Content-Type": "application/json"}),
                    timeout=20).read())
                break
            except Exception:
                res = None
        if res is None:
            continue
        for curve, acc in zip(part, res.get("result", {}).get("value", []) or []):
            try:
                if not acc:
                    continue
                raw = base64.b64decode(acc["data"][0])
                if len(raw) < 24:
                    continue
                vtok, vsol = struct.unpack_from("<QQ", raw, 8)
                if vtok > 0:
                    out[curve] = (vsol / vtok) * 1e6
            except Exception:
                continue
    return out


async def live_trade(action, mint, sol=None):
    """Asynchroner Subprozess: blockiert den Event-Loop NICHT (WS + Polling laufen weiter).
    Rueckgabe enthaelt 'gesendet' nur, wenn die TX on-chain bestaetigt wurde."""
    cmd = [sys.executable, SNIPE, action, mint] + ([str(sol)] if sol else [])
    if os.environ.get("LIVE_DRY", "0") == "1":
        cmd.insert(2, "--simulate")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        so, se = await asyncio.wait_for(proc.communicate(), timeout=180)
        out = (so.decode().strip() + " | " + se.decode().strip()).strip(" |")
    except Exception as e:
        out = f"Ausfuehrung fehlgeschlagen: {type(e).__name__}"
    print(f"  [LIVE] {action} {mint[:8]}…: {out[:220]}")
    return out


async def ws_messages(t_end):
    """Nachrichten vom PumpPortal-Stream — mit automatischem Reconnect und Backoff."""
    backoff = 3
    while now() < t_end:
        try:
            async with websockets.connect(WS, open_timeout=20, ping_interval=20) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                print(f"[{time.strftime('%H:%M:%S')}] WS verbunden (reconnect-faehig)")
                backoff = 3
                while now() < t_end:
                    try:
                        yield await asyncio.wait_for(ws.recv(), timeout=40)
                    except asyncio.TimeoutError:
                        continue
        except Exception as e:
            print(f"WS getrennt ({type(e).__name__}) → neuer Versuch in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


async def run():
    trackers = {}
    counter = 0
    letzter_versuch = {}      # Mint -> Zeitpunkt des letzten Kaufversuchs (gegen Dauerfeuer)
    stats = {"started": now(), "candidates": 0, "tracked": 0, "closed": 0,
             "wins": 0, "losses": 0, "pnl_sol": 0.0}
    t_end = now() + RUN_S
    live = load_position()
    # FIX15: Offene Position kann laengst weg sein (verkauft/verbrannt/nie erhalten).
    # Haengt sie als Karteileiche im Zustand, blockiert sie ueber 'done' jeden Kauf.
    if live.get("mint") and not live.get("closed"):
        try:
            import importlib.util as _iu0
            _sp0 = _iu0.spec_from_file_location("snipe_exec0", os.path.join(DIR, "solana-snipe.py"))
            _exec0 = _iu0.module_from_spec(_sp0)
            _sp0.loader.exec_module(_exec0)
            _bal0 = _exec0.token_balance(str(_exec0.load_kp().pubkey()), live["mint"])
            if _bal0 <= 0:
                print("  [LIVE] FIX15: Position verworfen - kein Token-Bestand mehr "
                      f"({live.get('sym')} {str(live['mint'])[:8]}) -> Bot wieder frei")
                jline(TRADES, {"ts": round(now()), "mint": live.get("mint"),
                               "symbol": live.get("sym"), "group": "live",
                               "reason": "ABANDONED_STARTUP", "pnl_sol": None})
                live.update(done=False, mint=None, entry=0.0, peak=0.0, sells=0,
                            last_try=0, closed=False)
                save_position(live)
                live = load_position()
            else:
                print(f"  [LIVE] Position vorhanden: {live.get('sym')} "
                      f"{_bal0 / 1e6:.2f} Token -> Ausstiegsueberwachung aktiv")
        except Exception as e:
            print("  [LIVE] WARNUNG: Bestandspruefung fehlgeschlagen:", type(e).__name__, e)

    if live.get("mint") and not live.get("closed"):
        # Nur ein Mint im Zustand reicht nicht: ohne Kurve gibt es keinen Kurs und damit
        # keinen Ausstieg. Kurven-Adresse (PDA) ableiten und Tracker nachziehen.
        try:
            import importlib.util as _iu
            _sp = _iu.spec_from_file_location("snipe_exec", os.path.join(DIR, "solana-snipe.py"))
            _exec = _iu.module_from_spec(_sp)
            _sp.loader.exec_module(_exec)
            from solders.pubkey import Pubkey
            _pda, _ = Pubkey.find_program_address(
                [b"bonding-curve", bytes(Pubkey.from_string(live["mint"]))],
                Pubkey.from_string(_exec.PUMP_PROG))
            _t = Tracker(live["mint"], live.get("sym") or "?", live["entry"], False,
                         "live", curve=str(_pda))
            _t.peak = max(live.get("peak") or 0.0, live["entry"])
            trackers[live["mint"]] = _t
            print(f"  [LIVE] verfolgte Position wiederhergestellt: {live.get('sym')} "
                  f"({str(_pda)[:8]}...) - Ausstiegsregeln aktiv")
        except Exception as e:
            print("  [LIVE] WARNUNG: Kurve nicht ableitbar:", type(e).__name__, e)

    def save_state():
        st = dict(stats)
        st["run_left_s"] = max(0, int(t_end - now()))
        st["open"] = len([t for t in trackers.values() if not t.closed])
        st["live"] = {k: live.get(k) for k in ("mint", "sym", "entry", "peak", "closed")}
        st["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
        json.dump(st, open(STATE, "w"), indent=2)

    async def checker():
        while now() < t_end:
            await asyncio.sleep(CHECK_S)
            for tr in list(trackers.values()):
                if not PAPER and tr.mint != live.get("mint") and (now() - tr.t0) > 900:
                    del trackers[tr.mint]
                    continue
                r = tr.check(now())
                if r and tr.mint != live.get("mint"):
                    rec = tr.close(r)
                    stats["closed"] += 1
                    stats["wins" if rec["pnl_sol"] > 0 else "losses"] += 1
                    stats["pnl_sol"] = round(stats["pnl_sol"] + rec["pnl_sol"], 6)
                    # FIX17: Tracker bleibt bis HOLD+60 s im Speicher - der Kursverlauf
                    # laeuft weiter, damit man spaeter andere Regeln durchrechnen kann.
            # FIX17: Beobachtungsfenster vorbei -> kompletten Kursverlauf ablegen
            for tr in list(trackers.values()):
                if (not tr.out and (now() - tr.t0) >= OBSERVE_S
                        and tr.mint != live.get("mint")):          # FIX18
                    jline(PATHS, {"ts": round(tr.t0), "mint": tr.mint, "symbol": tr.symbol,
                                  "group": tr.group, "entry_mcap": round(tr.entry, 2),
                                  "peak_mcap": round(tr.peak, 2),
                                  "trough_mcap": round(tr.trough, 2),
                                  "reserve_sol": round(getattr(tr, "reserve", 0.0), 4),
                                  "rebased": bool(getattr(tr, "rebased", False)),   # FIX19
                                  "closed_reason": tr.reason if hasattr(tr, "reason") else None,
                                  "path": tr.path})
                    tr.out = True
                    del trackers[tr.mint]

            # Live-Ausstieg: erst verkaufen, Status erst nach Bestaetigung schliessen
            if LIVE and live.get("mint") and not live.get("closed"):
                tr = trackers.get(live["mint"])
                ref = tr.last if tr else None
                if ref and live["entry"] > 0:
                    rr = ref / live["entry"]
                    live["peak"] = max(live.get("peak", 0.0), rr)
                    why = None
                    if rr <= LIVE_SL:
                        why = "SL"
                    elif live["peak"] >= BE_AT:
                        if rr <= BE_FLOOR:
                            why = "BE"
                        elif rr <= live["peak"] * TRAIL:
                            why = "TRAIL"
                    if why is None and LIVE_TP and rr >= LIVE_TP:
                        why = "TP"
                    if why is None and (now() - live["t0"]) >= LIVE_HOLD:
                        why = "TIME"
                    if why and (now() - live.get("last_try", 0)) >= 300:
                        live["last_try"] = now()
                        out = await live_trade("sell", live["mint"])
                        if "ERGEBNIS: OK" in (out or ""):
                            _m, _s = live.get("mint"), live.get("sym")
                            print(f"  [LIVE-EXIT] {why} bei {rr:.2f}x "
                                  f"(Hoch {live['peak']:.2f}x) verkauft OK")
                            jline(TRADES, {"ts": round(now()), "mint": _m, "symbol": _s,
                                           "group": "live", "reason": why,
                                           "ratio": round(rr, 3), "pnl_sol": None})
                            # Position zu -> Bot wieder frei fuer den naechsten Kauf
                            # (sonst blieb 'done' True und es kam nie wieder ein Kauf).
                            live.update(done=False, mint=None, entry=0.0, peak=0.0,
                                        sells=0, last_try=0, closed=False)
                            save_position(live)
                        else:
                            # NICHT schliessen: sonst steht Geld ohne Ausstieg fest
                            # (Vorfall 24.09.2026). Wiederholung alle 5 Min, kein Neukauf.
                            live["sells"] = live.get("sells", 0) + 1
                            print(f"  [LIVE-EXIT] {why} bei {rr:.2f}x — Verkauf noch nicht "
                                  f"bestaetigt (Versuch {live['sells']}, neuer Versuch in 5 Min)")
                            if live["sells"] == 3:
                                jline(TRADES, {"ts": round(now()), "mint": live["mint"],
                                               "symbol": live["sym"], "group": "live",
                                               "reason": "SELL_FAILED",
                                               "pnl_sol": None})
                                print("  [LIVE] Ausstieg haengt -> Position bleibt offen "
                                      "(Wiederholung alle 5 Min, kein Neukauf)")
                            # FIX15: Bestand weg oder dauerhaft erfolglos -> Position aufgeben,
                            # sonst blockiert sie ueber 'done' jeden weiteren Kauf.
                            if "kein Token-Bestand" in (out or "") or live.get("sells", 0) >= 10:
                                print(f"  [LIVE-EXIT] FIX15: Position aufgegeben ({why} bei "
                                      f"{rr:.2f}x, {live.get('sells')} Versuche ohne Bestand) "
                                      f"-> Bot wieder frei")
                                jline(TRADES, {"ts": round(now()), "mint": live.get("mint"),
                                               "symbol": live.get("sym"), "group": "live",
                                               "reason": "ABANDONED", "ratio": round(rr, 3),
                                               "pnl_sol": None})
                                live.update(done=False, mint=None, entry=0.0, peak=0.0,
                                            sells=0, last_try=0, closed=False)
                                save_position(live)
                save_position(live)
            save_state()

    async def poller():
        loop = asyncio.get_event_loop()
        while now() < t_end:
            await asyncio.sleep(POLL_S)
            # FIX17: nach dem Ausstieg noch bis HOLD weiter messen
            cur = [t.curve for t in trackers.values() if t.curve
                   and (not t.closed or (now() - t.t0) < OBSERVE_S)]   # FIX18
            if cur:
                try:
                    mc = await loop.run_in_executor(None, read_mcaps, cur)
                except Exception as e:
                    print("  (RPC-Poll-Fehler:", type(e).__name__, ")")
                    mc = {}
                for t in list(trackers.values()):
                    if t.curve in mc:
                        t.on_trade(mc[t.curve])
                        if (LIVE and not live["done"] and not t.closed and t.entry > 0
                                and t.last / t.entry >= TRIGGER_X
                                and (now() - t.t0) <= TRIGGER_WIN
                                and (now() - letzter_versuch.get(t.mint, 0)) >= 120):
                            live.update(done=True, mint=t.mint, entry=t.last, t0=now(),
                                        sym=t.symbol, closed=False, peak=0.0, sells=0,
                                        last_try=0)
                            letzter_versuch[t.mint] = now()
                            save_position(live)
                            print(f"  [LIVE-TRIGGER] {t.symbol} {t.entry:.1f} → {t.last:.1f} MC "
                                  f"({t.last/t.entry:.2f}x) → Kauf {LIVE_SOL} SOL")
                            out = await live_trade("buy", t.mint, LIVE_SOL)
                            if "ERGEBNIS: OK" not in (out or ""):
                                live.update(done=False, mint=None, entry=0.0)
                                print("  [LIVE] Kauf nicht bestaetigt → Trigger bleibt scharf")
                            save_position(live)

    chk = asyncio.create_task(checker())
    pol = asyncio.create_task(poller())
    print(f"Start · Trigger {TRIGGER_X}x in {TRIGGER_WIN}s · Einsatz {LIVE_SOL} SOL · "
          f"Stop {LIVE_SL} · Break-even {BE_AT} · Trailing {TRAIL} · Zeit {LIVE_HOLD}s · "
          f"Takt {POLL_S}s/{CHECK_S}s · Papier={PAPER}")

    async for msg in ws_messages(t_end):
        try:
            d = json.loads(msg)
        except Exception:
            continue
        mint = d.get("mint")
        if not mint:
            continue
        tx = d.get("txType")
        if tx and tx != "create":
            tr = trackers.get(mint)
            if tr:
                tr.on_trade(float(d.get("marketCapSol") or 0))
            continue
        pool = (d.get("pool") or "").lower()
        if pool != "pump" and not mint.endswith("pump"):
            continue
        if mint in ("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                    "So11111111111111111111111111111111111111112"):
            continue
        counter += 1
        mc = float(d.get("marketCapSol") or 0)
        sym = (d.get("symbol") or "?")[:24]
        stats["candidates"] += 1
        filtered = MIN_MCAP <= mc <= MAX_MCAP
        control = (counter % SAMPLE_EVERY == 0)
        if not (filtered or control) or len(trackers) >= MAX_TRACK:
            continue
        group = "filter" if filtered else "control"
        jline(CANDS, {"ts": round(now()), "mint": mint, "symbol": sym,
                      "name": (d.get("name") or "")[:40], "mcap": round(mc, 2), "group": group})
        trackers[mint] = Tracker(mint, sym, mc, filtered, group, d.get("bondingCurveKey"),
                                 reserve=max(0.0, float(d.get("vSolInBondingCurve") or 0) - 30.0))  # echte Reserve (Kurve startet bei 30 SOL virtuell)  # FIX16
        stats["tracked"] += 1

    chk.cancel()
    pol.cancel()
    save_state()
    save_position(live)


def summary():
    rows = []
    if os.path.exists(TRADES):
        for line in open(TRADES):
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    out = {"trades": len(rows)}
    if not rows:
        print(json.dumps(out))
        return

    def agg(rs):
        if not rs:
            return {}
        pnl = [r["pnl_sol"] for r in rs if isinstance(r.get("pnl_sol"), (int, float))]
        if not pnl:
            return {}
        return {"n": len(pnl),
                "winrate_%": round(100 * len([p for p in pnl if p > 0]) / len(pnl), 1),
                "pnl_sol": round(sum(pnl), 4),
                "avg_sol": round(sum(pnl) / len(pnl), 4),
                "best": round(max(pnl), 4), "worst": round(min(pnl), 4)}

    out["filter"] = agg([r for r in rows if r.get("group") == "filter"])
    out["control"] = agg([r for r in rows if r.get("group") == "control"])
    out["live"] = agg([r for r in rows if r.get("group") == "live"])
    out["exit_reasons"] = {k: len([r for r in rows if r.get("reason") == k])
                           for k in ("TP", "SL", "TIME", "SELL_FAILED")}
    for x in (1.5, 2.0, 3.0, 5.0):
        out[f"peak_reached_{x}x"] = len([r for r in rows
                                         if r.get("peak_mcap", 0) >= r.get("entry_mcap", 1) * x])
    top = sorted(rows, key=lambda r: r.get("peak_mcap", 0) / max(r.get("entry_mcap", 1e-9), 1e-9),
                 reverse=True)[:5]
    out["top_movers"] = [{"symbol": r.get("symbol"),
                          "x": round(r.get("peak_mcap", 0) / max(r.get("entry_mcap", 1e-9), 1e-9), 2),
                          "exit": r.get("reason"), "pnl_sol": r.get("pnl_sol")} for r in top]
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--summary":
        summary()
    else:
        try:
            asyncio.run(run())
        except KeyboardInterrupt:
            pass
