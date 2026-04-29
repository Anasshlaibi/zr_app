"""
ZineControl Web – Upgraded Backend
===================================
Stack:
  - gphoto2 (replaces ptpy) — industry-standard library, same tech as Lightroom tethering
  - USB hotplug via pyudev (Linux) — auto-detects camera on plug-in, zero clicks
  - Wi-Fi discovery via SSDP broadcast
  - Live view: gphoto2 capture_preview() loop → FFmpeg → MPEG1 → WebSocket → browser
  - Full camera config tree: ISO, shutter, aperture, WB — read AND write
"""

import asyncio
import os
import subprocess
import threading
import logging
import socket
import time
from typing import Set

from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ─── Try importing gphoto2 ────────────────────────────────────────────────────
try:
    import gphoto2 as gp
    GPHOTO2_AVAILABLE = True
    logger.info("gphoto2 loaded successfully")
except ImportError:
    GPHOTO2_AVAILABLE = False
    logger.warning("gphoto2 not installed — running in demo mode. Run: pip install gphoto2")

# ─── Try importing pyudev (Linux USB hotplug) ─────────────────────────────────
try:
    import pyudev
    PYUDEV_AVAILABLE = True
except ImportError:
    PYUDEV_AVAILABLE = False

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════════
TARGET_SIZE = os.getenv("STREAM_SIZE", "1280x720")
TARGET_FPS  = os.getenv("STREAM_FPS", "30")

