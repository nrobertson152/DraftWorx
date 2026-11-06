# SPDX-License-Identifier: GPL-3.0-or-later
"""Where two curves meet.

Line, circle and ellipse pairs are solved in closed form. Pairs without a neat closed form (a circle and an ellipse, two
ellipses, anything with a spline) are bracketed from a polygon of each curve and polished by Newton on the real curves,
so the answer is still on both curves to within rounding, not on an approximation of them.

`extend_a` / `extend_b` treat a line as infinite and an arc or elliptical arc as its whole circle or ellipse. A spline
doesn't extend.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .curves import Arc, Circle, EllipseArc, Line, Polyline, Spline
from .vec import EPS_ANGLE, TAU, Vec, tol


@dataclass(frozen=True, slots=True)
class Hit:
    point: Vec
    ta: float
    tb: float


def intersect(a, b, extend_a: bool = False, extend_b: bool = False) -> list[Hit]:
    """Every point where a and b meet, ordered along a. Overlapping stretches give the ends of the overlap."""
    if isinstance(a, Polyline):
        return _polyline(a, b, extend_b, False)
    if isinstance(b, Polyline):
        return _polyline(b, a, extend_a, True)
    if isinstance(a, Circle):
        a = a.as_arc()
    if isinstance(b, Circle):
        b = b.as_arc()
    ka, kb = type(a), type(b)
    if ka is Line:
        if kb is Line:
            hits = _line_line(a, b, extend_a, extend_b)
        elif kb is Arc:
            hits = _line_arc(a, b, extend_a, extend_b)
        elif kb is EllipseArc:
            hits = _line_ellipse(a, b, extend_a, extend_b)
        else:
            hits = _numeric(a, b, extend_a, extend_b)
    elif ka is Arc and kb is Line:
        hits = [Hit(h.point, h.tb, h.ta) for h in _line_arc(b, a, extend_b, extend_a)]
    elif ka is Arc and kb is Arc:
        hits = _arc_arc(a, b, extend_a, extend_b)
    elif ka is EllipseArc and kb is Line:
        hits = [Hit(h.point, h.tb, h.ta) for h in _line_ellipse(b, a, extend_b, extend_a)]
    else:
        hits = _numeric(a, b, extend_a, extend_b)
    hits.sort(key=lambda h: (h.ta, h.tb))
    return hits


def _polyline(p: Polyline, other, extend_other: bool, flip: bool) -> list[Hit]:
    n = len(p.segments)
    out: list[Hit] = []
    for i, s in enumerate(p.segments):
        for h in intersect(s, other, False, extend_other):
            t = (i + h.ta) / n
            out.append(Hit(h.point, h.tb, t) if flip else Hit(h.point, t, h.tb))
    out.sort(key=lambda h: (h.ta, h.tb))
    # a crossing exactly at a vertex is found by both segments
    unique: list[Hit] = []
    for h in out:
        if not any(abs(h.point[0] - u.point[0]) <= 1e-9 and abs(h.point[1] - u.point[1]) <= 1e-9 for u in unique[-4:]):
            unique.append(h)
    return unique


def _clamp_line(t: float, length: float, eps: float, extended: bool) -> float | None:
    """t, snapped to the end it's within tolerance of. None if it's off the segment (and the line isn't extended)."""
    d = t * length
    if -eps <= d <= eps:
        return 0.0
    if -eps <= d - length <= eps:
        return 1.0
    if extended or 0.0 < t < 1.0:
        return t
    return None


def _clamp_arc(arc: Arc | EllipseArc, t: float, length_per_rad: float, eps: float, extended: bool) -> float | None:
    """t along an arc from its direction, with the same idea. A point just before the start counts as the start."""
    sweep = abs(arc.sweep)
    angle = t * sweep
    limit = eps / length_per_rad if length_per_rad > 0.0 else eps
    if angle <= limit or TAU - angle <= limit:
        return 0.0
    if abs(angle - sweep) <= limit:
        return 1.0
    if extended or angle < sweep:
        return t
    return None


def _line_line(a: Line, b: Line, ex_a: bool, ex_b: bool) -> list[Hit]:
    (ax, ay), (bx, by) = a.a, a.b
    (cx, cy), (dx, dy) = b.a, b.b
    ux, uy = bx - ax, by - ay
    vx, vy = dx - cx, dy - cy
    la = math.hypot(ux, uy)
    lb = math.hypot(vx, vy)
    eps = tol(ax, ay, bx, by, cx, cy, dx, dy)
    if la == 0.0 or lb == 0.0:
        return _degenerate_line(a, b, la, lb, eps)
    den = ux * vy - uy * vx
    wx, wy = cx - ax, cy - ay
    if abs(den) <= EPS_ANGLE * la * lb:
        if abs(wx * uy - wy * ux) / la > eps:
            return []
        return _overlap(a, b, ex_a, ex_b, la, eps)
    ta = _clamp_line((wx * vy - wy * vx) / den, la, eps, ex_a)
    if ta is None:
        return []
    tb = _clamp_line((wx * uy - wy * ux) / den, lb, eps, ex_b)
    if tb is None:
        return []
    # An end of a segment is an exact number, so where the crossing is at an end, use it, not a recomputed value.
    if tb == 0.0 or tb == 1.0:
        p = b.point_at(tb)
    else:
        p = a.point_at(ta)
    return [Hit(p, ta, tb)]


def _degenerate_line(a: Line, b: Line, la: float, lb: float, eps: float) -> list[Hit]:
    if la == 0.0 and lb == 0.0:
        return [Hit(a.a, 0.0, 0.0)] if a.a.distance(b.a) <= eps else []
    if la == 0.0:
        t, q = b.project(a.a)
        return [Hit(a.a, 0.0, t)] if q.distance(a.a) <= eps else []
    t, q = a.project(b.a)
    return [Hit(b.a, t, 0.0)] if q.distance(b.a) <= eps else []


def _overlap(a: Line, b: Line, ex_a: bool, ex_b: bool, la: float, eps: float) -> list[Hit]:
    """Two segments on the same line: the ends of the part they share. Extending a line makes the "ends" meaningless."""
    if ex_a or ex_b:
        return []
    t0, t1 = a.param_of(b.a), a.param_of(b.b)
    lo, hi = (t0, t1) if t0 <= t1 else (t1, t0)
    margin = eps / la
    if hi < -margin or lo > 1.0 + margin:
        return []
    out: list[Hit] = []
    for t in sorted({max(0.0, lo), min(1.0, hi)}):
        p = a.point_at(t)
        tb = b.param_of(p)
        out.append(Hit(p, t, min(1.0, max(0.0, tb))))
    if len(out) == 2 and out[0].point.distance(out[1].point) <= eps:
        out.pop()
    return out


def _line_arc(line: Line, arc: Arc, ex_l: bool, ex_a: bool) -> list[Hit]:
    (ax, ay), (bx, by) = line.a, line.b
    (cx, cy), r = arc.c, arc.r
    ux, uy = bx - ax, by - ay
    L = math.hypot(ux, uy)
    eps = tol(ax, ay, bx, by, cx + r, cy + r, cx - r, cy - r)
    if L == 0.0:
        d = line.a.distance(arc.c)
        if abs(d - r) <= eps:
            t = _clamp_arc(arc, arc.param_of_angle(math.atan2(ay - cy, ax - cx)), r, eps, ex_a)
            if t is not None:
                return [Hit(line.a, 0.0, t)]
        return []
    ux /= L
    uy /= L
    wx, wy = cx - ax, cy - ay
    s = wx * ux + wy * uy
    h = ux * wy - uy * wx
    ah = abs(h)
    if ah > r + eps:
        return []
    if ah >= r - eps:
        along = (s,)        # tangent: one point, the foot of the perpendicular
    else:
        off = math.sqrt((r - ah) * (r + ah))
        along = (s - off, s + off)
    out: list[Hit] = []
    for d in along:
        tl = _clamp_line(d / L, L, eps, ex_l)
        if tl is None:
            continue
        p = line.point_at(tl) if tl in (0.0, 1.0) else Vec(ax + ux * d, ay + uy * d)
        ta = _clamp_arc(arc, arc.param_of_angle(math.atan2(p[1] - cy, p[0] - cx)), r, eps, ex_a)
        if ta is None:
            continue
        out.append(Hit(p, tl, ta))
    return out


def _arc_arc(a: Arc, b: Arc, ex_a: bool, ex_b: bool) -> list[Hit]:
    (ax, ay), ra = a.c, a.r
    (bx, by), rb = b.c, b.r
    eps = tol(ax + ra, ay + ra, ax - ra, ay - ra, bx + rb, by + rb, bx - rb, by - rb)
    dx, dy = bx - ax, by - ay
    d = math.hypot(dx, dy)
    if d <= eps:
        if abs(ra - rb) > eps:
            return []
        return _same_circle(a, b, ex_a, ex_b, eps)
    if d > ra + rb + eps or d < abs(ra - rb) - eps:
        return []
    ux, uy = dx / d, dy / d
    along = (d * d + ra * ra - rb * rb) / (2.0 * d)
    if abs(d - (ra + rb)) <= eps or abs(d - abs(ra - rb)) <= eps:
        points = [Vec(ax + ux * along, ay + uy * along)]
    else:
        h2 = ra * ra - along * along
        h = math.sqrt(h2) if h2 > 0.0 else 0.0
        points = [Vec(ax + ux * along - uy * h, ay + uy * along + ux * h),
                  Vec(ax + ux * along + uy * h, ay + uy * along - ux * h)]
    out: list[Hit] = []
    for p in points:
        ta = _clamp_arc(a, a.param_of_angle(math.atan2(p[1] - ay, p[0] - ax)), ra, eps, ex_a)
        if ta is None:
            continue
        tb = _clamp_arc(b, b.param_of_angle(math.atan2(p[1] - by, p[0] - bx)), rb, eps, ex_b)
        if tb is None:
            continue
        out.append(Hit(p, ta, tb))
    return out


def _same_circle(a: Arc, b: Arc, ex_a: bool, ex_b: bool, eps: float) -> list[Hit]:
    """Two arcs of one circle: where one's ends lie on the other."""
    if a.closed and b.closed:
        return []
    out: list[Hit] = []
    for t_on_a, p, in ((0.0, a.start), (1.0, a.end)):
        tb = _clamp_arc(b, b.param_of_angle(math.atan2(p[1] - b.c[1], p[0] - b.c[0])), b.r, eps, ex_b)
        if tb is not None and not a.closed:
            out.append(Hit(p, t_on_a, tb))
    for t_on_b, p in ((0.0, b.start), (1.0, b.end)):
        ta = _clamp_arc(a, a.param_of_angle(math.atan2(p[1] - a.c[1], p[0] - a.c[0])), a.r, eps, ex_a)
        if ta is not None and not b.closed:
            if not any(h.point.distance(p) <= eps for h in out):
                out.append(Hit(p, ta, t_on_b))
    return out


def _line_ellipse(line: Line, e: EllipseArc, ex_l: bool, ex_e: bool) -> list[Hit]:
    """In the ellipse's own frame it's a unit circle, and a line stays a line with the same parameter."""
    a_len = e.major.length()
    if a_len == 0.0 or e.ratio == 0.0:
        return []
    b_len = a_len * e.ratio
    ux, uy = e.major[0] / a_len, e.major[1] / a_len
    (cx, cy) = e.c
    (ax, ay), (bx, by) = line.a, line.b
    eps = tol(ax, ay, bx, by, cx + a_len, cy + a_len, cx - a_len, cy - a_len)

    def frame(x, y):
        dx, dy = x - cx, y - cy
        return (dx * ux + dy * uy) / a_len, (-dx * uy + dy * ux) / b_len
    p0x, p0y = frame(ax, ay)
    p1x, p1y = frame(bx, by)
    dx, dy = p1x - p0x, p1y - p0y
    A = dx * dx + dy * dy
    L = math.hypot(bx - ax, by - ay)
    if A == 0.0 or L == 0.0:
        return []
    Bh = (p0x * dx + p0y * dy)
    C = p0x * p0x + p0y * p0y - 1.0
    disc = Bh * Bh - A * C
    # tangent if the closest approach, in real length, is within tolerance of the ellipse
    closest = math.sqrt(max(0.0, C + 1.0 - Bh * Bh / A))
    if closest > 1.0 + eps / b_len:
        return []
    if abs(closest - 1.0) * b_len <= eps or disc <= 0.0:
        roots = (-Bh / A,)
    else:
        rt = math.sqrt(disc)
        roots = ((-Bh - rt) / A, (-Bh + rt) / A)
    out: list[Hit] = []
    for t in roots:
        tl = _clamp_line(t, L, eps, ex_l)
        if tl is None:
            continue
        p = line.point_at(tl)
        fx, fy = frame(p[0], p[1])
        te = _clamp_arc(e, e.param_to_t(math.atan2(fy, fx)), a_len, eps, ex_e)
        if te is None:
            continue
        out.append(Hit(p, tl, te))
    return out


# ------------------------------------------------------------------------------------------- the general solver

def _polygon(c) -> list[tuple[float, Vec]]:
    if isinstance(c, Spline):
        return c._polygon
    if isinstance(c, Line):
        return [(0.0, c.a), (1.0, c.b)]
    n = max(4, math.ceil(abs(c.sweep) / (math.pi / 24.0)))
    return [(i / n, c.point_at(i / n)) for i in range(n + 1)]


def _whole(c, extended: bool, other):
    """The curve to search on when extending, and how to get the caller's parameter back from a point on it."""
    if not extended:
        return c
    if isinstance(c, Arc):
        return Arc(c.c, c.r, 0.0, TAU)
    if isinstance(c, EllipseArc):
        return EllipseArc(c.c, c.major, c.ratio, 0.0, TAU)
    if isinstance(c, Line):
        # long enough to cross everything the other curve could reach
        x0, y0, x1, y1 = other.bbox()
        corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
        ts = [c.param_of(q) for q in corners]
        lo, hi = min(ts + [0.0]), max(ts + [1.0])
        span = (hi - lo) or 1.0
        return Line(c.point_at(lo - span * 0.01), c.point_at(hi + span * 0.01))
    return c


def _param_on(c, p) -> float:
    if isinstance(c, Line):
        return c.param_of(p)
    if isinstance(c, Arc):
        return c.param_of_angle(math.atan2(p[1] - c.c[1], p[0] - c.c[0]))
    if isinstance(c, EllipseArc):
        full = EllipseArc(c.c, c.major, c.ratio, 0.0, TAU)
        return c.param_to_t(full.project(p)[0] * TAU)
    return c.project(p)[0]


def _segment_gap(p0, p1, q0, q1) -> tuple[float, float, float]:
    """The closest approach of two segments, with the parameters where it happens."""
    ux, uy = p1[0] - p0[0], p1[1] - p0[1]
    vx, vy = q1[0] - q0[0], q1[1] - q0[1]
    wx, wy = p0[0] - q0[0], p0[1] - q0[1]
    a, b, c = ux * ux + uy * uy, ux * vx + uy * vy, vx * vx + vy * vy
    d, e = ux * wx + uy * wy, vx * wx + vy * wy
    den = a * c - b * b
    s = 0.0
    if den > 1e-30:
        s = min(1.0, max(0.0, (b * e - c * d) / den))
    t = (b * s + e) / c if c > 0.0 else 0.0
    if t < 0.0:
        t = 0.0
        s = min(1.0, max(0.0, -d / a)) if a > 0.0 else 0.0
    elif t > 1.0:
        t = 1.0
        s = min(1.0, max(0.0, (b - d) / a)) if a > 0.0 else 0.0
    dx = wx + ux * s - vx * t
    dy = wy + uy * s - vy * t
    return math.hypot(dx, dy), s, t


def _polish(a, b, ta: float, tb: float, eps: float) -> tuple[float, float, Vec] | None:
    """Solve a(ta) = b(tb) from a good start. Damped, so a tangency (where the plain Newton matrix is singular) still
    settles on the point of closest approach, and then counts only if the curves really touch there."""
    damping = 0.0
    for _ in range(40):
        pa, pb = a.point_at(ta), b.point_at(tb)
        fx, fy = pa[0] - pb[0], pa[1] - pb[1]
        err = math.hypot(fx, fy)
        if err <= eps * 0.01:
            break
        da, db = a.tangent_at(ta), b.tangent_at(tb)
        # J = [da, -db]; solve (J^T J + damping I) step = -J^T f
        m11 = da.dot(da) + damping
        m12 = -da.dot(db)
        m22 = db.dot(db) + damping
        r1 = -(da[0] * fx + da[1] * fy)
        r2 = db[0] * fx + db[1] * fy
        det = m11 * m22 - m12 * m12
        if det <= 1e-30 * (m11 * m22 + 1e-300):
            damping = max(damping * 10.0, 1e-9 * (m11 + m22 + 1e-30))
            continue
        na = min(1.0, max(0.0, ta + (r1 * m22 - r2 * m12) / det))
        nb = min(1.0, max(0.0, tb + (r2 * m11 - r1 * m12) / det))
        qa, qb = a.point_at(na), b.point_at(nb)
        new = math.hypot(qa[0] - qb[0], qa[1] - qb[1])
        if new < err or (na == ta and nb == tb):
            if na == ta and nb == tb:
                break
            ta, tb = na, nb
            damping *= 0.1
        else:
            damping = max(damping * 10.0, 1e-9 * (m11 + m22 + 1e-30))
            if damping > 1e6 * (m11 + m22 + 1e-30):
                break
    pa, pb = a.point_at(ta), b.point_at(tb)
    if math.hypot(pa[0] - pb[0], pa[1] - pb[1]) > eps:
        return None
    return ta, tb, pa


def _settle_tangency(a, b, found: tuple[float, float, Vec], eps: float) -> tuple[float, float, Vec]:
    """Where curves touch without crossing, every point for a short way either side is "on both" to within rounding, so
    solving a(ta) = b(tb) can't say which is the contact. The contact is where their directions agree, and that is a
    well-conditioned question: find it, and use it if the curves really touch there. A shallow pair of crossings has a
    gap at that point, so it keeps its two answers."""
    ta, tb, _ = found
    da, db = a.tangent_at(ta).unit(), b.tangent_at(tb).unit()
    if abs(da.cross(db)) > 1e-3:
        return found

    closed = a.closed

    def turn(t):
        u = t % 1.0 if closed else min(1.0, max(0.0, t))
        v = b.project(a.point_at(u))[0]
        return a.tangent_at(u).unit().cross(b.tangent_at(v).unit()), u, v
    step = 1e-3
    lo, hi = turn(ta - step), turn(ta + step)
    if lo[0] * hi[0] > 0.0:
        return found
    t0, t1, f0, f1 = (ta - step, ta + step, lo[0], hi[0]) if closed else (lo[1], hi[1], lo[0], hi[0])
    f, u, v = lo
    for _ in range(50):
        t = t1 - f1 * (t1 - t0) / (f1 - f0) if f1 != f0 else (t0 + t1) / 2.0
        if not min(t0, t1) < t < max(t0, t1):
            t = (t0 + t1) / 2.0
        f, u, v = turn(t)
        if f == 0.0 or abs(t1 - t0) < 1e-13:
            break
        # Illinois: keeps the bracket closing from both sides
        if f * f1 < 0.0:
            t0, f0 = t1, f1
        else:
            f0 *= 0.5
        t1, f1 = t, f
    p, q = a.point_at(u), b.point_at(v)
    if math.hypot(p[0] - q[0], p[1] - q[1]) > eps:
        return found
    return u, v, p


def _numeric(a, b, ex_a: bool, ex_b: bool) -> list[Hit]:
    A = _whole(a, ex_a, b)
    B = _whole(b, ex_b, a)
    pa, pb = _polygon(A), _polygon(B)
    box = (*A.bbox(), *B.bbox())
    eps = tol(*box)
    size = max(box[2] - box[0], box[3] - box[1], box[6] - box[4], box[7] - box[5], 1e-12)
    # how far apart the polygons can be where the curves touch: their sagittas
    near_enough = _sagitta(A, pa) + _sagitta(B, pb) + eps

    segs_b = []
    for i in range(len(pb) - 1):
        q0, q1 = pb[i][1], pb[i + 1][1]
        segs_b.append((min(q0[0], q1[0]), max(q0[0], q1[0]), min(q0[1], q1[1]), max(q0[1], q1[1]), i))
    segs_b.sort()
    found: list[tuple[float, float, Vec]] = []
    for i in range(len(pa) - 1):
        (t0, p0), (t1, p1) = pa[i], pa[i + 1]
        x0, x1 = (p0[0], p1[0]) if p0[0] < p1[0] else (p1[0], p0[0])
        y0, y1 = (p0[1], p1[1]) if p0[1] < p1[1] else (p1[1], p0[1])
        for sx0, sx1, sy0, sy1, j in segs_b:
            if sx0 > x1 + near_enough:
                break
            if sx1 < x0 - near_enough or sy0 > y1 + near_enough or sy1 < y0 - near_enough:
                continue
            (u0, q0), (u1, q1) = pb[j], pb[j + 1]
            gap, s, t = _segment_gap(p0, p1, q0, q1)
            if gap > near_enough:
                continue
            start_a, start_b = t0 + (t1 - t0) * s, u0 + (u1 - u0) * t
            r = _polish(A, B, start_a, start_b, eps)
            if r is None:
                continue
            found.append(_settle_tangency(A, B, r, eps))
    # A tangency is only as sharp as the square root of rounding, so near-copies need a coarser test than a crossing.
    merged: list[tuple[float, float, Vec]] = []
    same = max(eps, size * 1e-7)
    for r in found:
        if not any(r[2].distance(m[2]) <= same for m in merged):
            merged.append(r)
    out: list[Hit] = []
    for ta, tb, p in merged:
        ca = _accept(a, A, ta, p, ex_a, eps)
        if ca is None:
            continue
        cb = _accept(b, B, tb, p, ex_b, eps)
        if cb is None:
            continue
        out.append(Hit(p, ca, cb))
    return out


def _sagitta(c, polygon) -> float:
    if isinstance(c, Line):
        return 0.0
    if isinstance(c, Spline):
        (x0, y0, x1, y1) = c.bbox()
        return max(x1 - x0, y1 - y0) * 1e-3
    radius = c.r if isinstance(c, Arc) else c.major.length()
    step = abs(c.sweep) / (len(polygon) - 1)
    return radius * (1.0 - math.cos(step / 2.0)) * 1.5


def _accept(c, searched, t: float, p: Vec, extended: bool, eps: float) -> float | None:
    """The hit in terms of the caller's curve, whose parameter differs from the one searched when it was extended."""
    if c is searched:
        if isinstance(c, Spline):
            return t
        return t
    t2 = _param_on(c, p)
    if isinstance(c, Line):
        L = c.length()
        return _clamp_line(t2, L, eps, extended) if L else None
    if isinstance(c, Arc):
        return _clamp_arc(c, t2, c.r, eps, extended)
    if isinstance(c, EllipseArc):
        return _clamp_arc(c, t2, c.major.length(), eps, extended)
    return t2
