# SPDX-License-Identifier: GPL-3.0-or-later
import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from draftworx.core.units import (TypedError, format_angle, format_length, parse_angle, parse_length, parse_number, parse_point, unit_factor)
from draftworx.core.vec import Vec


class TestLength:
    @pytest.mark.parametrize("text,expected", [
        ("100", 100.0), ("1.5", 1.5), (".5", 0.5), ("5.", 5.0), ("1e3", 1000.0), ("-5", -5.0), ("+5", 5.0),
        ("100/3", 100 / 3), ("2*(3+4)", 14.0), ("10-3-2", 5.0), ("2+3*4", 14.0), ("(2+3)*4", 20.0), ("--5", 5.0), (" 7 ", 7.0), ("2 * 3", 6.0),
        ("25mm", 25.0), ("1cm", 10.0), ("1m", 1000.0), ("1in", 25.4), ("1 in", 25.4), ("1ft", 304.8), ("2in+1mm", 51.8),
        ('1"', 25.4), ("1'", 304.8), ("3'4\"", 40 * 25.4), ("3'-4\"", 40 * 25.4), ("3' 4\"", 40 * 25.4), ('1/2"', 12.7), ('4 1/2"', 4.5 * 25.4),
        ('3\'4-1/2"', 40.5 * 25.4), ("3'4", 40 * 25.4), ('1.5"', 1.5 * 25.4),
    ])
    def test_in_mm(self, text, expected):
        assert parse_length(text, "mm") == pytest.approx(expected)

    def test_a_drawing_in_inches(self):
        assert parse_length("1", "in") == 1.0
        assert parse_length("25.4mm", "in") == pytest.approx(1.0)
        assert parse_length("2'", "in") == pytest.approx(24.0)

    def test_a_drawing_in_metres(self):
        assert parse_length("1000mm", "m") == pytest.approx(1.0)

    @pytest.mark.parametrize("text", ["", "abc", "1+", "(1", "1)", "1 2", "5xyz", "1/0", "1,2", "1<2", "*", "1'/0", "1/0\"", "..", "1e", "import os", "__import__('os')", "1e999"])
    def test_refused_with_a_message(self, text):
        with pytest.raises(TypedError):
            parse_length(text)

    @given(st.text(alphabet="0123456789.+-*/()'\" emincft", max_size=12))
    def test_anything_typed_either_reads_or_says_why(self, text):
        try:
            v = parse_length(text)
        except TypedError:
            return
        assert math.isfinite(v)

    def test_deep_brackets_do_not_crash(self):
        with pytest.raises(TypedError):
            parse_length("(" * 100000)


class TestAngle:
    def test_degrees_by_default(self):
        assert parse_angle("90") == pytest.approx(math.pi / 2)
        assert parse_angle("45+45") == pytest.approx(math.pi / 2)
        assert parse_angle("90°") == pytest.approx(math.pi / 2)
        assert parse_angle("90d") == pytest.approx(math.pi / 2)

    def test_other_units(self):
        assert parse_angle("1r") == pytest.approx(1.0)
        assert parse_angle("100g") == pytest.approx(math.pi / 2)

    def test_lengths_are_not_angles(self):
        with pytest.raises(TypedError):
            parse_angle("5mm")

    def test_plain_number(self):
        assert parse_number("3/4") == 0.75


class TestPoint:
    L = Vec(100, 200)

    def test_a_bare_number_is_a_length(self):
        t = parse_point("50", self.L)
        assert t.length == 50.0 and t.point is None and t.angle is None

    def test_comma_is_from_the_last_point(self):
        assert parse_point("10,20", self.L).point == Vec(110, 220)

    def test_comma_with_no_last_point_is_from_the_origin(self):
        assert parse_point("10,20", None).point == Vec(10, 20)

    def test_at_sign_is_from_the_last_point(self):
        assert parse_point("@10,20", self.L).point == Vec(110, 220)

    def test_hash_is_from_the_origin(self):
        assert parse_point("#10,20", self.L).point == Vec(10, 20)

    def test_at_sign_needs_a_last_point(self):
        with pytest.raises(TypedError, match="last point"):
            parse_point("@10,20", None)

    def test_polar(self):
        p = parse_point("10<90", self.L).point
        assert p.distance((100, 210)) < 1e-9
        p = parse_point("#10<0", self.L).point
        assert p.distance((10, 0)) < 1e-9

    def test_an_angle_to_hold(self):
        assert parse_point("<45", self.L).angle == pytest.approx(math.pi / 4)

    def test_sums_and_units_in_a_point(self):
        p = parse_point("100/2,1in", self.L).point
        assert p.distance((150, 225.4)) < 1e-9
        p = parse_point("(1+2)*3,4", None).point
        assert p == Vec(9, 4)

    @pytest.mark.parametrize("text", ["", "#", "@", "1,2,3", "a,b", "#5", "@5", "1<2<3", "<"])
    def test_refused(self, text):
        with pytest.raises(TypedError):
            parse_point(text, self.L)


class TestFormat:
    def test_decimal(self):
        assert format_length(125.5) == "125.50"
        assert format_length(125.5, decimals=0) == "126"
        assert format_length(-1.25, decimals=1) == "-1.2"

    def test_no_negative_zero(self):
        assert format_length(-0.001, decimals=2) == "0.00"
        assert format_length(-0.001, decimals=0) == "0"

    def test_scientific(self):
        assert format_length(1500.0, "scientific", 2) == "1.50e+03"

    def test_architectural(self):
        assert format_length(40.5, "architectural", 4, "in") == "3'-4 1/2\""
        assert format_length(36.0, "architectural", 4, "in") == "3'-0\""
        assert format_length(0.5, "architectural", 4, "in") == "0'-1/2\""
        assert format_length(40.5 * 25.4, "architectural", 4, "mm") == "3'-4 1/2\""

    def test_fractional(self):
        assert format_length(4.5, "fractional", 4, "in") == '4 1/2"'
        assert format_length(0.25, "fractional", 4, "in") == '1/4"'
        assert format_length(3.0, "fractional", 4, "in") == '3"'

    def test_engineering(self):
        assert format_length(40.5, "engineering", 2, "in") == "3'-4.50\""
        assert format_length(35.999, "engineering", 2, "in") == "3'-0.00\""

    def test_rounding_up_carries(self):
        assert format_length(35.999, "architectural", 4, "in") == "3'-0\""

    def test_angles(self):
        assert format_angle(math.pi / 2) == "90°"
        assert format_angle(math.pi / 4, 2) == "45°"
        assert format_angle(0.5, 1) == "28.6°"
        assert format_angle(-math.pi / 2) == "270°"
        assert format_angle(math.tau - 1e-9) == "0°"

    def test_unit_factor(self):
        assert unit_factor("in", "mm") == 25.4
        assert unit_factor("mm", "in") == pytest.approx(1 / 25.4)
        assert unit_factor("m", "none") == 1000.0
