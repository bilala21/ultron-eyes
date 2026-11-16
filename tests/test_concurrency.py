import threading
import time

import numpy as np

from ultron_eyes.store import Filter, Store
from ultron_eyes.vision import Analysis, Box, ObjectHit


def test_parallel_reads_and_writes(tmp_path):
    s = Store(tmp_path)
    a = Analysis([ObjectHit("person", 0.9, Box(0, 0, 1, 1))])
    img = np.zeros((9, 16, 3), np.uint8)
    errors = []

    def writer():
        try:
            for _ in range(30):
                s.add(time.time(), 1, "Garage", b"\xff\xd8", img, a)
        except Exception as e:
            errors.append(e)

    def reader():
        try:
            for _ in range(60):
                for e in s.search(Filter(labels=["person"])):
                    s.get(e["id"])
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer)] + [threading.Thread(target=reader) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert s.stats()["events"] == 30
