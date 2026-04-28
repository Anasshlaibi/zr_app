import asyncio
import os
import subprocess
from typing import Set
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import gphoto2 as gp

# Set up logging for our camera commands
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

# --- CAMERA CONTROL (PHASE 2 SINGLETON) ---
class CameraCommand(BaseModel):
    value: str | None = None

class CameraManager:
    """
    Singleton Manager to ensure only ONE active PTP/IP connection 
    exists on port 15740 at a time, preventing camera lockups.
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(CameraManager, cls).__new__(cls)
            cls._instance.camera = gp.Camera()
            cls._instance.context = gp.Context()
            cls._instance.queue = asyncio.Queue()
            cls._instance.is_connected = False
        return cls._instance

    def _connect(self):
        if not self.is_connected:
            try:
                self.camera.init(self.context)
                self.is_connected = True
                logger.info("✅ PTP/IP Camera successfully connected.")
            except gp.GPhoto2Error as e:
                logger.error(f"⚠️ Camera connection failed (is it on the network?): {e}")

    async def worker_loop(self):
        """Background loop that processes commands one by one safely."""
        logger.info("Starting Camera Command Worker...")
        # Attempt initial connection
        await asyncio.to_thread(self._connect)

        while True:
            cmd_type, payload = await self.queue.get()
            
            try:
                if not self.is_connected:
                    await asyncio.to_thread(self._connect)

                if self.is_connected:
                    logger.info(f"Executing Command: {cmd_type} -> {payload}")
                    
                    # NOTE: Here is where the actual gphoto2 config setting happens.
                    # We wrap it in to_thread because gphoto2 calls are synchronous and block.
                    if cmd_type == "record":
                        # e.g., self.camera.trigger_capture(self.context)
                        pass
                    elif cmd_type in ["iso", "shutterspeed", "aperture"]:
                        # config = self.camera.get_config(self.context)
                        # child = config.get_child_by_name(cmd_type)
                        # child.set_value(payload)
                        # self.camera.set_config(config, self.context)
                        pass
                else:
                    logger.warning(f"Skipped {cmd_type}: Camera not connected.")

            except Exception as e:
                logger.error(f"Error executing {cmd_type}: {e}")
                self.is_connected = False # Force reconnect next time
            finally:
                self.queue.task_done()

cam_manager = CameraManager()
camera_worker_task: asyncio.Task | None = None

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
    if stream_task:
        stream_task.cancel()
    if camera_worker_task:
        camera_worker_task.cancel()
    if ffmpeg_process is not None and ffmpeg_process.poll() is None:
        ffmpeg_process.terminate()
        ffmpeg_process.wait(timeout=2)

@app.websocket("/ws/video")
async def websocket_video(ws: WebSocket) -> None:
    await ws.accept()
    clients.add(ws)
    try:
        while True:
            await ws.receive()
    except Exception:
        clients.discard(ws)

# --- PHASE 2 CONTROL ENDPOINTS ---
@app.post("/api/camera/iso")
async def set_iso(req: CameraCommand):
    await cam_manager.queue.put(("iso", req.value))
    return {"status": "queued", "command": "iso", "value": req.value}

@app.post("/api/camera/shutter")
async def set_shutter(req: CameraCommand):
    await cam_manager.queue.put(("shutterspeed", req.value))
    return {"status": "queued", "command": "shutterspeed", "value": req.value}

@app.post("/api/camera/aperture")
async def set_aperture(req: CameraCommand):
    await cam_manager.queue.put(("aperture", req.value))
    return {"status": "queued", "command": "aperture", "value": req.value}

@app.post("/api/camera/record")
async def toggle_record(req: CameraCommand):
    await cam_manager.queue.put(("record", req.value))
    return {"status": "queued", "command": "record", "value": req.value}