app = FastAPI(title="ZineControl Web v2")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# ═══════════════════════════════════════════════════════════════════════════════
# CAMERA MANAGER
# ═══════════════════════════════════════════════════════════════════════════════
class CameraManager:
    """
    Singleton that owns the gphoto2 Camera object and all state.
    Uses a threading.Lock because gphoto2 calls are blocking (run in thread pool).
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self.camera = None
        self.lock = threading.Lock()
        self.is_connected = False
        self.connection_type = None
        self.cam_props = {}          # live cache of ISO/shutter/aperture/wb
        self.hotplug_callbacks = []  # async callbacks for SSE/WS push

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _safe_get_config_value(self, name: str) -> str | None:
        """Read a single config value by name. Returns None if unsupported."""
        if not self.camera:
            return None
        try:
            cfg = self.camera.get_config()
            widget = gp.check_result(gp.gp_widget_get_child_by_name(cfg, name))
            return str(gp.check_result(gp.gp_widget_get_value(widget)))
        except Exception:
            return None

    def _safe_set_config_value(self, name: str, value: str):
        """Write a single config value by name."""
        cfg = self.camera.get_config()
        widget = gp.check_result(gp.gp_widget_get_child_by_name(cfg, name))
        gp.check_result(gp.gp_widget_set_value(widget, value))
        self.camera.set_config(cfg)

    def _get_widget_choices(self, name: str) -> list[str]:
        """Return all valid choices for a radio/menu widget."""
        try:
            cfg = self.camera.get_config()
            widget = gp.check_result(gp.gp_widget_get_child_by_name(cfg, name))
            count = gp.check_result(gp.gp_widget_count_choices(widget))
            return [str(gp.check_result(gp.gp_widget_get_choice(widget, i))) for i in range(count)]
        except Exception:
            return []

    def _read_all_props(self) -> dict:
        """Read all camera properties in one sweep and cache them."""
        if not self.camera:
            return {}
        props = {}
        for key in ["iso", "shutterspeed", "aperture", "whitebalance",
                    "exposurecompensation", "focusmode", "capturemode",
                    "imageformat", "batterylevel"]:
            props[key] = self._safe_get_config_value(key)
        self.cam_props = {k: v for k, v in props.items() if v is not None}
        return self.cam_props

    # ── Connection ────────────────────────────────────────────────────────────

    def connect_usb(self):
        """
        Connect via USB-C using gphoto2 (libgphoto2 under the hood).

        No Zadig needed on Mac/Linux — gphoto2 handles the USB driver itself.

        On Windows: install libgphoto2 via https://github.com/gphoto/libgphoto2/releases
        Camera must be in PTP mode: Menu > Setup > USB connection > PTP
        """
        if not GPHOTO2_AVAILABLE:
            raise Exception("gphoto2 not installed. Run: pip install gphoto2")
        with self.lock:
            try:
                if self.camera:
                    try:
                        self.camera.exit()
                    except Exception:
                        pass
                cam = gp.Camera()
                cam.init()
                self.camera = cam
                self.is_connected = True
                self.connection_type = "USB-C"
                self._read_all_props()
                logger.info("Connected via USB-C (gphoto2)")
                return True
            except gp.GPhoto2Error as e:
                self.is_connected = False
                self.camera = None
                raise Exception(
                    f"USB connection failed: {e}\n"
                    "Checklist:\n"
                    "1. Camera: Menu > Setup > USB connection > PTP\n"
                    "2. Unplug and replug the cable\n"
                    "3. Make sure no other app (Lightroom, Photos) is using the camera"
                )

    def connect_wifi(self, ip: str, port: int = 15740):
        """
        Connect via Wi-Fi using PTP/IP (port 15740 — the standard PTP/IP port).
        Camera must have Wi-Fi enabled: Menu > Network > Connect to smart device.
        """
        if not GPHOTO2_AVAILABLE:
            raise Exception("gphoto2 not installed. Run: pip install gphoto2")
        with self.lock:
            try:
                if self.camera:
                    try:
                        self.camera.exit()
                    except Exception:
                        pass

                # gphoto2 PTP/IP connection string format
                port_info_list = gp.PortInfoList()
                port_info_list.load()
                abilities_list = gp.CameraAbilitiesList()
                abilities_list.load()

                cam = gp.Camera()
                # Set the port to ptpip
                idx = port_info_list.lookup_path(f"ptpip:{ip}:{port}")
                cam.set_port_info(port_info_list[idx])
                cam.init()

                self.camera = cam
                self.is_connected = True
                self.connection_type = "Wi-Fi"
                self._read_all_props()
                logger.info(f"Connected via Wi-Fi to {ip}:{port}")
                return True
            except Exception as e:
                self.is_connected = False
                self.camera = None
                raise Exception(
                    f"Wi-Fi connection failed: {e}\n"
                    "Checklist:\n"
                    "1. Camera: Menu > Network > Connect to smart device (enable)\n"
                    "2. Check IP: Menu > Network > View current connection\n"
                    "3. PC must be on same Wi-Fi network as camera"
                )

    def disconnect(self):
        with self.lock:
            if self.camera:
                try:
                    self.camera.exit()
                except Exception:
                    pass
            self.camera = None
            self.is_connected = False
            self.connection_type = None
            self.cam_props = {}

    # ── Live view frame ───────────────────────────────────────────────────────

    def capture_preview_frame(self) -> bytes | None:
        """
        Pull one live-view JPEG frame from the camera.
        This is the same mechanism used by Lightroom, Capture One, and Nikon NX.
        Returns raw JPEG bytes, or None if failed.
        """
        if not self.camera or not self.is_connected:
            return None
        try:
            with self.lock:
                camera_file = self.camera.capture_preview()
                return camera_file.get_raw_data()
        except gp.GPhoto2Error as e:
            logger.warning(f"Preview frame error: {e}")
            if e.code in (-1, -7, -110):   # I/O error codes → camera disconnected
                self.is_connected = False
            return None

    # ── Property get/set ──────────────────────────────────────────────────────

    def get_property(self, name: str) -> str | None:
        if not self.is_connected:
            return None
        return self._safe_get_config_value(name)

    def set_property(self, name: str, value: str):
        if not self.is_connected or not self.camera:
            raise Exception("Camera not connected")
        with self.lock:
            try:
                self._safe_set_config_value(name, value)
                self.cam_props[name] = value
                logger.info(f"Set {name} = {value}")
            except gp.GPhoto2Error as e:
                raise Exception(f"Failed to set {name}: {e}")

    def get_choices(self, name: str) -> list[str]:
        if not self.is_connected:
            return []
        return self._get_widget_choices(name)

    def trigger_capture(self):
        """Trigger shutter. Image is saved to camera's memory card."""
        if not self.is_connected or not self.camera:
            raise Exception("Camera not connected")
        with self.lock:
            self.camera.trigger_capture()
            logger.info("Shutter triggered")

    def refresh_props(self) -> dict:
        return self._read_all_props()


cam = CameraManager()

# ═══════════════════════════════════════════════════════════════════════════════
# USB HOTPLUG (Linux only via pyudev)
# ═══════════════════════════════════════════════════════════════════════════════
hotplug_ws_clients: Set[WebSocket] = set()

