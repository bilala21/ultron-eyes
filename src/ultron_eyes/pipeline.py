"""Motion -> snapshot -> AI -> stored event.

Motion comes from Home Assistant's camera motion sensors over the HA WebSocket API.
Each camera has a cooldown so one person walking around doesn't create 50 events.
Work happens on a single background thread so the PC stays responsive (the models
are small and only run when something moves).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import threading
import time

import websockets

from .config import Config
from .nvr import NVR
from .store import Store
from .vision import Vision

log = logging.getLogger("ultron_eyes")

# Things that move matter more than things that are always there (parked cars, bikes).
PRIORITY = {"person": 100, "dog": 30, "cat": 30, "motorcycle": 5, "bicycle": 2}


def frame_score(a) -> tuple:
    """Rank burst frames: people/animals first, then readable plates, then more objects, then confidence."""
    return (
        sum(PRIORITY.get(o.label, 0) for o in a.objects),
        len(a.plates),
        len(a.objects),
        max((o.confidence for o in a.objects), default=0.0),
    )


class Pipeline:
    def __init__(self, cfg: Config, store: Store, vision: Vision | None = None, nvr: NVR | None = None):
        self.cfg = cfg
        self.store = store
        self.vision = vision or Vision(cfg.labels, cfg.min_confidence, read_plates=any(c.plates for c in cfg.cameras))
        self.nvr = nvr or NVR(cfg.nvr_host, cfg.nvr_user, cfg.nvr_password, cfg.nvr_port)
        self.jobs: queue.Queue[tuple[int, float]] = queue.Queue(maxsize=50)
        self.last_trigger: dict[int, float] = {}
        self.listeners = []  # callables(event_dict) for live updates / notifications
        self._lower_priority()

    @staticmethod
    def _lower_priority() -> None:
        """Keep games and other apps on the shared PC smooth."""
        try:
            if os.name == "nt":
                import ctypes
                ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)  # BELOW_NORMAL
            else:
                os.nice(10)
        except Exception:  # not fatal
            pass

    # ---- triggers ----
    def trigger(self, channel: int, ts: float | None = None) -> bool:
        ts = ts or time.time()
        if ts - self.last_trigger.get(channel, 0) < self.cfg.cooldown_seconds:
            return False
        self.last_trigger[channel] = ts
        try:
            self.jobs.put_nowait((channel, ts))
            return True
        except queue.Full:
            log.warning("queue full, dropping motion on channel %s", channel)
            return False

    # ---- worker ----
    def process(self, channel: int, ts: float) -> dict | None:
        cam = self.cfg.camera(channel)
        name = cam.name if cam else f"Cam {channel}"
        jpeg, img, analysis = self.best_frame(channel, plates=bool(cam and cam.plates))
        if not analysis.objects:
            log.info("cam %s: motion but nothing recognized", channel)
            return None
        latency = int((time.time() - ts) * 1000)
        event_id = self.store.add(ts, channel, name, jpeg, img, analysis, latency)
        event = self.store.get(event_id)
        log.info("cam %s (%s): %s plates=%s in %d ms", channel, name, analysis.labels,
                 [(p.text, p.known_name) for p in analysis.plates], latency)
        for fn in self.listeners:
            try:
                fn(event)
            except Exception:
                log.exception("listener failed")
        return event

    def best_frame(self, channel: int, plates: bool):
        """Motion alerts arrive a moment after the motion starts, and a full-resolution
        snapshot takes ~1.5 s, so a single frame often misses whoever caused the motion.
        Grab a short burst and keep the most informative frame."""
        best = None
        for i in range(max(1, self.cfg.burst_frames)):
            try:
                jpeg, img = self.nvr.snapshot(channel)
            except Exception as e:
                if best is None and i == self.cfg.burst_frames - 1:
                    raise
                log.debug("cam %s burst frame %d failed: %s", channel, i, e)
                continue
            analysis = self.vision.analyze(img, self.cfg.known_plates, plates=plates)
            if best is None or frame_score(analysis) > frame_score(best[2]):
                best = (jpeg, img, analysis)
            if "person" in analysis.labels and analysis.plates:
                break  # can't do better than this
        if best is None:
            raise RuntimeError(f"cam {channel}: no snapshot")
        return best

    def worker(self, stop: threading.Event) -> None:
        last_purge = 0.0
        while not stop.is_set():
            if time.time() - last_purge > 3600:
                removed = self.store.purge(self.cfg.retention_days)
                if removed:
                    log.info("retention: deleted %d events older than %d days", removed, self.cfg.retention_days)
                last_purge = time.time()
            try:
                channel, ts = self.jobs.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self.process(channel, ts)
            except Exception as e:
                log.warning("cam %s: failed (%s: %s)", channel, type(e).__name__, e)

    # ---- Home Assistant motion listener ----
    async def listen_home_assistant(self, stop: threading.Event) -> None:
        if not self.cfg.ha_url or not self.cfg.ha_token:
            log.warning("Home Assistant not configured; only manual triggers will work")
            return
        entity_to_channel = {c.motion_entity: c.channel for c in self.cfg.cameras if c.motion_entity}
        url = self.cfg.ha_url.rstrip("/").replace("http", "ws", 1) + "/api/websocket"
        while not stop.is_set():
            try:
                async with websockets.connect(url, max_size=2**24) as ws:
                    await ws.recv()  # auth_required
                    await ws.send(json.dumps({"type": "auth", "access_token": self.cfg.ha_token}))
                    if json.loads(await ws.recv()).get("type") != "auth_ok":
                        log.error("Home Assistant rejected the token")
                        return
                    await ws.send(json.dumps({
                        "id": 1, "type": "subscribe_trigger",
                        "trigger": {"platform": "state", "entity_id": list(entity_to_channel), "to": "on"},
                    }))
                    log.info("listening to %d motion sensors", len(entity_to_channel))
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("type") != "event":
                            continue
                        entity = msg["event"]["variables"]["trigger"]["entity_id"]
                        if entity in entity_to_channel:
                            self.trigger(entity_to_channel[entity])
                        if stop.is_set():
                            break
            except Exception as e:
                log.warning("Home Assistant connection lost (%s); retrying in 10 s", type(e).__name__)
                await asyncio.sleep(10)
