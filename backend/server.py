import asyncio
import os
import subprocess
from typing import Set, Dict
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import collections
import collections.abc
collections.Sequence = collections.abc.Sequence
collections.Callable = collections.abc.Callable
collections.Mapping = collections.abc.Mapping
collections.MutableSequence = collections.abc.MutableSequence

import ptpy
from ptpy import constants

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

RTSP_URL = os.getenv("NIKON_RTSP_URL", "rtsp://127.0.0.1:8554/live").strip()
TARGET_SIZE = os.getenv("STREAM_SIZE", "1280x720")
TARGET_FPS = os.getenv("STREAM_FPS", "30")

# --- CAMERA CONTROL & CONNECTION MANAGER ---
class CameraCommand(BaseModel):
    value: str | None = None

class ConnectionRequest(BaseModel):
    ip_address: str | None = None

# --- PTP PROPERTY MAPPINGS FOR NIKON ---
ISO_MAPPING = {
    "100": 0x0064,
    "200": 0x00C8,
    "400": 0x0190,
    "800": 0x0320,
    "1600": 0x0640,
    "3200": 0x0C80,
    "6400": 0x1900,
    "auto": 0x0000,
}

SHUTTER_MAPPING = {
    "1/50": 0x0032,
    "1/100": 0x0064,
    "1/200": 0x00C8,
    "1/500": 0x01F4,
    "1/1000": 0x03E8,
}

APERTURE_MAPPING = {
    "1.4": 0x8E,
    "2.0": 0xC6,
    "2.8": 0xFE,
    "4.0": 0x136,
    "5.6": 0x16E,
}

class CameraManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(CameraManager, cls).__new__(cls)
            cls._instance.camera = None
            cls._instance.queue = asyncio.Queue()
            cls._instance.is_connected = False
            cls._instance.connection_type = None
            cls._instance.state = {
                "iso": "auto",
                "shutter": "1/50",
                "aperture": "2.8",
                "recording": False
            }
        return cls._instance

    def connect_usb(self):
        try:
            self.camera = ptpy.PTPy()
            self.is_connected = True
            self.connection_type = "USB-C"
            logger.info("✅ Connected to Nikon via USB-C")
            return True
        except Exception as e:
            logger.error(f"⚠️ USB Connection failed: {e}")
            self.is_connected = False
            raise Exception("Could not detect camera on USB. If on Windows, ensure the Zadig WinUSB driver is installed!")

    def connect_wifi(self, ip_address: str):
        try:
            from ptpy.transports.ip import IPTransport
            self.camera = ptpy.PTPy(device=ip_address, transport=IPTransport)
            self.is_connected = True
            self.connection_type = "Wi-Fi"
            logger.info(f"✅ Connected to Nikon via Wi-Fi ({ip_address})")
            return True
        except Exception as e:
            logger.error(f"⚠️ Wi-Fi Connection failed: {e}")
            self.is_connected = False
            raise Exception("Could not connect over Wi-Fi. Check IP address and network.")

    def set_iso(self, iso_value: str):
        """Set camera ISO via PTP protocol"""
        try:
            if iso_value not in ISO_MAPPING:
                logger.warning(f"Unknown ISO value: {iso_value}")
                return False
            
            ptp_value = ISO_MAPPING[iso_value]
            # Property code for ISO on Nikon (0x500e is common)
            self.camera.set_property(0x500E, ptp_value)
            self.state["iso"] = iso_value
            logger.info(f"✅ ISO set to {iso_value}")
            return True
        except Exception as e:
            logger.error(f"Error setting ISO: {e}")
            return False

    def set_shutter(self, shutter_value: str):
        """Set camera shutter speed via PTP protocol"""
        try:
            if shutter_value not in SHUTTER_MAPPING:
                logger.warning(f"Unknown shutter value: {shutter_value}")
                return False
            
            ptp_value = SHUTTER_MAPPING[shutter_value]
            # Property code for shutter speed on Nikon (0x500d is common)
            self.camera.set_property(0x500D, ptp_value)
            self.state["shutter"] = shutter_value
            logger.info(f"✅ Shutter speed set to {shutter_value}")
            return True
        except Exception as e:
            logger.error(f"Error setting shutter: {e}")
            return False

    def set_aperture(self, aperture_value: str):
        """Set camera aperture via PTP protocol"""
        try:
            if aperture_value not in APERTURE_MAPPING:
                logger.warning(f"Unknown aperture value: {aperture_value}")
                return False
            
            ptp_value = APERTURE_MAPPING[aperture_value]
            # Property code for aperture on Nikon (0x500c is common)
            self.camera.set_property(0x500C, ptp_value)
            self.state["aperture"] = aperture_value
            logger.info(f"✅ Aperture set to f/{aperture_value}")
            return True
        except Exception as e:
            logger.error(f"Error setting aperture: {e}")
            return False

    def start_recording(self):
        """Start video recording"""
        try:
            # Send record operation code
            self.camera.send_request(0x990B)  # Nikon record start operation
            self.state["recording"] = True
            logger.info("📹 Recording started")
            return True
        except Exception as e:
            logger.error(f"Error starting recording: {e}")
            return False

    def stop_recording(self):
        """Stop video recording"""
        try:
            # Send stop recording operation code
            self.camera.send_request(0x990C)  # Nikon record stop operation
            self.state["recording"] = False
            logger.info("⏹️ Recording stopped")
            return True
        except Exception as e:
            logger.error(f"Error stopping recording: {e}")
            return False

    async def worker_loop(self):
        logger.info("Starting Camera Command Worker...")
        while True:
            cmd_type, payload = await self.queue.get()
            try:
                if self.is_connected and self.camera:
                    logger.info(f"Executing Command: {cmd_type} -> {payload}")
                    if cmd_type == "record":
                        if payload == "start":
                            await asyncio.to_thread(self.start_recording)
                        elif payload == "stop":
                            await asyncio.to_thread(self.stop_recording)
                    elif cmd_type == "iso":
                        await asyncio.to_thread(self.set_iso, payload)
                    elif cmd_type == "shutterspeed":
                        await asyncio.to_thread(self.set_shutter, payload)
                    elif cmd_type == "aperture":
                        await asyncio.to_thread(self.set_aperture, payload)
                else:
                    logger.warning(f"Skipped {cmd_type}: Camera not connected.")
            except Exception as e:
                logger.error(f"Error executing {cmd_type}: {e}")
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
    sock.settimeout(1.0)
    
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
        "type": cam_manager.connection_type,
        "state": cam_manager.state
    }

@app.get("/api/discover")
async def discover_camera():
    ip = await asyncio.to_thread(discover_camera_ip)
    if not ip:
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

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
