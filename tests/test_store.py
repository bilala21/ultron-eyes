import time

import numpy as np

from ultron_eyes.store import Filter, Store
from ultron_eyes.vision import Analysis, Box, ObjectHit, PlateHit

JPEG = b"\xff\xd8fake"
IMG = np.zeros((90, 160, 3), np.uint8)


def add(store, ts, cam, labels, plates=()):
    a = Analysis([ObjectHit(label, 0.9, Box(0, 0, 10, 10)) for label in labels],
                 [PlateHit(t, 0.9, Box(0, 0, 5, 5), known) for t, known in plates])
    return store.add(ts, cam, f"Cam {cam}", JPEG, IMG, a, latency_ms=1000)


def test_search_and_review(tmp_path):
    s = Store(tmp_path)
    now = time.time()
    add(s, now - 60, 1, ["car"], [("ABC1234", "Dad's Car")])
    add(s, now - 30, 2, ["person", "dog"])
    add(s, now - 10, 3, ["car"], [("ZZZ999", None)])

    assert {e["camera"] for e in s.search(Filter(labels=["person"]))} == {2}
    assert [e["camera"] for e in s.search(Filter(labels=["car"]))] == [3, 1]  # newest first
    assert [e["camera"] for e in s.search(Filter(known_names=["dad's car"]))] == [1]
    assert [e["camera"] for e in s.search(Filter(unknown_plates=True))] == [3]
    assert [e["camera"] for e in s.search(Filter(cameras=[1, 2]))] == [2, 1]
    assert s.search(Filter(start=now - 20))[0]["camera"] == 3

    e = s.search(Filter(labels=["dog"]))[0]
    s.review(e["id"], True)
    assert s.stats()["reviewed"] == 1 and s.stats()["precision"] == 1.0


def test_label_match_is_exact(tmp_path):
    s = Store(tmp_path)
    add(s, time.time(), 1, ["cat"])
    assert s.search(Filter(labels=["ca"])) == []  # no partial-word matches


def test_retention(tmp_path):
    s = Store(tmp_path)
    old = add(s, time.time() - 20 * 86400, 1, ["car"])
    snap = tmp_path / s.get(old)["snapshot"]
    assert snap.exists()
    add(s, time.time(), 1, ["car"])
    assert s.purge(14) == 1
    assert not snap.exists() and s.get(old) is None
    assert s.stats()["events"] == 1