def _usb_hotplug_thread():
    """
    Runs in a background thread, watches for camera USB plug/unplug events.
    When a PTP camera is detected, auto-connects and notifies all WebSocket clients.
    """
    if not PYUDEV_AVAILABLE:
        return

    context = pyudev.Context()
    monitor = pyudev.Monitor.from_netlink(context)
    monitor.filter_by(subsystem="usb")

    logger.info("USB hotplug monitor started")

    for device in iter(monitor.poll, None):
        if device.action == "add":
            # Check if it's a camera (PTP class = 0x06, subclass 0x01)
            device_class = device.attributes.get("bDeviceClass")
            if device_class in (b"06", b"00"):   # Image / misc (gphoto2 handles both)
                logger.info(f"Camera detected on USB: {device.get('ID_MODEL', 'Unknown')}")
                time.sleep(1.5)  # Let OS settle before connecting
                try:
                    cam.connect_usb()
                    _notify_hotplug("connected", "USB-C")
                except Exception as e:
                    logger.error(f"Auto-connect failed: {e}")
                    _notify_hotplug("error", str(e))

        elif device.action == "remove":
            if cam.connection_type == "USB-C" and cam.is_connected:
                cam.disconnect()
                logger.info("Camera unplugged — disconnected")
                _notify_hotplug("disconnected", None)

def _notify_hotplug(event: str, data):
    """Push a hotplug event to all connected WebSocket clients."""
    import json
    msg = json.dumps({"event": event, "data": data})
    dead = set()
    for ws in list(hotplug_ws_clients):
        try:
            asyncio.run_coroutine_threadsafe(ws.send_text(msg), _event_loop)
        except Exception:
            dead.add(ws)
    hotplug_ws_clients -= dead

_event_loop: asyncio.AbstractEventLoop | None = None

# ═══════════════════════════════════════════════════════════════════════════════
# WI-FI CAMERA DISCOVERY  (SSDP broadcast)
# ═══════════════════════════════════════════════════════════════════════════════
def discover_camera_ip() -> str | None:
    """
    Broadcasts SSDP to find Nikon/Sony cameras on the local network.
    Filters responses to only return devices that identify as Nikon/camera.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(4.0)

    ssdp = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 2\r\n"
        "ST: ssdp:all\r\n\r\n"
    )
    try:
        sock.sendto(ssdp.encode(), ("239.255.255.250", 1900))
        sock.sendto(ssdp.encode(), ("255.255.255.255", 1900))
        while True:
            data, addr = sock.recvfrom(2048)
            resp = data.decode(errors="ignore").lower()
            if any(k in resp for k in ["nikon", "ptp", "camera", "imaging", "sony", "canon"]):
                logger.info(f"Camera found at {addr[0]}")
                return addr[0]
    except socket.timeout:
        return None
    finally:
        sock.close()

# ═══════════════════════════════════════════════════════════════════════════════
# LIVE VIEW STREAM  (gphoto2 → FFmpeg → MPEG1 → WebSocket)
# ═══════════════════════════════════════════════════════════════════════════════
stream_clients: Set[WebSocket] = set()
_ffmpeg_proc: subprocess.Popen | None = None
_stream_task: asyncio.Task | None = None

def _make_ffmpeg() -> subprocess.Popen:
    """
    FFmpeg reads MJPEG from stdin (we pipe gphoto2 preview frames),
    transcodes to MPEG1 (what JSMpeg needs), outputs to stdout.
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "mjpeg",                      # input format: MJPEG frames
        "-i", "pipe:0",                     # from stdin
        "-an",                              # no audio
        "-c:v", "mpeg1video",
        "-bf", "0", "-g", "1",
        "-tune", "zerolatency",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-r", TARGET_FPS,
        "-s", TARGET_SIZE,
        "-f", "mpegts",
        "pipe:1"                            # to stdout
    ]
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )

async def _stream_loop():
    """
    Main streaming coroutine:
    1. Pull JPEG frame from gphoto2 (blocking → thread pool)
    2. Write it to FFmpeg stdin
    3. Read MPEG1 chunk from FFmpeg stdout
    4. Broadcast to all WebSocket clients
    """
    global _ffmpeg_proc

    logger.info("Stream loop started")
    _ffmpeg_proc = _make_ffmpeg()

    async def _read_ffmpeg_chunk() -> bytes:
        return await asyncio.to_thread(_ffmpeg_proc.stdout.read, 1316)

    while True:
        # If camera not connected, send a test pattern so the player stays alive
        if not cam.is_connected:
            await asyncio.sleep(0.5)
            continue

        # 1. Grab live view frame
        frame = await asyncio.to_thread(cam.capture_preview_frame)
        if frame is None:
            await asyncio.sleep(0.05)
            continue

        # 2. Feed frame to FFmpeg
        try:
            await asyncio.to_thread(_ffmpeg_proc.stdin.write, frame)
        except BrokenPipeError:
            logger.warning("FFmpeg pipe broken — restarting")
            _ffmpeg_proc = _make_ffmpeg()
            continue

        # 3. Read encoded chunk
        chunk = await _read_ffmpeg_chunk()
        if not chunk:
            continue

        # 4. Broadcast
        if not stream_clients:
            continue
        dead = set()
        results = await asyncio.gather(
            *(ws.send_bytes(chunk) for ws in list(stream_clients)),
            return_exceptions=True
        )
        for ws, res in zip(list(stream_clients), results):
            if isinstance(res, Exception):
                dead.add(ws)
        stream_clients -= dead

