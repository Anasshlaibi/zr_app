import { useEffect, useRef, useState } from "react";
import JSMpeg from "jsmpeg";

const API = "http://localhost:8000";
const WS_VIDEO = "ws://localhost:8000/ws/video";
const WS_EVENTS = "ws://localhost:8000/ws/events";

function PropModal({ prop, label, current, choices, onClose, onSet }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/90 p-4">
      <div className="w-full max-w-xs bg-zinc-900 border border-zinc-800 rounded-3xl overflow-hidden shadow-2xl">
        <div className="p-6 border-b border-zinc-800 flex justify-between items-center">
          <span className="text-xs font-black tracking-tighter text-zinc-500 uppercase">{label}</span>
          <button onClick={onClose} className="text-zinc-500 hover:text-white">✕</button>
        </div>
        <div className="max-h-64 overflow-y-auto">
          {choices.map(c => (
            <button key={c} onClick={() => { onSet(prop, c); onClose(); }}
              className={`w-full p-4 text-left text-sm border-b border-zinc-800/50 hover:bg-zinc-800 transition
                ${current === c ? "text-emerald-400 bg-emerald-400/5" : "text-zinc-300"}`}>
              {c}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function ConnectionScreen({ onConnect }) {
  const [mode, setMode] = useState("USB");
  const [ip, setIp] = useState("");
  const [loading, setLoading] = useState(false);

  const handleConnect = async () => {
    setLoading(true);
    try {
      if (mode === "USB") {
        await fetch(`${API}/api/connect/usb`, { method: "POST" });
      } else if (mode === "AUTO") {
        const res = await fetch(`${API}/api/discover`);
        const { ip } = await res.json();
        // logic to connect via wifi with discovered ip
      }
    } catch (e) { console.error(e); }
    setLoading(false);
  };

  return (
    <div className="fixed inset-0 z-40 bg-black flex flex-col items-center justify-center p-6 text-center">
      <h1 className="text-3xl font-black tracking-tighter text-white mb-2">ZINECONTROL</h1>
      <p className="text-zinc-500 text-xs tracking-widest uppercase mb-12">Nikon Pro Monitor</p>
      
      <div className="flex gap-2 mb-8 bg-zinc-900 p-1 rounded-2xl">
        {["AUTO", "USB", "WI-FI"].map(m => (
          <button key={m} onClick={() => setMode(m)}
            className={`px-6 py-2 rounded-xl text-[10px] font-bold transition
              ${mode === m ? "bg-white text-black" : "text-zinc-500 hover:text-white"}`}>
            {m}
          </button>
        ))}
      </div>

      <button onClick={handleConnect} disabled={loading}
        className="w-full max-w-xs bg-emerald-600 hover:bg-emerald-500 text-white font-black py-4 rounded-2xl transition disabled:opacity-50">
        {loading ? "CONNECTING..." : "INITIALIZE"}
      </button>
    </div>
  );
}

export default function App() {
  const canvasRef = useRef(null);
  const [connected, setConnected] = useState(false);
  const [props, setProps] = useState({});
  const [modal, setModal] = useState(null); // { prop, label, choices }

  useEffect(() => {
    const ws = new WebSocket(WS_EVENTS);
    ws.onmessage = (e) => {
      const data = JSON.parse(e.data);
      if (data.event === "status") {
        setConnected(data.connected);
        setProps(data.props);
      }
    };
    return () => ws.close();
  }, []);

  useEffect(() => {
    if (!connected) return;
    const player = new JSMpeg(WS_VIDEO, { canvas: canvasRef.current, audio: false });
    return () => player.destroy();
  }, [connected]);

  const sync = async () => {
    const res = await fetch(`${API}/api/camera/props`);
    setProps(await res.json());
  };

  const openProp = async (prop, label) => {
    const res = await fetch(`${API}/api/camera/choices/${prop}`);
    const { choices } = await res.json();
    setModal({ prop, label, choices });
  };

  const updateProp = async (prop, value) => {
    await fetch(`${API}/api/camera/prop/${prop}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value })
    });
    setProps(p => ({ ...p, [prop]: value }));
  };

  return (
    <div className="h-screen w-screen bg-black text-white overflow-hidden flex flex-col">
      {!connected && <ConnectionScreen />}
      {modal && <PropModal {...modal} current={props[modal.prop]} onClose={() => setModal(null)} onSet={updateProp} />}

      <header className="p-4 flex justify-between items-center border-b border-zinc-900">
        <div className="text-sm font-black tracking-tighter">ZINECONTROL</div>
        <button onClick={sync} className="text-[10px] font-bold text-zinc-500 hover:text-white">↻ SYNC</button>
      </header>

      <div className="flex-1 bg-zinc-950 flex items-center justify-center p-4">
        <canvas ref={canvasRef} className="w-full h-full object-contain rounded-2xl border border-zinc-900 shadow-2xl" />
      </div>

      <footer className="p-4 grid grid-cols-4 gap-2 bg-zinc-900/50 backdrop-blur-xl border-t border-zinc-900">
        {[["iso", "ISO"], ["shutterspeed", "SHUTTER"], ["aperture", "IRIS"], ["whitebalance", "WB"]].map(([k, l]) => (
          <button key={k} onClick={() => openProp(k, l)}
            className="flex flex-col items-center justify-center bg-zinc-800/80 p-3 rounded-2xl border border-zinc-700/50 hover:bg-zinc-700 transition">
            <span className="text-[8px] font-black text-zinc-500 uppercase tracking-widest">{l}</span>
            <span className="text-xs font-bold mt-1">{props[k] || "—"}</span>
          </button>
        ))}
      </footer>
    </div>
  );
}
