import datetime

from grouping import group_by_time_gap
from scanner import PhotoMetadata


def _meta(seconds_offset: int) -> PhotoMetadata:
    base = datetime.datetime(2026, 6, 1, 14, 0, 0)
    dt = base + datetime.timedelta(seconds=seconds_offset)
    return PhotoMetadata(
        path=f"/fake/{seconds_offset}.jpg",
        datetime_original=dt,
        exif_source="exif",
    )


def test_empty_input_returns_no_groups():
    assert group_by_time_gap([]) == []


def test_single_photo_is_its_own_group():
    photos = [_meta(0)]

    groups = group_by_time_gap(photos)

    assert groups == [[photos[0]]]


def test_photos_within_gap_join_same_scene():
    photos = [_meta(0), _meta(2)]

    groups = group_by_time_gap(photos, gap_seconds=3)

    assert groups == [[photos[0], photos[1]]]


def test_photo_exactly_at_gap_boundary_joins_same_scene():
    photos = [_meta(0), _meta(3)]

    groups = group_by_time_gap(photos, gap_seconds=3)

    assert groups == [[photos[0], photos[1]]]


def test_photo_beyond_gap_starts_new_scene():
    photos = [_meta(0), _meta(4)]

    groups = group_by_time_gap(photos, gap_seconds=3)

    assert groups == [[photos[0]], [photos[1]]]


def test_multiple_scenes_are_split_correctly():
    photos = [_meta(0), _meta(2), _meta(10), _meta(11)]

    groups = group_by_time_gap(photos, gap_seconds=3)

    assert groups == [[photos[0], photos[1]], [photos[2], photos[3]]]


def test_default_gap_is_three_seconds():
    photos = [_meta(0), _meta(3), _meta(7)]

    groups = group_by_time_gap(photos)

    assert groups == [[photos[0], photos[1]], [photos[2]]]


# --- 비슷하지 않은 사진에서 장면 나누기 ---------------------------------------

from grouping import distances_to_previous, split_dissimilar  # noqa: E402


def test_split_dissimilar_cuts_scene_where_photo_changes_a_lot():
    photos = [_meta(0), _meta(1), _meta(2), _meta(3)]
    # 세 번째 사진에서 피사체가 바뀜(앞 사진과 64비트 중 40비트 차이)
    hashes = {photos[0].path: 0, photos[1].path: 0b11, photos[2].path: (1 << 40) - 1, photos[3].path: (1 << 40) - 1}

    groups = split_dissimilar([photos], hashes, max_distance=20)

    assert groups == [[photos[0], photos[1]], [photos[2], photos[3]]]


def test_split_dissimilar_keeps_scene_when_difference_is_at_threshold():
    photos = [_meta(0), _meta(1)]
    hashes = {photos[0].path: 0, photos[1].path: (1 << 20) - 1}  # 정확히 20비트 차이

    assert split_dissimilar([photos], hashes, max_distance=20) == [photos]


def test_split_dissimilar_never_merges_separate_time_scenes():
    first, second = [_meta(0)], [_meta(100)]
    hashes = {first[0].path: 0, second[0].path: 0}  # 똑같은 사진이어도

    assert split_dissimilar([first, second], hashes, max_distance=20) == [first, second]


def test_split_dissimilar_does_not_cut_around_unreadable_photo():
    photos = [_meta(0), _meta(1), _meta(2)]
    hashes = {photos[0].path: 0, photos[1].path: None, photos[2].path: (1 << 40) - 1}

    assert split_dissimilar([photos], hashes, max_distance=20) == [photos]


def test_distances_to_previous_in_time_order():
    photos = [_meta(0), _meta(1), _meta(2)]
    hashes = {photos[0].path: 0, photos[1].path: 0b111, photos[2].path: None}

    assert distances_to_previous(photos, hashes) == {
        photos[0].path: None,
        photos[1].path: 3,
        photos[2].path: None,
    }


def _photos_at(*seconds):
    import datetime as _dt
    from pathlib import Path as _P

    from scanner import PhotoMetadata

    base = _dt.datetime(2026, 5, 1, 10, 0, 0)
    return [
        PhotoMetadata(path=_P(f"/s/{i}.jpg"), datetime_original=base + _dt.timedelta(seconds=s), exif_source="exif")
        for i, s in enumerate(seconds)
    ]


def _names(groups):
    return [[p.path.stem for p in g] for g in groups]


def test_apply_scene_edits_splits_and_joins_by_path():
    from pathlib import Path as _P

    from grouping import JOIN, SPLIT, apply_scene_edits, group_by_time_gap

    photos = _photos_at(0, 1, 2, 10, 11)
    groups = group_by_time_gap(photos, gap_seconds=3)
    assert _names(groups) == [["0", "1", "2"], ["3", "4"]]

    split = apply_scene_edits(groups, {_P("/s/2.jpg"): SPLIT})
    assert _names(split) == [["0", "1"], ["2"], ["3", "4"]]

    joined = apply_scene_edits(groups, {_P("/s/3.jpg"): JOIN})
    assert _names(joined) == [["0", "1", "2", "3", "4"]]

    # 이미 경계인 자리의 SPLIT, 경계가 아닌 자리의 JOIN, 맨 앞의 JOIN은 아무것도 안 바꿈
    same = apply_scene_edits(groups, {_P("/s/3.jpg"): SPLIT, _P("/s/1.jpg"): JOIN, _P("/s/0.jpg"): JOIN})
    assert _names(same) == _names(groups)
    assert apply_scene_edits(groups, {}) is groups
