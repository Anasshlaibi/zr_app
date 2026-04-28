import asyncio
import os
import subprocess
from typing import Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="ZineControl Web Bridge")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

clients: Set[WebSocket] = set()
stream_task: asyncio.Task | None = None
ffmpeg_process: subprocess.Popen[bytes] | None = None

RTSP_URL = os.getenv("NIKON_RTSP_URL", "").strip()
TARGET_SIZE = os.getenv("STREAM_SIZE", "1280x720")
TARGET_FPS = os.getenv("STREAM_FPS", "30")


def ffmpeg_command() -> list[str]:
    if RTSP_URL:
        source_args = [
            "-rtsp_transport",
            "tcp",
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-i",
            RTSP_URL,
        ]
    else:
        source_args = [
            "-re",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={TARGET_SIZE}:rate={TARGET_FPS}",
        ]

    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        *source_args,
        "-an",
        "-c:v",
        "mpeg1video",
        "-bf",
        "0",
        "-g",
        "1",
        "-tune",
        "zerolatency",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-r",
        TARGET_FPS,
        "-s",
        TARGET_SIZE,
        "-f",
        "mpegts",
        "-",
    ]


def start_ffmpeg() -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        ffmpeg_command(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )


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
        results = await asyncio.gather(
            *(ws.send_bytes(chunk) for ws in targets),
            return_exceptions=True,
        )
        for ws, result in zip(targets, results):
            if isinstance(result, Exception):
                dead_clients.add(ws)
        for ws in dead_clients:
            clients.discard(ws)


@app.on_event("startup")
async def startup_event() -> None:
    global stream_task
    stream_task = asyncio.create_task(stream_mpegts())


@app.on_event("shutdown")
async def shutdown_event() -> None:
    global stream_task, ffmpeg_process

    if stream_task is not None:
        stream_task.cancel()
        try:
            await stream_task
        except asyncio.CancelledError:
            pass

    if ffmpeg_process is not None and ffmpeg_process.poll() is None:
        ffmpeg_process.terminate()
        ffmpeg_process.wait(timeout=2)


@app.get("/health")
async def health() -> dict:
    source = RTSP_URL if RTSP_URL else "ffmpeg-testsrc"
    ffmpeg_up = ffmpeg_process is not None and ffmpeg_process.poll() is None
    return {"status": "ok", "clients": len(clients), "source": source, "ffmpeg_up": ffmpeg_up}


@app.websocket("/ws/video")
async def websocket_video(ws: WebSocket) -> None:
    await ws.accept()
    clients.add(ws)
    try:
        while True:
            await ws.receive()
    except WebSocketDisconnect:
        clients.discard(ws)
    except Exception:
        clients.discard(ws)