# ═══════════════════════════════════════════════════════════════════════════════
# APP LIFECYCLE
# ═══════════════════════════════════════════════════════════════════════════════
@app.on_event("startup")
async def startup():
    global _stream_task, _event_loop
    _event_loop = asyncio.get_event_loop()
    _stream_task = asyncio.create_task(_stream_loop())

    # Start USB hotplug watcher in background thread (Linux)
    if PYUDEV_AVAILABLE:
        t = threading.Thread(target=_usb_hotplug_thread, daemon=True)
        t.start()
        logger.info("USB hotplug watcher running")

@app.on_event("shutdown")
async def shutdown():
    global _stream_task, _ffmpeg_proc
    if _stream_task:
        _stream_task.cancel()
    if _ffmpeg_proc and _ffmpeg_proc.poll() is None:
        _ffmpeg_proc.terminate()
    cam.disconnect()

# ═══════════════════════════════════════════════════════════════════════════════
# WEBSOCKETS
# ═══════════════════════════════════════════════════════════════════════════════
@app.websocket("/ws/video")
async def ws_video(ws: WebSocket):
    await ws.accept()
    stream_clients.add(ws)
    try:
        while True:
            await ws.receive()
    except Exception:
        stream_clients.discard(ws)

@app.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    """
    Push channel: backend sends camera events to frontend.
    Events: hotplug connect/disconnect, property changes, errors.
    """
    await ws.accept()
    hotplug_ws_clients.add(ws)
    # Send current status immediately on connect
    import json
    await ws.send_text(json.dumps({
        "event": "status",
        "connected": cam.is_connected,
        "type": cam.connection_type,
        "props": cam.cam_props,
    }))
    try:
        while True:
            await ws.receive()
    except Exception:
        hotplug_ws_clients.discard(ws)

# ═══════════════════════════════════════════════════════════════════════════════
# REST API
# ═══════════════════════════════════════════════════════════════════════════════
class ConnectWifiReq(BaseModel):
    ip_address: str
    port: int = 15740

class SetPropReq(BaseModel):
    value: str

@app.get("/api/status")
async def status():
    return {
        "connected": cam.is_connected,
        "type": cam.connection_type,
        "props": cam.cam_props,
        "gphoto2": GPHOTO2_AVAILABLE,
        "hotplug": PYUDEV_AVAILABLE,
    }

@app.get("/api/discover")
async def discover():
    ip = await asyncio.to_thread(discover_camera_ip)
    if not ip:
        raise HTTPException(404, "No camera found on network. Make sure camera Wi-Fi is on and you're on the same network.")
    return {"ip_address": ip}

@app.post("/api/connect/usb")
async def connect_usb():
    try:
        await asyncio.to_thread(cam.connect_usb)
        return {"status": "connected", "type": "usb", "props": cam.cam_props}
    except Exception as e:
        raise HTTPException(400, str(e))

@app.post("/api/connect/wifi")
async def connect_wifi(req: ConnectWifiReq):
    try:
        await asyncio.to_thread(cam.connect_wifi, req.ip_address, req.port)
        return {"status": "connected", "type": "wifi", "props": cam.cam_props}
    except Exception as e:
        raise HTTPException(400, str(e))

@app.post("/api/disconnect")
async def disconnect():
    cam.disconnect()
    return {"status": "disconnected"}

@app.get("/api/camera/props")
async def get_props():
    if not cam.is_connected:
        raise HTTPException(400, "Camera not connected")
    props = await asyncio.to_thread(cam.refresh_props)
    return props

@app.get("/api/camera/choices/{prop}")
async def get_choices(prop: str):
    choices = await asyncio.to_thread(cam.get_choices, prop)
    return {"choices": choices}

@app.post("/api/camera/prop/{prop}")
async def set_prop(prop: str, req: SetPropReq):
    allowed = {"iso", "shutterspeed", "aperture", "whitebalance",
               "exposurecompensation", "focusmode", "capturemode", "imageformat"}
    if prop not in allowed:
        raise HTTPException(400, f"Unknown property: {prop}")
    try:
        await asyncio.to_thread(cam.set_property, prop, req.value)
        return {"status": "ok", prop: req.value}
    except Exception as e:
        raise HTTPException(400, str(e))

@app.post("/api/camera/capture")
async def capture():
    try:
        await asyncio.to_thread(cam.trigger_capture)
        return {"status": "captured"}
    except Exception as e:
        raise HTTPException(400, str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
