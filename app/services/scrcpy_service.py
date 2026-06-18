"""
Scrcpy streaming: send_frame_meta=true, parse complete H.264 frames.

Root cause of black screen:
  send_frame_meta=false → raw H.264 Annex B byte stream over ADB forward tunnel.
  TCP is a stream protocol — recv() returns arbitrary byte boundaries.
  Backend forwarded arbitrary-sized chunks (262144 bytes each) to WebSocket.
  Frontend VideoDecoder.decode() received truncated NAL units → no output → black.

Fix:
  send_frame_meta=true → scrcpy-server prefixes each frame with 12B meta header:
    [8B PTS (uint64 BE)] [4B frame_size (uint32 BE)] [frame_size bytes H.264 data]
  Backend reads meta header, parses frame_size, reads exact frame, forwards it
  as a single WebSocket binary message. Each WS message = one complete H.264 AU.
"""

import asyncio
import json
import logging
import socket
import struct
from pathlib import Path
from typing import Optional

import adbutils
from starlette.websockets import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

_JAR_DIR = Path(__file__).resolve().parent.parent / "binaries"
_JAR_V2 = _JAR_DIR / "scrcpy-server-v2.7.jar"

# Scrcpy wire protocol (send_frame_meta=true):
#   frame_meta = struct.pack('>QI', pts, frame_size)  → 12 bytes
#   frame_data = raw H.264 Annex B byte stream          → frame_size bytes
_FRAME_META_STRUCT = struct.Struct(">QI")   # PTS(8) + size(4) = 12


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly *n* bytes from a (blocking) socket."""
    buf = bytearray(n)
    view = memoryview(buf)
    while view:
        received = sock.recv_into(view, len(view))
        if not received:
            raise ConnectionError(
                f"scrcpy: EOF after {n - len(view)}/{n} bytes"
            )
        view = view[received:]
    return bytes(buf)


class ScrcpySession:
    """Scrcpy session — reads complete H.264 frames via 12B meta headers."""

    def __init__(self, serial: str):
        self.serial = serial
        self._dev: Optional[adbutils.AdbDevice] = None
        self._vs: Optional[socket.socket] = None   # video socket
        self._cs: Optional[socket.socket] = None   # control socket
        self._shell: Optional[adbutils.AdbConnection] = None
        self.width = self.height = 0
        self._started = self._closed = False

    @property
    def is_running(self) -> bool:
        return self._started and not self._closed

    async def start(self):
        """Push scrcpy-server.jar, start server, connect sockets, read preamble."""
        if self._started:
            return
        if not _JAR_V2.exists():
            raise FileNotFoundError(f"scrcpy-server.jar not found: {_JAR_V2}")

        try:
            self._dev = adbutils.adb.device(serial=self.serial)
        except adbutils.AdbError as e:
            raise RuntimeError(f"Device {self.serial} not found: {e}")

        # Push JAR to device
        logger.info("[Scrcpy] Pushing JAR to %s", self.serial)
        self._dev.sync.push(
            str(_JAR_V2),
            "/data/local/tmp/scrcpy_server.jar",
            check=True,
        )

        # Start scrcpy-server with send_frame_meta=true
        cmd = " ".join([
            "CLASSPATH=/data/local/tmp/scrcpy_server.jar",
            "app_process", "/", "com.genymobile.scrcpy.Server", "2.7",
            "log_level=info", "max_size=1280", "max_fps=30",
            "video_bit_rate=4000000",
            "tunnel_forward=true",
            "send_frame_meta=true",          # ← CRITICAL: adds 12B meta per frame
            "control=true",
            "audio=false", "show_touches=false", "stay_awake=false",
            "power_off_on_close=false", "clipboard_autosync=false",
        ])
        self._shell = await asyncio.get_running_loop().run_in_executor(
            None, lambda: self._dev.shell(cmd, stream=True),
        )
        await asyncio.sleep(2.0)

        # Connect video & control abstract sockets
        self._vs = await self._connect()
        self._cs = await self._connect()

        # Read preamble: [dummy:1][device_name:64][codec_info:4][width:4][height:4]
        def _preamble():
            s = self._vs
            dummy = _recv_exact(s, 1)
            if dummy != b"\x00":
                raise ConnectionError(f"bad dummy byte: {dummy.hex()}")
            _recv_exact(s, 64)   # device name (null-padded)
            _recv_exact(s, 4)    # codec info (leftover)
            r = _recv_exact(s, 8)
            self.width, self.height = struct.unpack(">II", r)

        await asyncio.get_running_loop().run_in_executor(None, _preamble)
        self._started = True
        logger.info(
            "[Scrcpy] Started %s  %dx%d  send_frame_meta=true",
            self.serial, self.width, self.height,
        )

    async def stream_to_websocket(self, ws: WebSocket):
        """Run video sender + control receiver concurrently."""
        v = asyncio.create_task(self._send_video(ws))
        c = asyncio.create_task(self._recv_ctrl(ws))
        try:
            done, pending = await asyncio.wait(
                [v, c], return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for t in (v, c):
                if not t.done():
                    t.cancel()
            await asyncio.gather(
                *[t for t in (v, c) if not t.done()],
                return_exceptions=True,
            )

    async def close(self):
        """Close sockets and shell connection."""
        if self._closed:
            return
        self._closed = True
        self._started = False

        def _cleanup():
            for sock in (self._cs, self._vs):
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass
            if self._shell:
                try:
                    self._shell.close()
                except Exception:
                    pass

        await asyncio.get_running_loop().run_in_executor(None, _cleanup)
        logger.info("[Scrcpy] Closed %s", self.serial)

    # ---- Internal helpers ---------------------------------------------------

    async def _connect(self) -> socket.socket:
        """Retry ADB abstract socket connection to scrcpy."""
        import retry
        from adbutils import AdbError

        def _do():
            @retry.retry(exceptions=AdbError, tries=30, delay=0.1)
            def _inner():
                return self._dev.create_connection(
                    adbutils.Network.LOCAL_ABSTRACT, "scrcpy",
                )
            return _inner()

        return await asyncio.get_running_loop().run_in_executor(None, _do)

    # ---- Video streaming (THE CORE FIX) -------------------------------------

    async def _send_video(self, ws: WebSocket):
        """
        Read scrcpy frames via send_frame_meta protocol.

        Each wire frame:
            [8B PTS (uint64 BE)] [4B frame_size (uint32 BE)] [N bytes H.264]

        Each WebSocket binary message = one complete H.264 access unit.
        """
        if not self._vs:
            return
        loop = asyncio.get_running_loop()

        # Tell frontend resolution and signal new protocol
        await ws.send_text(json.dumps({
            "type": "scrcpy_meta",
            "width": self.width,
            "height": self.height,
            "version": "2.7",
        }))

        while not self._closed:
            try:
                if ws.client_state.name != "CONNECTED":
                    break

                # ---- 1. Read 12B meta header (blocking IO in executor) ----
                meta = await loop.run_in_executor(None, _recv_exact, self._vs, 12)
                pts, frame_size = _FRAME_META_STRUCT.unpack(meta)

                # Sanity: max 4 MB per frame
                if frame_size == 0 or frame_size > 4 * 1024 * 1024:
                    logger.warning(
                        "[Scrcpy] skip bad frame  pts=%d  size=%d",
                        pts, frame_size,
                    )
                    continue

                # ---- 2. Read complete H.264 frame ----
                frame_data = await loop.run_in_executor(
                    None, _recv_exact, self._vs, frame_size,
                )

                # ---- 3. Forward as single WS binary message ----
                await ws.send_bytes(frame_data)

            except (ConnectionError, OSError) as exc:
                logger.debug("[Scrcpy] Video loop ended: %s", exc)
                break

    # ---- Control relay (touch / key / text) ---------------------------------

    async def _recv_ctrl(self, ws: WebSocket):
        """Relay JSON control messages from frontend to scrcpy control socket."""
        if not self._cs:
            return

        # TouchEvent  (scrcpy v2.x, big-endian): type+action+pointerId+x+y+w+h+pressure+...
        TF = struct.Struct("!BBQIIHHHII")   # 2 + 1 + 8 + 4 + 4 + 2 + 2 + 2 + 4 + 4 = 33 B
        # KeyEvent: type+action+keycode+repeat+metastate
        KF = struct.Struct("!BBIII")        # 2 + 4 + 4 + 4 = 14 B

        def _send_raw(b: bytes):
            try:
                self._cs.sendall(b)
            except OSError:
                pass

        while not self._closed:
            try:
                msg = json.loads(await ws.receive_text())
                t = msg.get("type")

                if t == "touchDown":
                    x = max(0, min(int(msg["x"] * self.width), self.width))
                    y = max(0, min(int(msg["y"] * self.height), self.height))
                    _send_raw(TF.pack(2, 0, 1, x, y, self.width, self.height, 0xFFFF, 1, 1))

                elif t == "touchUp":
                    _send_raw(TF.pack(2, 1, 1, 0, 0, self.width, self.height, 0xFFFF, 1, 1))

                elif t == "touchMove":
                    x = max(0, min(int(msg["x"] * self.width), self.width))
                    y = max(0, min(int(msg["y"] * self.height), self.height))
                    _send_raw(TF.pack(2, 2, 1, x, y, self.width, self.height, 0xFFFF, 1, 1))

                elif t == "keyEvent":
                    _send_raw(KF.pack(0, msg.get("action", 0), msg.get("keycode", 0), 0, 0))

                elif t == "text":
                    tb = msg.get("text", "").encode("utf-8")
                    _send_raw(struct.pack("!BI", 1, len(tb)) + tb)

                elif t == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))

                elif t == "stop":
                    break

            except (WebSocketDisconnect, json.JSONDecodeError):
                break


# ======================================================================
# ScrcpyService — session lifecycle management
# ======================================================================

class ScrcpyService:
    """Manage ScrcpySession instances per device serial."""

    def __init__(self):
        self._sessions: dict[str, ScrcpySession] = {}
        self._lock = asyncio.Lock()

    async def get_or_create_session(self, serial: str) -> ScrcpySession:
        """Return existing running session, or create a new one."""
        async with self._lock:
            s = self._sessions.get(serial)
            if s and s.is_running:
                return s
            s = ScrcpySession(serial)
            await s.start()
            self._sessions[serial] = s
            return s

    async def close_session(self, serial: str):
        """Close and remove the session for *serial*."""
        async with self._lock:
            s = self._sessions.pop(serial, None)
            if s:
                await s.close()

    async def close_all(self):
        """Close every active session."""
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            for s in sessions:
                await s.close()

    def list_active_sessions(self) -> list[dict]:
        return [
            {
                "serial": s.serial,
                "width": s.width,
                "height": s.height,
                "version": "2.7",
            }
            for s in self._sessions.values()
            if s.is_running
        ]


scrcpy_service = ScrcpyService()
