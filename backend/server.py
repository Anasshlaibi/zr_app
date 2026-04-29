import asyncio
import os
import subprocess
import threading
import logging
import socket
import time
import json
from typing import Set
from fastapi import FastAPI, WebSocket, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

try:
    import gphoto2 as gp
    GPHOTO2_AVAILABLE = True
except ImportError:
    GPHOTO2_AVAILABLE = False
    logger.error("gphoto2 NOT INSTALLED. System will fail to connect.")

app = FastAPI(title="ZineControl Web - GPhoto2 Core")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

class CameraState:
    def __init__(self):
        self.camera = None
        self.context = gp.Context() if GPHOTO2_AVAILABLE else None
        self.lock = threading.Lock()
        self.connected = False
        self.type = None
        self.props = {}

    def _get_current_props(self):
        if not self.camera: return {}
        res = {}
        config = self.camera.get_config()
        for name in ["iso", "shutterspeed", "aperture", "whitebalance"]:
            try:
                child = gp.check_result(gp.gp_widget_get_child_by_name(config, name))
                res[name] = str(gp.check_result(gp.gp_widget_get_value(child)))
            except: pass
        self.props = res
        return res

    def set_prop(self, name: str, value: str):
        with self.lock:
            config = self.camera.get_config()
            child = gp.check_result(gp.gp_widget_get_child_by_name(config, name))
            gp.check_result(gp.gp_widget_set_value(child, value))
            self.camera.set_config(config)
            self.props[name] = value

    def get_choices(self, name: str):
        try:
            config = self.camera.get_config()
            child = gp.check_result(gp.gp_widget_get_child_by_name(config, name))
            count = gp.check_result(gp.gp_widget_count_choices(child))
            return [str(gp.check_result(gp.gp_widget_get_choice(child, i))) for i in range(count)]
        except: return []

    def connect_usb(self):
        with self.lock:
            self.camera = gp.Camera()
            self.camera.init()
            self.connected = True
            self.type = "USB-C"
            self._get_current_props()

    def connect_wifi(self, ip: str):
        with self.lock:
            port_info_list = gp.PortInfoList()
            port_info_list.load()
            idx = port_info_list.lookup_path(f"ptpip:{ip}")
            self.camera = gp.Camera()
            self.camera.set_port_info(port_info_list[idx])
            self.camera.init()
            self.connected = True
            self.type = "Wi-Fi"
            self._get_current_props()

    def disconnect(self):
        with self.lock:
            if self.camera: self.camera.exit()
            self.camera = None
            self.connected = False
            self.type = None

state = CameraState()
video_clients: Set[WebSocket] = set()
event_clients: Set[WebSocket] = set()

def discover_camera_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(2.0)
    msg = "M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 2\r\nST: ssdp:all\r\n\r\n"
    try:
        s.sendto(msg.encode(), ("239.255.255.250", 1900))
        while True:
            data, addr = s.recvfrom(2048)
            if b"Nikon" in data or b"PTP" in data: return addr[0]
    except: return None

async def capture_preview_loop():
    ffmpeg_cmd = [
        "ffmpeg", "-f", "image2pipe", "-i", "pipe:0", "-c:v", "mpeg1video",
        "-b:v", "2000k", "-bf", "0", "-f", "mpegts", "pipe:1"
    ]
    proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    
    while True:
        if state.connected:
            try:
                frame = await asyncio.to_thread(state.camera.capture_preview)
                data = await asyncio.to_thread(frame.get_raw_data)
                proc.stdin.write(data)
                chunk = await asyncio.to_thread(proc.stdout.read, 1024)
                if video_clients:
                    await asyncio.gather(*(ws.send_bytes(chunk) for ws in video_clients), return_exceptions=True)
            except Exception as e:
                logger.warning(f"Preview error: {e}")
                await asyncio.sleep(0.1)
        else:
            await asyncio.sleep(0.5)

@app.on_event("startup")
async def startup():
    asyncio.create_task(capture_preview_loop())

@app.websocket("/ws/video")
async def ws_video(ws: WebSocket):
    await ws.accept()
    video_clients.add(ws)
    try:
        while True: await ws.receive_text()
    except: video_clients.remove(ws)

@app.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    await ws.accept()
    event_clients.add(ws)
    await ws.send_json({"event": "status", "connected": state.connected, "type": state.type, "props": state.props})
    try:
        while True: await ws.receive_text()
    except: event_clients.remove(ws)

@app.get("/api/camera/props")
async def get_props():
    return state._get_current_props()

@app.get("/api/camera/choices/{prop}")
async def get_choices(prop: str):
    return {"choices": state.get_choices(prop)}

@app.post("/api/camera/prop/{prop}")
async def set_prop(prop: str, req: dict):
    state.set_prop(prop, req["value"])
    return {"status": "ok"}

@app.post("/api/connect/usb")
async def conn_usb():
    try:
        state.connect_usb()
        return {"status": "connected"}
    except Exception as e: raise HTTPException(400, str(e))

@app.get("/api/discover")
async def discover():
    ip = await asyncio.to_thread(discover_camera_ip)
    if not ip: raise HTTPException(404, "Not found")
    return {"ip": ip}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
