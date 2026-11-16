"""Hikvision NVR client (ISAPI). Only reads snapshots; never changes NVR settings."""

from __future__ import annotations

import httpx
import numpy as np
import cv2


class NVR:
    def __init__(self, host: str, user: str, password: str, port: int = 80, timeout: float = 20.0):
        self.base = f"http://{host}:{port}"
        self.client = httpx.Client(auth=httpx.DigestAuth(user, password), timeout=timeout)

    def snapshot_jpeg(self, channel: int, width: int = 2560, height: int = 1440) -> bytes:
        """Full-resolution JPEG from the main stream of a channel (channel 3 -> stream 301)."""
        r = self.client.get(
            f"{self.base}/ISAPI/Streaming/channels/{channel}01/picture",
            params={"videoResolutionWidth": width, "videoResolutionHeight": height},
        )
        r.raise_for_status()
        if not r.content.startswith(b"\xff\xd8"):
            raise RuntimeError(f"channel {channel} did not return a JPEG")
        return r.content

    def snapshot(self, channel: int) -> tuple[bytes, np.ndarray]:
        jpeg = self.snapshot_jpeg(channel)
        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError(f"channel {channel} snapshot could not be decoded")
        return jpeg, img

    def close(self) -> None:
        self.client.close()
