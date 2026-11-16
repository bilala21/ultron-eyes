"""Turn plain-English questions into event filters.

The rule-based parser runs fully offline and handles the common questions
("people at the front door last night", "when was dad's car here today",
"unknown cars this week", "dogs in the backyard after 9pm"). If it cannot make
sense of a question and a Gemini API key is configured, the question TEXT ONLY
is sent to Gemini to be converted into the same filter format. Images are never sent.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .config import Camera, KnownPlate
from .store import Filter

LABEL_WORDS = {
    "person": ["person", "people", "someone", "somebody", "anyone", "anybody", "man", "men", "woman", "women",
               "kid", "kids", "child", "children", "visitor", "visitors", "stranger", "strangers", "human", "humans"],
    "car": ["car", "cars"],
    "truck": ["truck", "trucks", "van", "vans"],
    "bus": ["bus", "buses"],
    "motorcycle": ["motorcycle", "motorcycles", "motorbike", "scooter"],
    "bicycle": ["bike", "bikes", "bicycle", "bicycles", "cyclist"],
    "dog": ["dog", "dogs", "puppy"],
    "cat": ["cat", "cats", "kitten"],
    "backpack": ["backpack", "backpacks"],
    "suitcase": ["suitcase", "luggage"],
}
VEHICLE_WORDS = {"vehicle", "vehicles", "anything driving"}
UNKNOWN_WORDS = re.compile(r"\b(unknown|unfamiliar|strange|stranger'?s?|unrecognized|don'?t know|do not know|new)\b.{0,12}\b(car|cars|vehicle|vehicles|plate|plates)\b")
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
UNSUPPORTED = {"package": "package", "packages": "package", "delivery": "package", "box": "package", "face": "face"}


@dataclass
class Parsed:
    filter: Filter
    understood: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source: str = "rules"


def _clock(text: str) -> int | None:
    """'10pm' -> 22, '7 am' -> 7, '22:00' -> 22, 'noon' -> 12, 'midnight' -> 0."""
    text = text.strip()
    if text == "noon":
        return 12
    if text == "midnight":
        return 0
    m = re.fullmatch(r"(\d{1,2})(?::\d{2})?\s*(am|pm)?", text)
    if not m:
        return None
    h = int(m.group(1)) % 24
    if m.group(2) == "pm" and h < 12:
        h += 12
    if m.group(2) == "am" and h == 12:
        h = 0
    return h


CLOCK = r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)?|noon|midnight)"


def parse(question: str, cameras: list[Camera], known: list[KnownPlate], now: datetime | None = None) -> Parsed:
    now = now or datetime.now()
    q = " " + question.lower().strip().rstrip("?.!") + " "
    f, out = Filter(), Parsed(Filter())
    out.filter = f
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # ---- time window ----
    day_start = None
    if " yesterday " in q or "last night" in q:
        day_start = midnight - timedelta(days=1)
    elif " today " in q or "this morning" in q or "this afternoon" in q or "this evening" in q or "tonight" in q:
        day_start = midnight
    else:
        for i, wd in enumerate(WEEKDAYS):
            if re.search(rf"\b{wd}\b", q):
                back = (now.weekday() - i) % 7
                day_start = midnight - timedelta(days=back)
                break

    if "last night" in q:
        f.start, f.end = (midnight - timedelta(hours=6)).timestamp(), (midnight + timedelta(hours=6)).timestamp()
        out.understood.append("last night (6pm to 6am)")
    elif m := re.search(r"\b(?:last|past)\s+(\d+|an?|one)?\s*(minute|hour|day|week)s?\b", q):
        n = {"a": 1, "an": 1, "one": 1, None: 1}.get(m.group(1), None) or int(m.group(1))
        f.start = (now - timedelta(**{m.group(2) + "s": n})).timestamp()
        out.understood.append(f"the last {n} {m.group(2)}{'s' if n > 1 else ''}")
    elif "this week" in q:
        f.start = (midnight - timedelta(days=now.weekday())).timestamp()
        out.understood.append("this week (since Monday)")
    elif "last week" in q:
        this_monday = midnight - timedelta(days=now.weekday())
        f.start, f.end = (this_monday - timedelta(days=7)).timestamp(), this_monday.timestamp()
        out.understood.append("last week")
    elif day_start is not None:
        f.start, f.end = day_start.timestamp(), (day_start + timedelta(days=1)).timestamp()
        out.understood.append(day_start.strftime("%A %b %d"))

    part = None
    if "this morning" in q or re.search(r"\bin the morning\b", q):
        part = (5, 12, "morning")
    elif "this afternoon" in q or "in the afternoon" in q:
        part = (12, 17, "afternoon")
    elif "this evening" in q or "tonight" in q or "in the evening" in q:
        part = (17, 24, "evening")
    if part:
        base = datetime.fromtimestamp(f.start) if f.start else midnight
        base = base.replace(hour=0, minute=0, second=0, microsecond=0)
        f.start, f.end = (base + timedelta(hours=part[0])).timestamp(), (base + timedelta(hours=part[1])).timestamp()
        out.understood.append(part[2])

    base = datetime.fromtimestamp(f.start).replace(hour=0, minute=0, second=0, microsecond=0) if f.start else midnight
    if m := re.search(rf"\bbetween\s+{CLOCK}\s+and\s+{CLOCK}", q):
        a, b = _clock(m.group(1)), _clock(m.group(2))
        if a is not None and b is not None:
            if "pm" in m.group(2) and "am" not in m.group(1) and "pm" not in m.group(1) and a < 12:
                a += 12  # "between 2 and 5pm"
            f.start, f.end = (base + timedelta(hours=a)).timestamp(), (base + timedelta(hours=b if b > a else b + 24)).timestamp()
            out.understood.append(f"between {a:02d}:00 and {b:02d}:00")
    else:
        if m := re.search(rf"\bafter\s+{CLOCK}", q):
            h = _clock(m.group(1))
            if h is not None:
                f.start = (base + timedelta(hours=h)).timestamp()
                if f.end is None or f.end <= f.start:
                    f.end = (base + timedelta(days=1, hours=6 if h >= 18 else 0)).timestamp()
                out.understood.append(f"after {h:02d}:00")
        if m := re.search(rf"\bbefore\s+{CLOCK}", q):
            h = _clock(m.group(1))
            if h is not None:
                f.end = (base + timedelta(hours=h)).timestamp()
                if f.start is None or f.start >= f.end:
                    f.start = base.timestamp()
                out.understood.append(f"before {h:02d}:00")

    # ---- what ----
    labels = set()
    for label, words in LABEL_WORDS.items():
        if any(re.search(rf"\b{re.escape(w)}\b", q) for w in words):
            labels.add(label)
    if any(w in q for w in VEHICLE_WORDS):
        labels |= {"car", "truck", "bus", "motorcycle"}
    if "car" in labels:
        labels |= {"truck"}  # the detector often calls SUVs and pickups "truck"
    for word, thing in UNSUPPORTED.items():
        if re.search(rf"\b{word}\b", q):
            out.warnings.append(f"Ultron can't recognize a {thing} yet; showing other matches instead.")

    # ---- known cars / plates ----
    names = []
    for k in known:
        words = [w for w in re.findall(r"[a-z0-9']+", k.name.lower()) if len(w) >= 4 and w not in {"car", "cars"}]
        if k.name.lower() in q or any(re.search(rf"\b{re.escape(w)}\b", q) for w in words):
            names.append(k.name)
    if names:
        f.known_names = names
        labels |= {"car", "truck"}
        out.understood.append("known car: " + ", ".join(names))
    if UNKNOWN_WORDS.search(q):
        f.unknown_plates = True
        labels |= {"car", "truck"}
        out.understood.append("plates that are not on the known list")
    if m := re.search(r"\b(?!cam)(?=[a-z0-9]*\d)(?=[a-z0-9]*[a-z])[a-z0-9]{5,8}\b", q):
        f.plate_text = m.group(0).upper()
        out.understood.append(f"plate containing {f.plate_text}")
    if labels:
        f.labels = sorted(labels)
        out.understood.insert(0, ", ".join(sorted(labels)))

    # ---- where ----
    cams = []
    if m := re.search(r"\bcam(?:era)?\s*#?(\d{1,2})\b", q):
        cams.append(int(m.group(1)))
    for c in cameras:
        name = c.name.lower()
        if name in q or all(re.search(rf"\b{re.escape(w)}\b", q) for w in name.split()):
            cams.append(c.channel)
    if not cams:  # single-word match: "driveway" -> both driveway cameras
        for c in cameras:
            if any(len(w) >= 5 and re.search(rf"\b{re.escape(w)}\b", q) for w in c.name.lower().split()):
                cams.append(c.channel)
    if cams:
        f.cameras = sorted(set(cams))
        out.understood.append("cameras: " + ", ".join(
            next((c.name for c in cameras if c.channel == ch), f"Cam {ch}") for ch in f.cameras))
    return out


def is_empty(p: Parsed) -> bool:
    f = p.filter
    return not any([f.start, f.end, f.cameras, f.labels, f.known_names, f.unknown_plates, f.plate_text])


def parse_with_gemini(question: str, cameras: list[Camera], known: list[KnownPlate], api_key: str,
                      now: datetime | None = None, model: str = "gemini-2.5-flash") -> Parsed:
    """Fallback: send only the question text (plus camera and car names) to Gemini, get back a filter."""
    from google import genai

    now = now or datetime.now()
    prompt = (
        "Convert a home security camera search question into JSON. Reply with JSON only, keys: "
        "start (ISO datetime or null), end (ISO datetime or null), cameras (list of channel numbers or null), "
        "labels (subset of person,car,truck,bus,motorcycle,bicycle,dog,cat or null), known_names (list or null), "
        "unknown_plates (bool).\n"
        f"Now: {now.isoformat(timespec='minutes')} ({now.strftime('%A')}).\n"
        f"Cameras: {json.dumps({c.channel: c.name for c in cameras})}\n"
        f"Known car names: {json.dumps([k.name for k in known])}\n"
        f"Question: {question}"
    )
    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(model=model, contents=prompt)
    text = re.sub(r"^```(?:json)?|```$", "", resp.text.strip(), flags=re.M).strip()
    d = json.loads(text)
    iso = lambda s: datetime.fromisoformat(s).timestamp() if s else None  # noqa: E731
    f = Filter(start=iso(d.get("start")), end=iso(d.get("end")), cameras=d.get("cameras") or None,
               labels=d.get("labels") or None, known_names=d.get("known_names") or None,
               unknown_plates=bool(d.get("unknown_plates")))
    return Parsed(f, understood=["interpreted by Gemini"], source="gemini")


def interpret(question: str, cameras: list[Camera], known: list[KnownPlate], gemini_api_key: str | None = None,
              now: datetime | None = None, gemini_model: str = "gemini-2.5-flash") -> Parsed:
    parsed = parse(question, cameras, known, now)
    if is_empty(parsed) and gemini_api_key and question.strip():
        try:
            return parse_with_gemini(question, cameras, known, gemini_api_key, now, gemini_model)
        except Exception as e:  # network down, quota, bad JSON: fall back to "everything recent"
            parsed.warnings.append(f"Gemini fallback failed ({type(e).__name__}); showing recent events.")
    return parsed
