import { useEffect, useRef, useState } from "react";
import JSMpeg from "jsmpeg";

export default function App() {
  const canvasRef = useRef(null);
  const [streamStatus, setStreamStatus] = useState("CONNECTING");
  const [rec, setRec] = useState(false);

  // Connection Setup States
  const [camConnected, setCamConnected] = useState(false);
  const [setupMode, setSetupMode] = useState("wifi");
  const [ipAddress, setIpAddress] = useState("192.168.1.50");
  const [isConnecting, setIsConnecting] = useState(false);
  const [isScanning, setIsScanning] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");

  // Camera State
  const [cameraState, setCameraState] = useState({
    iso: "auto",
    shutter: "1/50",
    aperture: "2.8",
    recording: false
  });

  // UI State
  const [selectedIso, setSelectedIso] = useState("800");
  const [selectedShutter, setSelectedShutter] = useState("1/50");
  const [selectedAperture, setSelectedAperture] = useState("2.8");

  const ISO_OPTIONS = ["100", "200", "400", "800", "1600", "3200", "6400"];
  const SHUTTER_OPTIONS = ["1/50", "1/100", "1/200", "1/500", "1/1000"];
  const APERTURE_OPTIONS = ["1.4", "2.0", "2.8", "4.0", "5.6"];

  const getBaseUrl = () => {
    const host = window.location.hostname;
    return `http://${host}:8000`;
  };

  const getWsUrl = () => {
    const host = window.location.hostname;
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    return `${protocol}://${host}:8000/ws/video`;
  };

  // Fetch camera status on mount and periodically
  useEffect(() => {
    const checkStatus = async () => {
      try {
        const res = await fetch(`${getBaseUrl()}/api/status`);
        const data = await res.json();
        if (data.connected) {
          setCamConnected(true);
          if (data.state) {
            setCameraState(data.state);
          }
        }
      } catch (err) {
        console.log("Backend offline");
      }
    };

    checkStatus();
    const interval = setInterval(checkStatus, 1000); // Poll every second
    return () => clearInterval(interval);
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
      const res = await fetch(`${getBaseUrl()}/api/camera/${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      console.log(`Command sent: ${endpoint}`, data);
    } catch (err) {
      console.error(`Failed to send ${endpoint}`, err);
    }
  };

  // Video stream setup
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
              <button
                onClick={() => setSetupMode("wifi")}
                className={`flex-1 rounded-md py-2 text-sm font-medium transition ${
                  setupMode === "wifi" ? "bg-zinc-800 text-white" : "text-zinc-500"
                }`}
              >
                WIFI
              </button>
              <button
                onClick={() => setSetupMode("usb")}
                className={`flex-1 rounded-md py-2 text-sm font-medium transition ${
                  setupMode === "usb" ? "bg-zinc-800 text-white" : "text-zinc-500"
                }`}
              >
                USB
              </button>
            </div>
            {setupMode === "wifi" && (
              <div className="mb-6">
                <label className="mb-2 block text-xs font-medium tracking-wide text-zinc-400">CAMERA IP ADDRESS</label>
                {isScanning ? (
                  <div className="w-full rounded-lg border border-emerald-900 bg-emerald-900/20 px-4 py-3 text-sm font-medium tracking-wide text-emerald-400 flex items-center justify-center">
                    🔍 SCANNING...
                  </div>
                ) : (
                  <input
                    type="text"
                    value={ipAddress}
                    onChange={(e) => setIpAddress(e.target.value)}
                    className="w-full rounded-lg border border-zinc-700 bg-black px-4 py-3 text-sm text-white focus:border-emerald-600 focus:outline-none"
                  />
                )}
              </div>
            )}
            {errorMsg && (
              <div className="mb-4 rounded border border-red-900/50 bg-red-900/20 p-3 text-xs text-red-400">
                {errorMsg}
              </div>
            )}
            <button
              onClick={setupMode === "wifi" && !isScanning ? discoverCamera : handleConnect}
              disabled={isConnecting || isScanning}
              className="w-full rounded-lg bg-emerald-600 py-3 text-sm font-bold tracking-wider text-white disabled:bg-zinc-700 disabled:text-zinc-500 transition hover:bg-emerald-500"
            >
              {isConnecting ? "CONNECTING..." : isScanning ? "SCANNING..." : setupMode === "wifi" ? "RESCAN & INITIALIZE" : "INITIALIZE RIG"}
            </button>
          </div>
        </div>
      )}

      <section
        className="mx-auto flex h-full w-full max-w-[1024px] flex-col justify-between p-2 transition-opacity duration-500"
        style={{ opacity: camConnected ? 1 : 0.2 }}
      >
        {/* Header */}
        <header className="flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2">
          <div className="text-xs tracking-[0.18em] text-zinc-500">ZINECONTROL WEB</div>
          <div className="flex items-center gap-2">
            <span
              className={`h-2.5 w-2.5 rounded-full ${
                streamStatus === "LIVE" ? "bg-emerald-500" : "bg-red-600"
              }`}
            />
            <span className="text-[11px] font-medium tracking-wide text-zinc-300">FEED: {streamStatus}</span>
          </div>
        </header>

        {/* Main Video Canvas */}
        <div className="my-2 flex min-h-0 flex-1 items-center justify-center">
          <div className="relative w-full overflow-hidden rounded-lg border border-zinc-800 bg-black">
            <div className="aspect-video w-full">
              <canvas ref={canvasRef} width={1280} height={720} className="h-full w-full object-cover" />
            </div>
          </div>
        </div>

        {/* Camera State Display */}
        <div className="mb-2 grid grid-cols-4 gap-2 rounded-lg border border-zinc-800 bg-zinc-900 p-2 text-xs">
          <div className="rounded bg-zinc-800 p-2 text-center">
            <div className="text-zinc-500 text-[10px]">ISO</div>
            <div className="font-bold text-white">{cameraState.iso}</div>
          </div>
          <div className="rounded bg-zinc-800 p-2 text-center">
            <div className="text-zinc-500 text-[10px]">SHUTTER</div>
            <div className="font-bold text-white">{cameraState.shutter}</div>
          </div>
          <div className="rounded bg-zinc-800 p-2 text-center">
            <div className="text-zinc-500 text-[10px]">APERTURE</div>
            <div className="font-bold text-white">f/{cameraState.aperture}</div>
          </div>
          <div className="rounded bg-zinc-800 p-2 text-center">
            <div className="text-zinc-500 text-[10px]">REC</div>
            <div className={`font-bold ${cameraState.recording ? "text-red-500" : "text-zinc-400"}`}>
              {cameraState.recording ? "ON" : "OFF"}
            </div>
          </div>
        </div>

        {/* Controls - ISO */}
        <div className="mb-2">
          <div className="mb-1 text-[10px] font-bold tracking-widest text-zinc-500">ISO</div>
          <div className="grid grid-cols-7 gap-1">
            {ISO_OPTIONS.map((iso) => (
              <button
                key={iso}
                onClick={() => {
                  setSelectedIso(iso);
                  sendCommand("iso", { value: iso });
                }}
                className={`rounded px-2 py-2 text-xs font-bold transition ${
                  selectedIso === iso
                    ? "bg-emerald-600 text-white"
                    : "border border-zinc-700 bg-zinc-800 text-zinc-200 hover:bg-zinc-700"
                }`}
              >
                {iso}
              </button>
            ))}
          </div>
        </div>

        {/* Controls - Shutter Speed */}
        <div className="mb-2">
          <div className="mb-1 text-[10px] font-bold tracking-widest text-zinc-500">SHUTTER</div>
          <div className="grid grid-cols-5 gap-1">
            {SHUTTER_OPTIONS.map((shutter) => (
              <button
                key={shutter}
                onClick={() => {
                  setSelectedShutter(shutter);
                  sendCommand("shutter", { value: shutter });
                }}
                className={`rounded px-2 py-2 text-xs font-bold transition ${
                  selectedShutter === shutter
                    ? "bg-emerald-600 text-white"
                    : "border border-zinc-700 bg-zinc-800 text-zinc-200 hover:bg-zinc-700"
                }`}
              >
                {shutter}
              </button>
            ))}
          </div>
        </div>

        {/* Controls - Aperture */}
        <div className="mb-2">
          <div className="mb-1 text-[10px] font-bold tracking-widest text-zinc-500">APERTURE</div>
          <div className="grid grid-cols-5 gap-1">
            {APERTURE_OPTIONS.map((aperture) => (
              <button
                key={aperture}
                onClick={() => {
                  setSelectedAperture(aperture);
                  sendCommand("aperture", { value: aperture });
                }}
                className={`rounded px-2 py-2 text-xs font-bold transition ${
                  selectedAperture === aperture
                    ? "bg-emerald-600 text-white"
                    : "border border-zinc-700 bg-zinc-800 text-zinc-200 hover:bg-zinc-700"
                }`}
              >
                f/{aperture}
              </button>
            ))}
          </div>
        </div>

        {/* Record Button */}
        <footer className="rounded-lg border border-zinc-800 bg-zinc-900 p-2">
          <button
            onClick={() => {
              const newState = !rec;
              setRec(newState);
              sendCommand("record", { value: newState ? "start" : "stop" });
            }}
            className={`w-full rounded-lg py-4 text-sm font-extrabold text-white transition ${
              rec ? "bg-red-600 hover:bg-red-700" : "bg-zinc-800 border border-zinc-700 hover:bg-zinc-700"
            }`}
          >
            {rec ? "⏹️ STOP RECORDING" : "🔴 START RECORDING"}
          </button>
        </footer>
      </section>
    </main>
  );
}
