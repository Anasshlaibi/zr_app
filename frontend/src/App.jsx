import { useEffect, useRef, useState } from "react";
import JSMpeg from "jsmpeg";

export default function App() {
  const canvasRef = useRef(null);
  const [streamStatus, setStreamStatus] = useState("CONNECTING");
  const [rec, setRec] = useState(false);

  // Connection Setup States
  const [camConnected, setCamConnected] = useState(false);
  const [setupMode, setSetupMode] = useState("wifi"); // 'usb' or 'wifi'
  const [ipAddress, setIpAddress] = useState("192.168.1.50");
  const [isConnecting, setIsConnecting] = useState(false);
  const [isScanning, setIsScanning] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");

  const getBaseUrl = () => {
    const host = window.location.hostname;
    return `http://${host}:8000`;
  };

  const getWsUrl = () => {
    const host = window.location.hostname;
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    return `${protocol}://${host}:8000/ws/video`;
  };

  useEffect(() => {
    fetch(`${getBaseUrl()}/api/status`)
      .then(res => res.json())
      .then(data => {
        if (data.connected) setCamConnected(true);
      })
      .catch(() => console.log("Backend offline"));
  }, []);

  const handleConnect = async () => {
    setIsConnecting(true);
    setErrorMsg("");
    const endpoint = setupMode === "usb" ? "/api/connect/usb" : "/api/connect/wifi";
    const body = setupMode === "wifi" ? { ip_address: ipAddress } : {};

    try {
      const res = await fetch(`${getBaseUrl()}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      const data = await res.json();

      if (!res.ok) throw new Error(data.detail || "Connection failed");
      setCamConnected(true);
    } catch (err) {
      setErrorMsg(err.message);
    } finally {
      setIsConnecting(false);
    }
  };

  const discoverCamera = async () => {
    setIsScanning(true);
    setErrorMsg("");
    try {
      const res = await fetch(`${getBaseUrl()}/api/discover`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Discovery failed");

      setIpAddress(data.ip_address);

      const connectRes = await fetch(`${getBaseUrl()}/api/connect/wifi`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ip_address: data.ip_address })
      });
      const connectData = await connectRes.json();
      if (!connectRes.ok) throw new Error(connectData.detail || "Connection failed");

      setCamConnected(true);
    } catch (err) {
      setErrorMsg(err.message);
    } finally {
      setIsScanning(false);
    }
  };

  useEffect(() => {
    if (setupMode === "wifi" && !camConnected) {
      discoverCamera();
    }
  }, [setupMode, camConnected]);

  const sendCommand = async (endpoint, payload = {}) => {
    if (!camConnected) return;
    try {
      await fetch(`${getBaseUrl()}/api/camera/${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } catch (err) {
      console.error(`Failed to send ${endpoint}`, err);
    }
  };

  useEffect(() => {
    let isMounted = true;
    let player = null;
    let retryTimer = null;

    const connectPlayer = () => {
      const wsUrl = getWsUrl();
      const canvas = canvasRef.current;

      if (!isMounted || !canvas) return;

      setStreamStatus("CONNECTING");
      try {
        player = new JSMpeg(wsUrl, {
          canvas,
          autoplay: true,
          audio: false,
          onSourceEstablished: () => isMounted && setStreamStatus("LIVE"),
          onStalled: () => isMounted && setStreamStatus("RECONNECTING"),
          onSourceCompleted: () => {
            if (!isMounted) return;
            setStreamStatus("RECONNECTING");
            retryTimer = setTimeout(connectPlayer, 800);
          },
        });
      } catch (e) {
        console.error("JSMpeg error:", e);
        if (isMounted) setStreamStatus("ERROR");
      }
    };

    connectPlayer();

    return () => {
      isMounted = false;
      if (retryTimer) clearTimeout(retryTimer);
      if (player && typeof player.destroy === "function") player.destroy();
    };
  }, []);

  return (
    <main className="relative h-screen w-screen overflow-hidden bg-zinc-950 text-zinc-100 font-sans">
      {!camConnected && (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-black/90 backdrop-blur-sm p-4">
          <div className="w-full max-w-sm rounded-xl border border-zinc-800 bg-zinc-900 p-6 shadow-2xl">
            <h2 className="mb-6 text-center text-lg font-bold tracking-[0.15em] text-zinc-100">RIG CONNECTION</h2>
            <div className="mb-6 flex rounded-lg bg-zinc-950 p-1">
              <button onClick={() => setSetupMode("wifi")} className={`flex-1 rounded-md py-2 text-sm font-medium transition ${setupMode === "wifi" ? "bg-zinc-800 text-white" : "text-zinc-500"}`}>Wi-Fi (PTP/IP)</button>
              <button onClick={() => setSetupMode("usb")} className={`flex-1 rounded-md py-2 text-sm font-medium transition ${setupMode === "usb" ? "bg-zinc-800 text-white" : "text-zinc-500"}`}>USB-C</button>
            </div>
            {setupMode === "wifi" && (
              <div className="mb-6">
                <label className="mb-2 block text-xs font-medium tracking-wide text-zinc-400">CAMERA IP ADDRESS</label>
                {isScanning ? (
                  <div className="w-full rounded-lg border border-emerald-900 bg-emerald-900/20 px-4 py-3 text-sm font-medium tracking-wide text-emerald-400 flex items-center justify-center animate-pulse">Scanning Network...</div>
                ) : (
                  <input type="text" value={ipAddress} onChange={(e) => setIpAddress(e.target.value)} className="w-full rounded-lg border border-zinc-700 bg-black px-4 py-3 text-sm text-white focus:border-emerald-500 focus:outline-none" placeholder="192.168.1.x" />
                )}
              </div>
            )}
            {errorMsg && <div className="mb-4 rounded border border-red-900/50 bg-red-900/20 p-3 text-xs text-red-400">{errorMsg}</div>}
            <button onClick={setupMode === "wifi" && !isScanning ? discoverCamera : handleConnect} disabled={isConnecting || isScanning} className="w-full rounded-lg bg-emerald-600 py-3 text-sm font-bold tracking-wide text-white transition hover:bg-emerald-500 disabled:opacity-50">
              {isConnecting ? "CONNECTING..." : isScanning ? "SCANNING..." : setupMode === "wifi" ? "RESCAN & INITIALIZE" : "INITIALIZE RIG"}
            </button>
          </div>
        </div>
      )}
      <section className="mx-auto flex h-full w-full max-w-[1024px] flex-col justify-between p-2 transition-opacity duration-500" style={{ opacity: camConnected ? 1 : 0.2 }}>
        <header className="flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2">
          <div className="text-xs tracking-[0.18em] text-zinc-500">ZINECONTROL WEB</div>
          <div className="flex items-center gap-2">
            <span className={`h-2.5 w-2.5 rounded-full ${streamStatus === "LIVE" ? "bg-emerald-500" : "bg-red-600"}`} />
            <span className="text-[11px] font-medium tracking-wide text-zinc-300">FEED: {streamStatus}</span>
          </div>
        </header>
        <div className="my-2 flex min-h-0 flex-1 items-center justify-center">
          <div className="relative w-full overflow-hidden rounded-lg border border-zinc-800 bg-black">
            <div className="aspect-video w-full">
              <canvas ref={canvasRef} width={1280} height={720} className="h-full w-full object-cover" />
            </div>
          </div>
        </div>
        <footer className="grid grid-cols-4 gap-2 rounded-lg border border-zinc-800 bg-zinc-900 p-2">
          <button onClick={() => sendCommand("iso", { value: "800" })} className="rounded-md border border-zinc-700 bg-zinc-800 py-3 text-sm font-semibold text-zinc-200 active:bg-zinc-700">ISO</button>
          <button onClick={() => sendCommand("shutter", { value: "1/50" })} className="rounded-md border border-zinc-700 bg-zinc-800 py-3 text-sm font-semibold text-zinc-200 active:bg-zinc-700">Shutter</button>
          <button onClick={() => sendCommand("aperture", { value: "2.8" })} className="rounded-md border border-zinc-700 bg-zinc-800 py-3 text-sm font-semibold text-zinc-200 active:bg-zinc-700">Iris</button>
          <button onClick={() => { const newState = !rec; setRec(newState); sendCommand("record", { value: newState ? "start" : "stop" }); }} className={`rounded-md py-3 text-sm font-extrabold text-white transition ${rec ? "bg-red-600" : "bg-red-800/80"}`}>{rec ? "REC ●" : "REC"}</button>
        </footer>
      </section>
    </main>
  );
}
