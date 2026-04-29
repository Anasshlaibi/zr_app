import asyncio
import os
import subprocess
import socket
from typing import Set
import logging

from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import gphoto2 as gp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="ZineControl Pro Bridge")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

video_clients: Set[WebSocket] = set()
event_clients: Set[WebSocket] = set()
ffmpeg_process: subprocess.Popen | None = None

class CameraProp(BaseModel):
    value: str

class ConnectionRequest(BaseModel):
    ip_address: str | None = None

class CameraManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(CameraManager, cls).__new__(cls)
            cls._instance.camera = None
            cls._instance.context = gp.Context()
            cls._instance.queue = asyncio.Queue()
            cls._instance.is_connected = False
            cls._instance.preview_task = None
        return cls._instance

    def _get_props(self):
        if not self.camera: return {}
        props = {}
        config = self.camera.get_config(self.context)
        for p in ["iso", "shutterspeed", "aperture", "whitebalance"]:
            try:
                props[p] = config.get_child_by_name(p).get_value()
            except: props[p] = "—"
        return props

    def connect_wifi(self, ip):
        self.camera = gp.Camera()
        ports = gp.PortInfoList()
        ports.load()
        idx = ports.lookup_path(f"ptpip:{ip}")
        if idx < 0: raise Exception("Camera not found on network")
        self.camera.set_port_info(ports[idx])
        self.camera.init(self.context)
        self.is_connected = True
        self.preview_task = asyncio.create_task(self.preview_loop())

    async def preview_loop(self):
        while self.is_connected:
            try:
                preview = await asyncio.to_thread(self.camera.capture_preview, self.context)
                data = await asyncio.to_thread(preview.get_raw_data)
                if ffmpeg_process and ffmpeg_process.stdin:
                    ffmpeg_process.stdin.write(data)
                    ffmpeg_process.stdin.flush()
            except: pass
            await asyncio.sleep(0.03)

cam_manager = CameraManager()

def start_ffmpeg():
    return subprocess.Popen([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "image2pipe", "-vcodec", "mjpeg", "-i", "-",
        "-c:v", "mpeg1video", "-f", "mpegts", "-tune", "zerolatency", "-preset", "ultrafast", "-"
    ], stdin=subprocess.PIPE, stdout=subprocess.PIPE)

async def stream_worker():
    global ffmpeg_process
    while True:
        if not ffmpeg_process or ffmpeg_process.poll() is not None:
            ffmpeg_process = start_ffmpeg()
        chunk = await asyncio.to_thread(ffmpeg_process.stdout.read, 1316)
        if chunk and video_clients:
            await asyncio.gather(*(ws.send_bytes(chunk) for ws in video_clients), return_exceptions=True)

@app.on_event("startup")
async def startup():
    asyncio.create_task(stream_worker())

@app.websocket("/ws/video")
async def ws_video(ws: WebSocket):
    await ws.accept()
    video_clients.add(ws)
    try:
        while True: await ws.receive()
    except: video_clients.discard(ws)

@app.get("/api/camera/props")
async def get_props():
    return cam_manager._get_props()

@app.post("/api/connect/wifi")
async def connect_wifi(req: ConnectionRequest):
    await asyncio.to_thread(cam_manager.connect_wifi, req.ip_address)
    return {"status": "connected", "props": cam_manager._get_props()}
