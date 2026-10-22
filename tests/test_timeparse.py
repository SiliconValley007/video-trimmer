import pytest

from video_trimmer.timeparse import format_time, parse_time


@pytest.mark.parametrize("text,expected", [
    ("90", 90), ("1:30", 90), ("01:01:30", 3690), ("0:0:5.5", 5.5), (" 12.25 ", 12.25), (".5", 0.5),
    ("1:30.250", 90.25), ("00:00:01.5", 1.5), ("1:30,5", 90.5), ("0", 0), ("5.", 5), ("100:00", 6000),
    ("00:00:59.999", 59.999), ("10:00:00", 36000),
])
def test_parse_ok(text, expected):
    assert parse_time(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", [
    "", "   ", "abc", "1:2:3:4", "1:75", "1:61:00", "-5", "1.5:30", "inf", "nan", "1e3", "1::2",
    "1,2,3", "1:30:", ":30", "1.2.3", "00:60", "00:00:60", "1 2",
])
def test_parse_bad(text):
    with pytest.raises(ValueError):
        parse_time(text)


def test_parse_none_is_error():
    with pytest.raises(ValueError):
        parse_time(None)  # type: ignore[arg-type]


def test_format_roundtrip_and_clamp():
    assert format_time(3690.5) == "01:01:30.500"
    assert parse_time(format_time(3690.5)) == 3690.5
    assert format_time(-1) == "00:00:00.000"
    assert format_time(0.0004) == "00:00:00.000"
    assert format_time(359999.999) == "99:59:59.999"
