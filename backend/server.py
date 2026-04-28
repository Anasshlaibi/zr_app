import asyncio
import os
import subprocess
from typing import Set
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import gphoto2 as gp

import socket
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="ZineControl Web Bridge")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- VIDEO STREAMING GLOBALS ---
clients: Set[WebSocket] = set()
stream_task: asyncio.Task | None = None
ffmpeg_process: subprocess.Popen[bytes] | None = None

RTSP_URL = os.getenv("NIKON_RTSP_URL", "").strip()
TARGET_SIZE = os.getenv("STREAM_SIZE", "1280x720")
TARGET_FPS = os.getenv("STREAM_FPS", "30")

# --- CAMERA CONTROL & CONNECTION MANAGER ---
class CameraCommand(BaseModel):
    value: str | None = None

class ConnectionRequest(BaseModel):
    ip_address: str | None = None

class CameraManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(CameraManager, cls).__new__(cls)
            cls._instance.camera = gp.Camera()
            cls._instance.context = gp.Context()
            cls._instance.queue = asyncio.Queue()
            cls._instance.is_connected = False
            cls._instance.connection_type = None
        return cls._instance

    def connect_usb(self):
        try:
            # Re-initialize to clear old states
            self.camera = gp.Camera()
            self.camera.init(self.context)
            self.is_connected = True
            self.connection_type = "USB-C"
            logger.info("✅ Connected to Nikon via USB-C")
            return True
        except gp.GPhoto2Error as e:
            logger.error(f"⚠️ USB Connection failed: {e}")
            self.is_connected = False
            raise Exception("Could not detect camera on USB. Is it plugged in and powered on?")

    def connect_wifi(self, ip_address: str):
        try:
            self.camera = gp.Camera()
            port_info_list = gp.PortInfoList()
            port_info_list.load()
            
            # Find the specific PTP/IP port index
            idx = port_info_list.lookup_path(f"ptpip:{ip_address}")
            if idx < 0:
                raise Exception(f"PTP/IP port not found for {ip_address}")
                
            self.camera.set_port_info(port_info_list[idx])
            self.camera.init(self.context)
            self.is_connected = True
            self.connection_type = "Wi-Fi"
            logger.info(f"✅ Connected to Nikon via Wi-Fi ({ip_address})")
            return True
        except gp.GPhoto2Error as e:
            logger.error(f"⚠️ Wi-Fi Connection failed: {e}")
            self.is_connected = False
            raise Exception("Could not connect over Wi-Fi. Check IP address and network.")

    async def worker_loop(self):
        logger.info("Starting Camera Command Worker...")
        while True:
            cmd_type, payload = await self.queue.get()
            try:
                if self.is_connected:
                    logger.info(f"Executing Command: {cmd_type} -> {payload}")
                    if cmd_type == "record":
                        logger.info("📸 Triggering capture...")
                        await asyncio.to_thread(self.camera.trigger_capture, self.context)
                    elif cmd_type in ["iso", "shutterspeed", "aperture"]:
                        logger.info(f"⚙️ Setting {cmd_type} to {payload}...")
                        def update_config():
                            config = self.camera.get_config(self.context)
                            child = config.get_child_by_name(cmd_type)
                            child.set_value(payload)
                            self.camera.set_config(config, self.context)
                        await asyncio.to_thread(update_config)
                else:
                    logger.warning(f"Skipped {cmd_type}: Camera not connected.")
            except Exception as e:
                logger.error(f"Error executing {cmd_type}: {e}")
                self.is_connected = False # Drop connection on fatal command error
            finally:
                self.queue.task_done()

cam_manager = CameraManager()
camera_worker_task: asyncio.Task | None = None

