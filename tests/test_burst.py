from ultron_eyes.config import Config
from ultron_eyes.pipeline import Pipeline, frame_score
from ultron_eyes.vision import Analysis, Box, ObjectHit

B = Box(0, 0, 1, 1)


def a(*labels):
    return Analysis([ObjectHit(label, 0.9, B) for label in labels])


def test_person_beats_more_parked_objects():
    assert frame_score(a("person")) > frame_score(a("car", "car", "bicycle"))


def test_more_objects_wins_when_no_person():
    assert frame_score(a("car", "bicycle")) > frame_score(a("car"))


class FakeNVR:
    def __init__(self):
        self.n = 0

    def snapshot(self, channel):
        self.n += 1
        return b"\xff\xd8", self.n  # "image" is just the frame number


class FakeVision:
    frames = {1: a("car", "car"), 2: a("car", "car", "person"), 3: a("car")}

    def analyze(self, img, known, plates=True):
        return self.frames[img]


def test_burst_keeps_frame_with_person(tmp_path):
    cfg = Config(nvr_host="x", nvr_user="u", nvr_password="p", burst_frames=3)
    p = Pipeline.__new__(Pipeline)
    p.cfg, p.nvr, p.vision = cfg, FakeNVR(), FakeVision()
    jpeg, img, analysis = p.best_frame(1, plates=False)
    assert img == 2 and "person" in analysis.labels
