"""On-device vision: object detection (RF-DETR, COCO classes) and license plate reading (FastALPR).

Everything runs locally on the CPU through ONNX Runtime. No images leave the machine.
Plates are read on vehicle crops instead of the whole frame, because a plate is only
a few dozen pixels wide in a 2560x1440 driveway shot.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import KnownPlate, normalize_plate

VEHICLES = {"car", "truck", "bus", "motorcycle"}
CPU = ["CPUExecutionProvider"]


@dataclass
class Box:
    x1: int
    y1: int
    x2: int
    y2: int


@dataclass
class ObjectHit:
    label: str
    confidence: float
    box: Box


@dataclass
class PlateHit:
    text: str
    confidence: float
    box: Box
    known_name: str | None = None


@dataclass
class Analysis:
    objects: list[ObjectHit] = field(default_factory=list)
    plates: list[PlateHit] = field(default_factory=list)

    @property
    def labels(self) -> list[str]:
        return sorted({o.label for o in self.objects})


def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def match_plate(text: str, known: list[KnownPlate], max_distance: int = 1) -> str | None:
    """Return the owner name of the closest known plate, allowing one misread character."""
    text = normalize_plate(text)
    if len(text) < 4:
        return None
    best = min(known, key=lambda k: levenshtein(text, k.plate), default=None)
    if best and levenshtein(text, best.plate) <= max_distance:
        return best.name
    return None


class Vision:
    def __init__(self, labels: list[str], min_confidence: float = 0.5, read_plates: bool = True):
        from open_image_models import create_detector

        self.labels = set(labels)
        self.min_confidence = min_confidence
        self.detector = create_detector("rf-detr-nano-384-coco", providers=CPU)
        self.alpr = None
        if read_plates:
            from fast_alpr import ALPR

            self.alpr = ALPR(
                detector_model="yolo-v9-t-640-license-plate-end2end",
                detector_providers=CPU,
                detector_conf_thresh=0.3,
                ocr_model="cct-s-v2-global-model",
                ocr_device="cpu",
            )

    def detect(self, img: np.ndarray) -> list[ObjectHit]:
        hits = []
        for d in self.detector.predict(img):
            if d.label in self.labels and d.confidence >= self.min_confidence:
                b = d.bounding_box
                hits.append(ObjectHit(d.label, round(float(d.confidence), 3), Box(int(b.x1), int(b.y1), int(b.x2), int(b.y2))))
        return hits

    def read_plates(self, img: np.ndarray, vehicles: list[ObjectHit], known: list[KnownPlate]) -> list[PlateHit]:
        if self.alpr is None:
            return []
        h, w = img.shape[:2]
        found: dict[str, PlateHit] = {}
        for v in vehicles:
            mx, my = int((v.box.x2 - v.box.x1) * 0.1), int((v.box.y2 - v.box.y1) * 0.1)
            x1, y1 = max(0, v.box.x1 - mx), max(0, v.box.y1 - my)
            x2, y2 = min(w, v.box.x2 + mx), min(h, v.box.y2 + my)
            for r in self.alpr.predict(img[y1:y2, x1:x2]):
                if not r.ocr or not r.ocr.text:
                    continue
                text = normalize_plate(r.ocr.text)
                if len(text) < 4:
                    continue
                conf = r.ocr.confidence
                conf = float(np.mean(conf)) if isinstance(conf, (list, tuple, np.ndarray)) else float(conf)
                b = r.detection.bounding_box
                hit = PlateHit(text, round(conf, 3), Box(x1 + int(b.x1), y1 + int(b.y1), x1 + int(b.x2), y1 + int(b.y2)),
                               known_name=match_plate(text, known))
                if text not in found or found[text].confidence < hit.confidence:
                    found[text] = hit
        return list(found.values())

    def analyze(self, img: np.ndarray, known: list[KnownPlate], plates: bool = True) -> Analysis:
        objects = self.detect(img)
        vehicles = [o for o in objects if o.label in VEHICLES]
        return Analysis(objects, self.read_plates(img, vehicles, known) if plates and vehicles else [])