def discover_camera_ip() -> str | None:
    """
    Sends a UDP broadcast to discover the Nikon camera via SSDP/UPnP.
    Returns the IP address if found, otherwise None.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(1.0) # Resolves in under 1 second
    
    ssdp_request = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 1\r\n"
        "ST: ssdp:all\r\n"
        "\r\n"
    )
    
    try:
        sock.sendto(ssdp_request.encode(), ("239.255.255.250", 1900))
        sock.sendto(ssdp_request.encode(), ("255.255.255.255", 1900))
        
        while True:
            data, addr = sock.recvfrom(1024)
            # In a professional app, we verify the headers contain Nikon or PTP/IP signatures.
            # Here we assume any rapid response is our rig.
            return addr[0]
    except socket.timeout:
        return None
    except Exception as e:
        logger.error(f"Discovery error: {e}")
        return None
    finally:
        sock.close()

# --- FFMPEG VIDEO FUNCTIONS ---
def ffmpeg_command() -> list[str]:
    if RTSP_URL:
        source_args = ["-rtsp_transport", "tcp", "-fflags", "nobuffer", "-flags", "low_delay", "-i", RTSP_URL]
    else:
        source_args = ["-re", "-f", "lavfi", "-i", f"testsrc2=size={TARGET_SIZE}:rate={TARGET_FPS}"]

    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", *source_args,
        "-an", "-c:v", "mpeg1video", "-bf", "0", "-g", "1",
        "-tune", "zerolatency", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", "-r", TARGET_FPS, "-s", TARGET_SIZE, "-f", "mpegts", "-"
    ]

def start_ffmpeg() -> subprocess.Popen[bytes]:
    return subprocess.Popen(ffmpeg_command(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)

async def stream_mpegts() -> None:
    global ffmpeg_process
    while True:
        if ffmpeg_process is None or ffmpeg_process.poll() is not None:
            ffmpeg_process = start_ffmpeg()
        if ffmpeg_process.stdout is None:
            await asyncio.sleep(0.1)
            continue
        chunk = await asyncio.to_thread(ffmpeg_process.stdout.read, 1316)
        if not chunk:
            await asyncio.sleep(0.1)
            continue
        if not clients:
            continue
        dead_clients = set()
        targets = list(clients)
        results = await asyncio.gather(*(ws.send_bytes(chunk) for ws in targets), return_exceptions=True)
        for ws, result in zip(targets, results):
            if isinstance(result, Exception):
                dead_clients.add(ws)
        for ws in dead_clients:
            clients.discard(ws)

# --- FASTAPI LIFECYCLE & ROUTES ---
@app.on_event("startup")
async def startup_event() -> None:
    global stream_task, camera_worker_task
    stream_task = asyncio.create_task(stream_mpegts())
    camera_worker_task = asyncio.create_task(cam_manager.worker_loop())

@app.on_event("shutdown")
async def shutdown_event() -> None:
    global stream_task, ffmpeg_process, camera_worker_task
    if stream_task: stream_task.cancel()
    if camera_worker_task: camera_worker_task.cancel()
    if ffmpeg_process is not None and ffmpeg_process.poll() is None:
        ffmpeg_process.terminate()
        ffmpeg_process.wait(timeout=2)

@app.websocket("/ws/video")
async def websocket_video(ws: WebSocket) -> None:
    await ws.accept()
    clients.add(ws)
    try:
        while True: await ws.receive()
    except Exception:
        clients.discard(ws)

# --- CONNECTION ENDPOINTS ---
@app.get("/api/status")
async def get_status():
    return {
        "connected": cam_manager.is_connected,
        "type": cam_manager.connection_type
    }

@app.get("/api/discover")
async def discover_camera():
    ip = await asyncio.to_thread(discover_camera_ip)
    if not ip:
        # For demonstration if camera is offline, you could mock it, but we return 404 to trigger manual fallback.
        raise HTTPException(status_code=404, detail="No camera found on network")
    return {"ip_address": ip}

@app.post("/api/connect/usb")
async def connect_usb():
    try:
        await asyncio.to_thread(cam_manager.connect_usb)
        return {"status": "connected", "type": "usb"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/connect/wifi")
async def connect_wifi(req: ConnectionRequest):
    if not req.ip_address:
        raise HTTPException(status_code=400, detail="IP address required for Wi-Fi")
    try:
        await asyncio.to_thread(cam_manager.connect_wifi, req.ip_address)
        return {"status": "connected", "type": "wifi"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# --- CAMERA CONTROL ENDPOINTS ---
@app.post("/api/camera/iso")
async def set_iso(req: CameraCommand):
    await cam_manager.queue.put(("iso", req.value))
    return {"status": "queued"}

@app.post("/api/camera/shutter")
async def set_shutter(req: CameraCommand):
    await cam_manager.queue.put(("shutterspeed", req.value))
    return {"status": "queued"}

@app.post("/api/camera/aperture")
async def set_aperture(req: CameraCommand):
    await cam_manager.queue.put(("aperture", req.value))
    return {"status": "queued"}

@app.post("/api/camera/record")
async def toggle_record(req: CameraCommand):
    await cam_manager.queue.put(("record", req.value))
    return {"status": "queued"}