# SPDX-License-Identifier: GPL-3.0-or-later
import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from draftworx.core.spatial import SpatialIndex

bbox = st.tuples(st.floats(-1e6, 1e6), st.floats(-1e6, 1e6), st.floats(0, 1e5), st.floats(0, 1e5)).map(
    lambda t: (t[0], t[1], t[0] + t[2], t[1] + t[3]))


def overlaps(a, b):
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


@given(st.lists(bbox, max_size=60), bbox)
def test_query_matches_brute_force(boxes, window):
    idx = SpatialIndex()
    for i, b in enumerate(boxes):
        idx.insert(i, b)
    assert idx.query(window) == [i for i, b in enumerate(boxes) if overlaps(b, window)]


@given(st.lists(bbox, min_size=1, max_size=40), st.data())
def test_remove_and_replace(boxes, data):
    idx = SpatialIndex()
    live = dict(enumerate(boxes))
    for k, b in live.items():
        idx.insert(k, b)
    for _ in range(20):
        k = data.draw(st.sampled_from(sorted(boxes and range(len(boxes)))))
        if k in live and data.draw(st.booleans()):
            idx.remove(k)
            del live[k]
        else:
            live[k] = data.draw(bbox)
            idx.insert(k, live[k])
        window = data.draw(bbox)
        assert idx.query(window) == sorted(i for i, b in live.items() if overlaps(b, window))
    assert len(idx) == len(live)


def test_points_and_giants_side_by_side():
    idx = SpatialIndex()
    idx.insert(1, (5.0, 5.0, 5.0, 5.0))
    idx.insert(2, (-1e9, -1e9, 1e9, 1e9))
    idx.insert(3, (1e-13, 1e-13, 1e-13, 1e-13))
    assert idx.query((4.9, 4.9, 5.1, 5.1)) == [1, 2]
    assert idx.query((0.0, 0.0, 1e-12, 1e-12)) == [2, 3]


def test_results_do_not_depend_on_insertion_order():
    boxes = [(float(i), 0.0, float(i) + 1, 1.0) for i in range(100)]
    a, b = SpatialIndex(), SpatialIndex()
    for i, q in enumerate(boxes):
        a.insert(i, q)
    order = list(enumerate(boxes))
    random.Random(1).shuffle(order)
    for i, q in order:
        b.insert(i, q)
    assert a.query((10, 0, 20, 1)) == b.query((10, 0, 20, 1))


def test_non_finite_is_refused():
    with pytest.raises(ValueError):
        SpatialIndex().insert(1, (float("nan"), 0.0, 1.0, 1.0))
    with pytest.raises(ValueError):
        SpatialIndex().insert(1, (0.0, 0.0, float("inf"), 1.0))


def test_bounds():
    idx = SpatialIndex()
    assert idx.bounds() is None
    idx.insert(1, (0.0, 0.0, 1.0, 1.0))
    idx.insert(2, (5.0, -2.0, 6.0, 1.0))
    assert idx.bounds() == (0.0, -2.0, 6.0, 1.0)
    idx.remove(2)
    assert idx.bounds() == (0.0, 0.0, 1.0, 1.0)


def test_reinserting_a_key_moves_it():
    idx = SpatialIndex()
    idx.insert(1, (0.0, 0.0, 1.0, 1.0))
    idx.insert(1, (100.0, 100.0, 101.0, 101.0))
    assert idx.query((0, 0, 1, 1)) == []
    assert idx.query((100, 100, 101, 101)) == [1]
    assert len(idx) == 1
    idx.remove(99)      # removing what isn't there is harmless


def _cells_visited(idx, window):
    """How many cells a query looks at: what its speed depends on."""
    x0, y0, x1, y1 = window
    seen = 0
    stack = [idx._root]
    while stack:
        cell = stack.pop()
        seen += 1
        r = cell.h * 2
        if cell.cx - r > x1 or cell.cx + r < x0 or cell.cy - r > y1 or cell.cy + r < y0:
            continue
        stack.extend(cell.kids)
    return seen


def test_points_do_not_make_a_query_walk_everything():
    idx = SpatialIndex()
    r = random.Random(3)
    for i in range(20_000):
        x, y = r.random() * 1e5, r.random() * 1e5
        idx.insert(i, (x, y, x, y))
    assert _cells_visited(idx, (5e4, 5e4, 5e4 + 100, 5e4 + 100)) < 150


def test_a_tight_cluster_in_a_huge_drawing_is_still_fast_to_search():
    idx = SpatialIndex()
    r = random.Random(4)
    for i in range(10_000):
        x, y = r.random() * 1e6, r.random() * 1e6
        idx.insert(i, (x, y, x + 1, y + 1))
    for i in range(10_000, 30_000):
        x, y = 1000 + r.random(), 1000 + r.random()
        idx.insert(i, (x, y, x + 1e-3, y + 1e-3))
    assert _cells_visited(idx, (1000.5, 1000.5, 1000.51, 1000.51)) < 300
    hits = idx.query((1000.5, 1000.5, 1000.51, 1000.51))
    assert 0 < len(hits) < 5000


def test_many_identical_points_share_a_cell_rather_than_dividing_forever():
    idx = SpatialIndex()
    for i in range(1000):
        idx.insert(i, (1.0, 1.0, 1.0, 1.0))
    assert len(idx.query((0.5, 0.5, 1.5, 1.5))) == 1000
    for i in range(1000):
        idx.remove(i)
    assert len(idx) == 0 and idx.bounds() is None


def test_empty_cells_are_cleaned_up():
    idx = SpatialIndex()
    for i in range(1000):
        idx.insert(i, (float(i), 0.0, float(i), 0.0))
    for i in range(1000):
        idx.remove(i)
    assert not idx._root.kids and not idx._root.items


def test_coordinates_far_apart_all_work():
    idx = SpatialIndex()
    idx.insert(1, (1e300, 1e300, 1e300, 1e300))
    idx.insert(2, (-1e300, 0.0, -1e300, 0.0))
    idx.insert(3, (1e-300, 1e-300, 1e-300, 1e-300))
    assert idx.query((1e300, 1e300, 1e300, 1e300)) == [1]
    assert idx.query((-1e300, -1.0, -1e300, 1.0)) == [2]
    assert idx.query((0.0, 0.0, 1.0, 1.0)) == [3]
    assert idx.query((-1e301, -1e301, 1e301, 1e301)) == [1, 2, 3]


def test_a_box_too_large_for_doubles_is_refused_not_a_hang():
    with pytest.raises(ValueError):
        SpatialIndex().insert(1, (-1.7e308, 0.0, 1.7e308, 0.0))
