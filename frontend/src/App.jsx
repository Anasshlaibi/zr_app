import { useEffect, useRef, useState, useCallback } from "react";
import JSMpeg from "jsmpeg";

const BASE = () => `http://${window.location.hostname}:8000`;
const WS_VIDEO = () => `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.hostname}:8000/ws/video`;
const WS_EVENTS = () => `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.hostname}:8000/ws/events`;

// ─── Property Selector Modal ──────────────────────────────────────────────────
function PropModal({ prop, label, current, onClose, onSet }) {
  const [choices, setChoices] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(current);

  useEffect(() => {
    fetch(`${BASE()}/api/camera/choices/${prop}`)
      .then(r => r.json())
      .then(d => { setChoices(d.choices || []); setLoading(false); });
  }, [prop]);

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/80 backdrop-blur-sm p-4">
      <div className="w-full max-w-sm rounded-2xl border border-zinc-700 bg-zinc-900 overflow-hidden shadow-2xl">
        <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-800">
          <span className="text-xs font-bold tracking-widest text-zinc-400">{label}</span>
          <button onClick={onClose} className="text-zinc-500 hover:text-white text-xl leading-none">×</button>
        </div>
        <div className="max-h-72 overflow-y-auto">
          {loading ? (
            <div className="py-8 text-center text-zinc-500 text-sm animate-pulse">Loading options...</div>
          ) : choices.length === 0 ? (
            <div className="py-8 text-center text-zinc-600 text-sm">No options available</div>
          ) : choices.map(c => (
            <button
              key={c}
              onClick={() => setSelected(c)}
              className={`w-full px-5 py-3 text-left text-sm transition border-b border-zinc-800/50 last:border-0
                ${selected === c
                  ? "bg-emerald-600/20 text-emerald-400 font-semibold"
                  : "text-zinc-300 hover:bg-zinc-800"}`}
            >
              {c}
              {selected === c && <span className="float-right">✓</span>}
            </button>
          ))}
        </div>
        <div className="flex gap-2 p-4 border-t border-zinc-800">
          <button onClick={onClose} className="flex-1 rounded-lg py-2.5 text-sm border border-zinc-700 text-zinc-400 hover:bg-zinc-800">
            Cancel
          </button>
          <button
            onClick={() => { onSet(prop, selected); onClose(); }}
            className="flex-1 rounded-lg py-2.5 text-sm bg-emerald-600 text-white font-bold hover:bg-emerald-500"
          >
            Apply
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Camera Property Button ───────────────────────────────────────────────────
function PropButton({ prop, label, value, connected, onOpen }) {
  return (
    <button
      onClick={() => connected && onOpen(prop, label)}
      disabled={!connected}
      className="flex flex-col items-center justify-center rounded-xl border border-zinc-700/80 bg-zinc-800/80 py-3 px-2 gap-0.5
        active:bg-zinc-700 disabled:opacity-40 transition hover:border-zinc-600 hover:bg-zinc-800 min-w-0"
    >
      <span className="text-[9px] font-bold tracking-widest text-zinc-500 uppercase">{label}</span>
      <span className="text-sm font-bold text-zinc-100 truncate w-full text-center mt-0.5">
        {value ?? "—"}
      </span>
    </button>
  );
}

// ─── Connection Screen ────────────────────────────────────────────────────────
function ConnectionScreen({ onConnected }) {
  const [mode, setMode] = useState("auto");  // auto | usb | wifi
  const [ip, setIp] = useState("192.168.1.50");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [phase, setPhase] = useState("");    // scanning | connecting | ""

  // Auto-discover on mount
  useEffect(() => {
    if (mode === "auto") autoConnect();
  }, []);

  const autoConnect = async () => {
    setBusy(true); setError(""); setPhase("scanning");
    // First try USB
    try {
      setPhase("Trying USB-C...");
      const r = await fetch(`${BASE()}/api/connect/usb`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      if (r.ok) { const d = await r.json(); onConnected(d); return; }
    } catch (_) {}

    // Then try Wi-Fi discovery
    try {
      setPhase("Scanning Wi-Fi...");
      const r = await fetch(`${BASE()}/api/discover`);
      if (r.ok) {
        const { ip_address } = await r.json();
        setIp(ip_address);
        setPhase(`Connecting to ${ip_address}...`);
        const cr = await fetch(`${BASE()}/api/connect/wifi`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ip_address })
        });
        if (cr.ok) { const d = await cr.json(); onConnected(d); return; }
      }
    } catch (_) {}

    setError("No camera found automatically. Try USB-C or enter IP manually.");
    setPhase(""); setBusy(false);
  };

  const connectUsb = async () => {
    setBusy(true); setError(""); setPhase("Connecting USB-C...");
    try {
      const r = await fetch(`${BASE()}/api/connect/usb`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail);
      onConnected(d);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); setPhase(""); }
  };

  const connectWifi = async () => {
    setBusy(true); setError(""); setPhase(`Connecting to ${ip}...`);
    try {
      const r = await fetch(`${BASE()}/api/connect/wifi`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ip_address: ip })
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail);
      onConnected(d);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); setPhase(""); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-zinc-950 p-4">
      <div className="w-full max-w-xs">
        {/* Logo / title */}
        <div className="text-center mb-8">
          <div className="text-2xl font-black tracking-widest text-white">ZINECONTROL</div>
          <div className="text-xs text-zinc-500 tracking-widest mt-1">NIKON Z fc · WEB MONITOR</div>
        </div>

        {/* Mode tabs */}
        <div className="flex rounded-xl bg-zinc-900 p-1 mb-5 border border-zinc-800">
          {[["auto", "AUTO"], ["usb", "USB-C"], ["wifi", "WI-FI"]].map(([m, l]) => (
            <button key={m} onClick={() => { setMode(m); setError(""); }}
              className={`flex-1 rounded-lg py-2 text-xs font-bold tracking-wide transition
                ${mode === m ? "bg-zinc-700 text-white" : "text-zinc-500 hover:text-zinc-300"}`}>
              {l}
            </button>
          ))}
        </div>

        {/* Mode content */}
        {mode === "auto" && (
          <div className="text-center py-4">
            {busy ? (
              <>
                <div className="inline-block w-8 h-8 rounded-full border-2 border-emerald-500 border-t-transparent animate-spin mb-4" />
                <div className="text-sm text-zinc-400 animate-pulse">{phase}</div>
              </>
            ) : (
              <button onClick={autoConnect}
                className="w-full rounded-xl bg-emerald-600 py-3.5 text-sm font-bold text-white hover:bg-emerald-500 transition">
                SCAN & CONNECT
              </button>
            )}
          </div>
        )}

        {mode === "usb" && (
          <div className="space-y-3">
            <div className="rounded-xl bg-zinc-900 border border-zinc-800 p-4 text-xs text-zinc-400 space-y-1">
              <div className="font-bold text-zinc-300 mb-2">Before connecting:</div>
              <div>1. Camera: Menu → Setup → USB → <span className="text-emerald-400">PTP</span></div>
              <div>2. Plug in USB-C cable</div>
              <div>3. Mac/Linux: ready. Windows: install <span className="text-emerald-400">Zadig WinUSB</span></div>
            </div>
            <button onClick={connectUsb} disabled={busy}
              className="w-full rounded-xl bg-emerald-600 py-3.5 text-sm font-bold text-white hover:bg-emerald-500 disabled:opacity-40 transition">
              {busy ? phase : "CONNECT USB-C"}
            </button>
          </div>
        )}

        {mode === "wifi" && (
          <div className="space-y-3">
            <div>
              <label className="text-xs font-bold tracking-widest text-zinc-500 mb-2 block">CAMERA IP</label>
              <input value={ip} onChange={e => setIp(e.target.value)}
                className="w-full rounded-xl border border-zinc-700 bg-zinc-900 px-4 py-3 text-sm text-white focus:border-emerald-500 focus:outline-none"
                placeholder="192.168.1.x" />
            </div>
            <button onClick={connectWifi} disabled={busy}
              className="w-full rounded-xl bg-emerald-600 py-3.5 text-sm font-bold text-white hover:bg-emerald-500 disabled:opacity-40 transition">
              {busy ? phase : "CONNECT WI-FI"}
            </button>
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="mt-4 rounded-xl border border-red-900/50 bg-red-900/20 p-3 text-xs text-red-400">
            {error}
          </div>
        )}
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════════════════
// MAIN APP
// ═══════════════════════════════════════════════════════════════════════════════
export default function App() {
  const canvasRef = useRef(null);
  const [streamStatus, setStreamStatus] = useState("CONNECTING");
  const [camConnected, setCamConnected] = useState(false);
  const [connType, setConnType] = useState(null);
  const [props, setProps] = useState({});           // live camera properties
  const [rec, setRec] = useState(false);
  const [modal, setModal] = useState(null);          // { prop, label } | null
  const [notification, setNotification] = useState(null);

  // ── Hotplug WebSocket ───────────────────────────────────────────────────────
  useEffect(() => {
    let ws;
    const connect = () => {
      ws = new WebSocket(WS_EVENTS());
      ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.event === "connected") {
          setCamConnected(true);
          setConnType(msg.data);
          showNotif(`📷 Camera connected via ${msg.data}`);
        } else if (msg.event === "disconnected") {
          setCamConnected(false);
          setConnType(null);
          setProps({});
          showNotif("⚠️ Camera disconnected");
        } else if (msg.event === "status") {
          if (msg.connected) { setCamConnected(true); setConnType(msg.type); }
          if (msg.props) setProps(msg.props);
        } else if (msg.event === "error") {
          showNotif(`Error: ${msg.data}`, "error");
        }
      };
      ws.onclose = () => setTimeout(connect, 2000);
    };
    connect();
    return () => ws?.close();
  }, []);

  // ── JSMpeg video player ─────────────────────────────────────────────────────
  useEffect(() => {
    let player, retry;
    const start = () => {
      if (!canvasRef.current) return;
      setStreamStatus("CONNECTING");
      try {
        player = new JSMpeg(WS_VIDEO(), {
          canvas: canvasRef.current, autoplay: true, audio: false,
          onSourceEstablished: () => setStreamStatus("LIVE"),
          onStalled:           () => setStreamStatus("RECONNECTING"),
          onSourceCompleted:   () => { setStreamStatus("RECONNECTING"); retry = setTimeout(start, 800); },
        });
      } catch { setStreamStatus("ERROR"); }
    };
    start();
    return () => { clearTimeout(retry); player?.destroy?.(); };
  }, []);

  // ── Helpers ─────────────────────────────────────────────────────────────────
  const showNotif = (msg, type = "info") => {
    setNotification({ msg, type });
    setTimeout(() => setNotification(null), 3500);
  };

  const onConnected = (data) => {
    setCamConnected(true);
    setConnType(data.type);
    if (data.props) setProps(data.props);
  };

  const setProp = async (prop, value) => {
    try {
      const r = await fetch(`${BASE()}/api/camera/prop/${prop}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value })
      });
      if (!r.ok) throw new Error((await r.json()).detail);
      setProps(p => ({ ...p, [prop]: value }));
      showNotif(`${prop.toUpperCase()} → ${value}`);
    } catch (e) { showNotif(e.message, "error"); }
  };

  const triggerCapture = async () => {
    try {
      await fetch(`${BASE()}/api/camera/capture`, { method: "POST" });
      showNotif("📸 Captured");
    } catch (e) { showNotif("Capture failed", "error"); }
  };

  const toggleRec = async () => {
    const next = !rec;
    setRec(next);
    showNotif(next ? "● Recording started" : "■ Recording stopped");
  };

  const refreshProps = async () => {
    if (!camConnected) return;
    const r = await fetch(`${BASE()}/api/camera/props`);
    if (r.ok) setProps(await r.json());
  };

  useEffect(() => {
    if (camConnected) {
      refreshProps();
      const t = setInterval(refreshProps, 5000);  // poll every 5s
      return () => clearInterval(t);
    }
  }, [camConnected]);

  // ── Render ──────────────────────────────────────────────────────────────────
  return (
    <main className="relative h-screen w-screen overflow-hidden bg-zinc-950 text-zinc-100 font-sans select-none">

      {/* Connection screen overlay */}
      {!camConnected && <ConnectionScreen onConnected={onConnected} />}

      {/* Property modal */}
      {modal && (
        <PropModal
          prop={modal.prop}
          label={modal.label}
          current={props[modal.prop]}
          onClose={() => setModal(null)}
          onSet={setProp}
        />
      )}

      {/* Toast notification */}
      {notification && (
        <div className={`absolute top-4 left-1/2 -translate-x-1/2 z-40 px-4 py-2.5 rounded-full text-xs font-bold shadow-xl border transition-all
          ${notification.type === "error"
            ? "bg-red-900 border-red-700 text-red-200"
            : "bg-zinc-800 border-zinc-700 text-zinc-100"}`}>
          {notification.msg}
        </div>
      )}

      {/* Main layout */}
      <div className="flex flex-col h-full p-2 gap-2 max-w-screen-xl mx-auto">

        {/* Header */}
        <header className="flex items-center justify-between rounded-xl border border-zinc-800 bg-zinc-900 px-4 py-2.5">
          <div className="flex items-center gap-3">
            <span className="text-xs font-black tracking-widest text-white">ZINECONTROL</span>
            {connType && (
              <span className="text-[10px] bg-zinc-800 border border-zinc-700 rounded-full px-2.5 py-0.5 text-zinc-400 font-bold tracking-wide">
                {connType}
              </span>
            )}
          </div>
          <div className="flex items-center gap-3">
            <button onClick={refreshProps} disabled={!camConnected}
              className="text-zinc-500 hover:text-zinc-200 text-xs font-bold tracking-wide disabled:opacity-30 transition">
              ↻ SYNC
            </button>
            <div className="flex items-center gap-1.5">
              <span className={`h-2 w-2 rounded-full ${streamStatus === "LIVE" ? "bg-emerald-500 animate-pulse" : "bg-red-600"}`} />
              <span className="text-[10px] font-bold tracking-widest text-zinc-400">{streamStatus}</span>
            </div>
          </div>
        </header>

        {/* Live view canvas */}
        <div className="flex-1 min-h-0 relative rounded-xl overflow-hidden border border-zinc-800 bg-black">
          <canvas
            ref={canvasRef}
            width={1280} height={720}
            className="w-full h-full object-contain"
          />
          {/* Overlay: stream status when not live */}
          {streamStatus !== "LIVE" && camConnected && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/60 backdrop-blur-sm">
              <div className="text-zinc-400 text-sm font-bold tracking-widest animate-pulse">{streamStatus}...</div>
            </div>
          )}
          {/* Recording badge */}
          {rec && (
            <div className="absolute top-3 left-3 flex items-center gap-1.5 bg-red-600 rounded-full px-3 py-1 shadow-lg">
              <span className="w-2 h-2 rounded-full bg-white animate-pulse" />
              <span className="text-[10px] font-black text-white tracking-widest">REC</span>
            </div>
          )}
        </div>

        {/* Controls footer */}
        <footer className="rounded-xl border border-zinc-800 bg-zinc-900 p-2">
          {/* Camera property buttons */}
          <div className="grid grid-cols-4 gap-2 mb-2">
            <PropButton prop="iso"           label="ISO"     value={props.iso}           connected={camConnected} onOpen={(p,l) => setModal({ prop:p, label:l })} />
            <PropButton prop="shutterspeed"  label="SHUTTER" value={props.shutterspeed}  connected={camConnected} onOpen={(p,l) => setModal({ prop:p, label:l })} />
            <PropButton prop="aperture"      label="IRIS"    value={props.aperture}      connected={camConnected} onOpen={(p,l) => setModal({ prop:p, label:l })} />
            <PropButton prop="whitebalance"  label="WB"      value={props.whitebalance}  connected={camConnected} onOpen={(p,l) => setModal({ prop:p, label:l })} />
          </div>

          {/* Action buttons */}
          <div className="grid grid-cols-3 gap-2">
            <button onClick={triggerCapture} disabled={!camConnected}
              className="rounded-xl border border-zinc-700 bg-zinc-800 py-3 text-xs font-bold tracking-widest text-zinc-200
                active:bg-zinc-700 disabled:opacity-30 transition hover:border-zinc-500">
              📸 CAPTURE
            </button>
            <button onClick={toggleRec} disabled={!camConnected}
              className={`rounded-xl py-3 text-xs font-black tracking-widest text-white transition disabled:opacity-30
                ${rec ? "bg-red-600 hover:bg-red-500" : "bg-red-900/70 border border-red-800 hover:bg-red-800"}`}>
              {rec ? "■ STOP REC" : "● START REC"}
            </button>
            <button onClick={() => { setCamConnected(false); setProps({}); setConnType(null); fetch(`${BASE()}/api/disconnect`, { method: "POST" }); }}
              className="rounded-xl border border-zinc-700 bg-zinc-800 py-3 text-xs font-bold tracking-widest text-zinc-400
                active:bg-zinc-700 transition hover:border-zinc-500 hover:text-zinc-200">
              ⏏ DISCONNECT
            </button>
          </div>
        </footer>
      </div>
    </main>
  );
}
