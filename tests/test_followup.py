"""Motion sensors can stay on for minutes; the person often appears well after the "on" edge."""

import queue

from ultron_eyes.config import Config
from ultron_eyes.pipeline import Pipeline
from ultron_eyes.vision import Analysis, Box, ObjectHit, PlateHit

B = Box(0, 0, 1, 1)


def a(*labels, plates=()):
    return Analysis([ObjectHit(label, 0.9, B) for label in labels], [PlateHit(p, 0.9, B) for p in plates])


def pipe():
    p = Pipeline.__new__(Pipeline)
    p.cfg = Config(nvr_host="x", nvr_user="u", nvr_password="p", cooldown_seconds=20,
                   active_interval_seconds=4, max_active_seconds=180)
    p.jobs, p.last_trigger, p.active, p.last_sample, p.pending, p.last_saved = queue.Queue(), {}, {}, {}, set(), {}
    return p


def test_keeps_sampling_while_motion_stays_on():
    p = pipe()
    p.motion(1, True, ts=1000)
    assert p.jobs.get_nowait() == (1, 1000, False)   # burst on the "on" edge
    p.pending.clear()
    p.last_sample[1] = 1001
    p.follow_up_tick(now=1003)
    assert p.jobs.empty()                            # too soon
    p.follow_up_tick(now=1006)
    assert p.jobs.get_nowait() == (1, 1006, True)    # follow-up while still on


def test_stops_when_motion_turns_off_or_is_stuck():
    p = pipe()
    p.motion(1, True, ts=1000)
    p.motion(1, False)
    p.pending.clear(); p.jobs = queue.Queue()
    p.follow_up_tick(now=1100)
    assert p.jobs.empty()
    p.motion(2, True, ts=1000)
    p.pending.clear(); p.jobs = queue.Queue()
    p.follow_up_tick(now=1000 + 181)
    assert p.jobs.empty() and 2 not in p.active


def test_no_duplicate_jobs_per_camera():
    p = pipe()
    assert p.trigger(1, 1000)
    assert not p.trigger(1, 1010, follow_up=True)    # one already queued


def test_save_rules():
    p = pipe()
    assert p.should_save(1, a("car"), False, 0)
    p.last_saved[1] = (0, frozenset(), frozenset({"car"}), frozenset())
    assert not p.should_save(1, a("car"), True, 5)        # same parked car on a follow-up: skip
    assert p.should_save(1, a("car", "person"), True, 5)  # a person walked in: save right away
    p.last_saved[1] = (5, frozenset({"person"}), frozenset({"car"}), frozenset())
    assert not p.should_save(1, a("car", "person"), True, 10)   # same person 5 s later: skip
    assert p.should_save(1, a("car", "person"), True, 26)       # still there after cooldown: save
    assert p.should_save(1, a("car", plates=["ABC123"]), True, 10)  # new plate: save
    assert p.should_save(1, a("car", "truck"), False, 10)       # new motion and a truck arrived
