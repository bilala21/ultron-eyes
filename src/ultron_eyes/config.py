"""Configuration loading.

Settings live in a YAML file (config.yaml, never committed). Secrets can be
given inline or, better, through environment variables so the file itself can
be shared safely.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Camera:
    channel: int
    name: str
    motion_entity: str | None = None  # Home Assistant motion sensor that triggers this camera
    plates: bool = False  # run license plate reading on cars seen by this camera


@dataclass
class KnownPlate:
    plate: str
    name: str


@dataclass
class Config:
    nvr_host: str
    nvr_user: str
    nvr_password: str
    nvr_port: int = 80
    ha_url: str | None = None
    ha_token: str | None = None
    cameras: list[Camera] = field(default_factory=list)
    known_plates: list[KnownPlate] = field(default_factory=list)
    data_dir: Path = Path("data")
    retention_days: int = 14
    cooldown_seconds: int = 20
    burst_frames: int = 3  # snapshots per motion event; the best one is kept
    active_interval_seconds: float = 4  # re-check a camera this often while its motion sensor stays on
    max_active_seconds: int = 180  # stop re-checking after this (sensor stuck on)
    min_confidence: float = 0.5
    labels: list[str] = field(default_factory=lambda: [
        "person", "car", "truck", "bus", "motorcycle", "bicycle", "dog", "cat", "backpack", "handbag", "suitcase",
    ])
    gemini_api_key: str | None = None
    web_host: str = "127.0.0.1"
    web_port: int = 8765
    web_token: str | None = None  # required when the web UI listens on the LAN
    web_allowed_hosts: list[str] = field(default_factory=list)  # extra Host names, e.g. the PC's LAN IP
    gemini_model: str = "gemini-2.5-flash"

    def camera(self, channel: int) -> Camera | None:
        return next((c for c in self.cameras if c.channel == channel), None)


def _env_or(value, env_name):
    return os.environ.get(env_name) or value


def load(path: str | os.PathLike = "config.yaml") -> Config:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    nvr = raw.get("nvr", {})
    ha = raw.get("home_assistant", {}) or {}
    cfg = Config(
        nvr_host=nvr["host"],
        nvr_port=int(nvr.get("port", 80)),
        nvr_user=_env_or(nvr.get("username"), "ULTRON_NVR_USER"),
        nvr_password=_env_or(nvr.get("password"), "ULTRON_NVR_PASSWORD"),
        ha_url=ha.get("url"),
        ha_token=_env_or(ha.get("token"), "ULTRON_HA_TOKEN"),
        cameras=[Camera(**c) for c in raw.get("cameras", [])],
        known_plates=[KnownPlate(plate=normalize_plate(k["plate"]), name=k["name"]) for k in raw.get("known_plates", [])],
        data_dir=Path(raw.get("data_dir", "data")),
        retention_days=int(raw.get("retention_days", 14)),
        cooldown_seconds=int(raw.get("cooldown_seconds", 20)),
        burst_frames=int(raw.get("burst_frames", 3)),
        active_interval_seconds=float(raw.get("active_interval_seconds", 4)),
        max_active_seconds=int(raw.get("max_active_seconds", 180)),
        min_confidence=float(raw.get("min_confidence", 0.5)),
        gemini_api_key=_env_or(raw.get("gemini_api_key"), "GEMINI_API_KEY"),
        web_host=raw.get("web", {}).get("host", "127.0.0.1"),
        web_port=int(raw.get("web", {}).get("port", 8765)),
        web_token=_env_or(raw.get("web", {}).get("token"), "ULTRON_WEB_TOKEN"),
        web_allowed_hosts=list(raw.get("web", {}).get("allowed_hosts", [])),
        gemini_model=raw.get("gemini_model", "gemini-2.5-flash"),
    )
    if "labels" in raw:
        cfg.labels = list(raw["labels"])
    if not cfg.nvr_user or not cfg.nvr_password:
        raise ValueError("NVR username/password missing (set in config.yaml or ULTRON_NVR_USER / ULTRON_NVR_PASSWORD)")
    if cfg.web_host not in ("127.0.0.1", "localhost") and not cfg.web_token:
        raise ValueError("web.token is required when web.host is not 127.0.0.1")
    return cfg


def normalize_plate(text: str) -> str:
    """Uppercase and strip everything except letters and digits ("abc 123-4" -> "ABC1234")."""
    return "".join(ch for ch in text.upper() if ch.isalnum())
