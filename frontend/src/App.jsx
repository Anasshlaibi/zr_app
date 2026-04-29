import { useEffect, useRef, useState } from "react";
import JSMpeg from "jsmpeg";

export default function App() {
  const canvasRef = useRef(null);
  const [props, setProps] = useState({});
  const [connected, setConnected] = useState(false);
  const [streamStatus, setStreamStatus] = useState("DISCONNECTED");

  const syncProps = async () => {
    const res = await fetch(`http://${window.location.hostname}:8000/api/camera/props`);
    if (res.ok) setProps(await res.json());
  };

  useEffect(() => {
    if (connected) {
      const player = new JSMpeg.Player(`ws://${window.location.hostname}:8000/ws/video`, {
        canvas: canvasRef.current,
        onSourceEstablished: () => setStreamStatus("LIVE")
      });
      syncProps();
      return () => player.destroy();
    }
  }, [connected]);

  return (
    <main className="h-screen w-screen bg-zinc-950 text-zinc-100 p-4">
      <header className="flex justify-between items-center mb-4 border border-zinc-800 bg-zinc-900 p-3 rounded-xl">
        <h1 className="font-black tracking-tighter">ZINECONTROL PRO</h1>
        <div className="flex gap-2 items-center">
          <span className={`h-2 w-2 rounded-full ${streamStatus === "LIVE" ? "bg-emerald-500" : "bg-red-500"}`} />
          <span className="text-xs font-bold text-zinc-400">{streamStatus}</span>
        </div>
      </header>

      <div className="aspect-video bg-black rounded-2xl overflow-hidden border border-zinc-800 mb-4">
        <canvas ref={canvasRef} className="w-full h-full object-contain" />
      </div>

      <footer className="grid grid-cols-4 gap-2">
        {["iso", "shutterspeed", "aperture", "whitebalance"].map(p => (
          <button key={p} className="bg-zinc-900 border border-zinc-800 p-3 rounded-xl active:bg-zinc-800">
            <div className="text-[10px] text-zinc-500 font-bold uppercase">{p}</div>
            <div className="text-sm font-black">{props[p] || "—"}</div>
          </button>
        ))}
      </footer>
    </main>
  );
}
