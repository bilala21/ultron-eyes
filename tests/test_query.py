from datetime import datetime

from ultron_eyes.config import Camera, KnownPlate, normalize_plate
from ultron_eyes.query import is_empty, parse
from ultron_eyes.vision import levenshtein, match_plate

CAMS = [
    Camera(1, "Garage"), Camera(2, "Driveway Left"), Camera(3, "Driveway Right"),
    Camera(7, "Front Porch"), Camera(8, "Front Door"), Camera(6, "Backyard"),
]
KNOWN = [KnownPlate("ABC1234", "Dad's Tesla"), KnownPlate("XYZ987", "Mom's Honda")]
NOW = datetime(2026, 11, 15, 14, 30)  # a Sunday afternoon


def p(q):
    return parse(q, CAMS, KNOWN, NOW)


def ts(*a):
    return datetime(*a).timestamp()


def test_people_front_door_last_night():
    r = p("Was anyone at the front door last night?")
    assert r.filter.labels == ["person"]
    assert r.filter.cameras == [8]
    assert r.filter.start == ts(2026, 11, 14, 18) and r.filter.end == ts(2026, 11, 15, 6)


def test_known_car_today():
    r = p("When did dad's tesla leave today")
    assert r.filter.known_names == ["Dad's Tesla"]
    assert "car" in r.filter.labels
    assert r.filter.start == ts(2026, 11, 15) and r.filter.end == ts(2026, 11, 16)


def test_unknown_cars_this_week():
    r = p("show me unknown cars this week")
    assert r.filter.unknown_plates
    assert r.filter.start == ts(2026, 11, 9)  # Monday


def test_driveway_matches_both_cameras():
    assert p("cars in the driveway").filter.cameras == [2, 3]


def test_after_time_yesterday():
    r = p("dogs in the backyard yesterday after 9pm")
    assert r.filter.labels == ["dog"] and r.filter.cameras == [6]
    assert r.filter.start == ts(2026, 11, 14, 21)


def test_between_times():
    r = p("people between 2 and 5pm today")
    assert r.filter.start == ts(2026, 11, 15, 14) and r.filter.end == ts(2026, 11, 15, 17)


def test_last_n_hours():
    assert p("anything in the last 3 hours").filter.start == ts(2026, 11, 15, 11, 30)


def test_weekday():
    r = p("trucks on friday")
    assert r.filter.start == ts(2026, 11, 13) and "truck" in r.filter.labels


def test_camera_number():
    assert p("cam 3 this morning").filter.cameras == [3]


def test_package_warning():
    assert p("did a package come today").warnings


def test_gibberish_is_empty():
    assert is_empty(p("hello there"))


def test_plate_helpers():
    assert normalize_plate("abc 12-34") == "ABC1234"
    assert levenshtein("ABC1234", "ABC1Z34") == 1
    assert match_plate("ABC1Z34", KNOWN) == "Dad's Tesla"   # one misread character still matches
    assert match_plate("QQQ0000", KNOWN) is None
