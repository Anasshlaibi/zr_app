import { useEffect, useRef, useState } from "react";

export default function App() {
  const canvasRef = useRef(null);
  const [status, setStatus] = useState("CONNECTING");
  const [rec, setRec] = useState(false);

  // Helper function to hit the FastAPI backend
  const sendCommand = async (endpoint, payload = {}) => {
    try {
      const url = `http://${window.location.hostname}:8000/api/camera/${endpoint}`;
      await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } catch (err) {
      console.error(`Failed to send ${endpoint} command`, err);
    }
  };

  const handleRecordToggle = () => {
    const newRecState = !rec;
    setRec(newRecState);
    sendCommand("record", { value: newRecState ? "start" : "stop" });
  };

  useEffect(() => {
    let isMounted = true;
    let player = null;
    let retryTimer = null;

    const connectPlayer = () => {
      const wsProtocol = window.location.protocol === "https:" ? "wss" : "ws";
      const wsUrl = `${wsProtocol}://${window.location.hostname}:8000/ws/video`;
      const JSMpeg = window.JSMpeg;
      const canvas = canvasRef.current;

      if (!isMounted || !JSMpeg || !canvas) return;

      setStatus("CONNECTING");
      player = new JSMpeg.Player(wsUrl, {
        canvas,
        autoplay: true,
        audio: false,
        disableGl: false,
        preserveDrawingBuffer: true,
        onSourceEstablished: () => isMounted && setStatus("LIVE"),
        onStalled: () => isMounted && setStatus("RECONNECTING"),
        onSourceCompleted: () => {
          if (!isMounted) return;
          setStatus("RECONNECTING");
          retryTimer = setTimeout(connectPlayer, 800);
        },
      });
    };

    const ensureJSMpeg = () => {
      if (window.JSMpeg) {
        connectPlayer();
        return;
      }
      const script = document.createElement("script");
      script.src = "https://cdn.jsdelivr.net/npm/jsmpeg@0.2.1/jsmpeg.min.js";
      script.async = true;
      script.onload = () => isMounted && connectPlayer();
      script.onerror = () => isMounted && setStatus("ERROR");
      document.body.appendChild(script);
    };

    ensureJSMpeg();

    return () => {
      isMounted = false;
      if (retryTimer) clearTimeout(retryTimer);
      if (player && typeof player.destroy === "function") player.destroy();
    };
  }, []);

  return (
    <main className="h-screen w-screen overflow-hidden bg-zinc-950 text-zinc-100">
      <section className="mx-auto flex h-full w-full max-w-[1024px] flex-col justify-between p-2">
        <header className="flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2">
          <div className="text-xs tracking-[0.18em] text-zinc-500">ZINECONTROL WEB</div>
          <div className="flex items-center gap-2">
            <span
              className={`h-2.5 w-2.5 rounded-full ${
                status === "LIVE"
                  ? "bg-emerald-500"
                  : status === "CONNECTING" || status === "RECONNECTING"
                    ? "bg-amber-500"
                    : "bg-red-600"
              }`}
            />
            <span className="text-[11px] font-medium tracking-wide text-zinc-300">{status}</span>
          </div>
        </header>

        <div className="my-2 flex min-h-0 flex-1 items-center justify-center">
          <div className="relative w-full overflow-hidden rounded-lg border border-zinc-800 bg-black shadow-[0_20px_60px_rgba(0,0,0,0.5)]">
            <div className="aspect-video w-full">
              <canvas ref={canvasRef} width={1280} height={720} className="h-full w-full object-cover" />
            </div>
            <div className="pointer-events-none absolute left-3 top-3 rounded border border-zinc-700 bg-zinc-950/70 px-2 py-1 text-[10px] font-medium tracking-[0.14em] text-zinc-300">
              ZR LIVE MONITOR
            </div>
          </div>
        </div>

        <footer className="grid grid-cols-4 gap-2 rounded-lg border border-zinc-800 bg-zinc-900 p-2">
          <button 
            onClick={() => sendCommand("iso", { value: "800" })}
            className="rounded-md border border-zinc-700 bg-zinc-800 px-2 py-3 text-sm font-semibold text-zinc-200 active:scale-[0.98] active:bg-zinc-700 transition"
          >
            ISO (800)
          </button>
          <button 
            onClick={() => sendCommand("shutter", { value: "1/50" })}
            className="rounded-md border border-zinc-700 bg-zinc-800 px-2 py-3 text-sm font-semibold text-zinc-200 active:scale-[0.98] active:bg-zinc-700 transition"
          >
            Shutter
          </button>
          <button 
            onClick={() => sendCommand("aperture", { value: "2.8" })}
            className="rounded-md border border-zinc-700 bg-zinc-800 px-2 py-3 text-sm font-semibold text-zinc-200 active:scale-[0.98] active:bg-zinc-700 transition"
          >
            Aperture
          </button>
          <button
            onClick={handleRecordToggle}
            className={`rounded-md px-2 py-3 text-sm font-extrabold tracking-wide text-white transition active:scale-[0.98] ${
              rec
                ? "border border-red-300 bg-red-600 shadow-[0_0_18px_rgba(220,38,38,0.45)]"
                : "border border-red-900 bg-red-700/90"
            }`}
          >
            {rec ? "REC ●" : "REC"}
          </button>
        </footer>
      </section>
    </main>
  );
}