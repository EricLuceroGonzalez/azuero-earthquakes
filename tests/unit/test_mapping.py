"""Tests de las funciones puras que traducen magnitud a sonido y tamaño."""

import numpy as np
import pytest

from sismos_azuero.main import BUCKETS, bucket_for, mag_to_area, mag_to_gain, main


@pytest.mark.parametrize(
    ("mag", "expected"),
    [(2.5, "m2"), (3.0, "m3"), (4.9, "m4"), (5.0, "m5"), (6.4, "m6"), (7.7, "m7")],
)
def test_bucket_for(mag, expected):
    assert bucket_for(mag).name == expected


def test_buckets_sorted_and_valid_durations():
    mins = [b.min_mag for b in BUCKETS]
    assert mins == sorted(mins)
    # La API de Sound Effects acepta de 0.5 a 30 segundos.
    assert all(0.5 <= b.duration <= 30 for b in BUCKETS)


def test_gain_grows_with_magnitude_and_never_clips():
    gains = [mag_to_gain(m) for m in np.arange(2.5, 9.0, 0.5)]
    assert gains == sorted(gains)
    assert max(gains) < 1.0


def test_area_grows_with_magnitude():
    areas = mag_to_area(np.array([3.0, 4.0, 5.0]))
    assert np.all(np.diff(areas) > 0)


def test_cli_requires_subcommand():
    with pytest.raises(SystemExit):
        main([])


def test_regions_inside_catalog_bbox():
    from sismos_azuero.main import BBOX, REGIONS

    for region in REGIONS.values():
        b = region["bbox"]
        assert BBOX["minlatitude"] <= b["minlatitude"] < b["maxlatitude"]
        assert b["maxlatitude"] <= BBOX["maxlatitude"]
        assert BBOX["minlongitude"] <= b["minlongitude"] < b["maxlongitude"]
        assert b["maxlongitude"] <= BBOX["maxlongitude"]
