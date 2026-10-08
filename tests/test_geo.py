import pytest

from mando.geo import (
    bearing_deg,
    cardinal_es,
    centroid,
    describe_offset,
    distance_to_ring_m,
    format_distance,
    haversine_m,
    point_in_ring,
)

SQUARE = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
CLOSED_SQUARE = SQUARE + [SQUARE[0]]


def test_haversine_one_degree_latitude():
    assert haversine_m(0.0, 0.0, 1.0, 0.0) == pytest.approx(111195, abs=50)


def test_bearing_north_and_east():
    assert bearing_deg(0.0, 0.0, 1.0, 0.0) == pytest.approx(0.0, abs=0.01)
    assert bearing_deg(0.0, 0.0, 0.0, 1.0) == pytest.approx(90.0, abs=0.01)


@pytest.mark.parametrize("deg,expected", [
    (0, "N"), (44, "NE"), (90, "E"), (135, "SE"),
    (180, "S"), (225, "SO"), (270, "O"), (315, "NO"), (359, "N"),
])
def test_cardinal_es(deg, expected):
    assert cardinal_es(deg) == expected


@pytest.mark.parametrize("ring", [SQUARE, CLOSED_SQUARE])
def test_point_in_ring(ring):
    assert point_in_ring(0.5, 0.5, ring) is True
    assert point_in_ring(2.0, 2.0, ring) is False


def test_distance_to_ring_inside_is_zero():
    assert distance_to_ring_m(0.5, 0.5, SQUARE) == 0.0


def test_distance_to_ring_outside():
    ring = [[0.0, 0.0], [0.01, 0.0], [0.01, 0.01], [0.0, 0.01]]
    assert distance_to_ring_m(0.011, 0.005, ring) == pytest.approx(111.2, abs=2.0)


def test_centroid_ignores_closing_duplicate():
    assert centroid(CLOSED_SQUARE) == (0.5, 0.5)
    assert centroid(SQUARE) == (0.5, 0.5)


@pytest.mark.parametrize("m,expected", [(33, "35 m"), (123, "120 m"), (1234, "1,2 km")])
def test_format_distance(m, expected):
    assert format_distance(m) == expected


def test_describe_offset_same_place():
    assert describe_offset(0.0, 0.0, 0.0, 0.0) == "aquí mismo"
    assert describe_offset(0.0, 0.0, 0.00001, 0.0) == "aquí mismo"


def test_describe_offset_far():
    assert describe_offset(0.0, 0.0, 0.001, 0.0) == "110 m al N"
