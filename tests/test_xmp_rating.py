import xml.etree.ElementTree as ET

import pytest

from xmp_rating import (
    ACTION_CREATED,
    ACTION_SKIPPED,
    ACTION_UPDATED,
    NS_RDF,
    NS_XMP,
    NS_XMPDM,
    UnsupportedXmp,
    plan_xmp,
    set_values,
    undo_xmp,
    write_xmp,
)

LIGHTROOM_XMP = """<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core 7.0-c000">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/"
   xmp:Rating="2"
   crs:Exposure2012="+0.35">
   <crs:ToneCurvePV2012>
    <rdf:Seq><rdf:li>0, 0</rdf:li></rdf:Seq>
   </crs:ToneCurvePV2012>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
"""


def _values(path):
    root = ET.fromstring(path.read_bytes())
    description = next(root.iter(f"{{{NS_RDF}}}Description"))
    return {
        "rating": description.attrib.get(f"{{{NS_XMP}}}Rating"),
        "pick": description.attrib.get(f"{{{NS_XMPDM}}}pick"),
    }


def _shoot(tmp_path):
    """JPG+RAW 2쌍, JPG만 1장, RAW만 1장, RAW/ 하위 폴더 짝 1쌍."""
    for name in ("a", "b", "jpgonly", "sub"):
        (tmp_path / f"{name}.jpg").write_bytes(b"jpg")
    (tmp_path / "a.CR3").write_bytes(b"raw")
    (tmp_path / "b.CR3").write_bytes(b"raw")
    (tmp_path / "rawonly.NEF").write_bytes(b"raw")
    (tmp_path / "RAW").mkdir()
    (tmp_path / "RAW" / "sub.CR3").write_bytes(b"raw")
    return tmp_path


def test_plan_finds_raw_and_skips_jpg_only(tmp_path):
    d = _shoot(tmp_path)
    rows = plan_xmp([d / "a.jpg", d / "jpgonly.jpg", d / "rawonly.NEF", d / "sub.jpg"], [d / "b.jpg"])

    by_photo = {r.photo.name: r for r in rows}
    assert by_photo["a.jpg"].xmp == d / "a.xmp"
    assert by_photo["jpgonly.jpg"].raw is None
    assert by_photo["rawonly.NEF"].xmp == d / "rawonly.xmp"
    assert by_photo["sub.jpg"].xmp == d / "RAW" / "sub.xmp"
    assert by_photo["b.jpg"].mark == "reject"


def test_write_creates_rating_and_reject_and_leaves_raw_alone(tmp_path):
    d = _shoot(tmp_path)
    rows = plan_xmp([d / "a.jpg", d / "jpgonly.jpg"], [d / "b.jpg"])

    results = write_xmp(rows, 3, tmp_path / "run")

    assert [r.action for r in results] == [ACTION_CREATED, ACTION_SKIPPED, ACTION_CREATED]
    assert _values(d / "a.xmp") == {"rating": "3", "pick": None}
    assert _values(d / "b.xmp") == {"rating": "-1", "pick": "-1"}
    assert (d / "a.CR3").read_bytes() == b"raw" and (d / "a.jpg").read_bytes() == b"jpg"
    assert not (d / "jpgonly.xmp").exists()
    assert (tmp_path / "run" / "xmp_log.jsonl").read_text(encoding="utf-8").count("\n") == 3


def test_existing_lightroom_xmp_keeps_everything_but_rating(tmp_path):
    d = _shoot(tmp_path)
    (d / "a.xmp").write_text(LIGHTROOM_XMP, encoding="utf-8")

    results = write_xmp(plan_xmp([d / "a.jpg"], []), 5, tmp_path / "run")

    text = (d / "a.xmp").read_text(encoding="utf-8")
    assert results[0].action == ACTION_UPDATED
    assert text == LIGHTROOM_XMP.replace('xmp:Rating="2"', 'xmp:Rating="5"')
    assert results[0].backup.read_text(encoding="utf-8") == LIGHTROOM_XMP


def test_reject_adds_pick_with_namespace_declaration(tmp_path):
    d = _shoot(tmp_path)
    (d / "b.xmp").write_text(LIGHTROOM_XMP, encoding="utf-8")

    write_xmp(plan_xmp([], [d / "b.jpg"]), 3, tmp_path / "run")

    assert _values(d / "b.xmp") == {"rating": "-1", "pick": "-1"}
    assert 'crs:Exposure2012="+0.35"' in (d / "b.xmp").read_text(encoding="utf-8")


def test_best_clears_an_earlier_reject(tmp_path):
    d = _shoot(tmp_path)
    write_xmp(plan_xmp([], [d / "a.jpg"]), 3, tmp_path / "run1")

    write_xmp(plan_xmp([d / "a.jpg"], []), 4, tmp_path / "run2")

    assert _values(d / "a.xmp") == {"rating": "4", "pick": "0"}


def test_element_form_rating_is_updated():
    text = (
        f'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="{NS_RDF}">'
        f'<rdf:Description rdf:about="" xmlns:xmp="{NS_XMP}"><xmp:Rating>1</xmp:Rating>'
        "</rdf:Description></rdf:RDF></x:xmpmeta>"
    )

    assert "<xmp:Rating>4</xmp:Rating>" in set_values(text, {("xmp", "Rating"): 4})


def test_unsafe_xmp_is_refused():
    with pytest.raises(UnsupportedXmp):
        set_values("<not xml", {("xmp", "Rating"): 3})
    other = LIGHTROOM_XMP.replace('xmlns:xmp="http://ns.adobe.com/xap/1.0/"', 'xmlns:xmp="urn:other"')
    with pytest.raises(UnsupportedXmp):
        set_values(other, {("xmp", "Rating"): 3})


def test_unsafe_existing_file_is_skipped_untouched(tmp_path):
    d = _shoot(tmp_path)
    (d / "a.xmp").write_text("<broken", encoding="utf-8")

    results = write_xmp(plan_xmp([d / "a.jpg"], []), 3, tmp_path / "run")

    assert results[0].action == ACTION_SKIPPED
    assert (d / "a.xmp").read_text(encoding="utf-8") == "<broken"


def test_uppercase_existing_sidecar_is_reused(tmp_path):
    d = _shoot(tmp_path)
    (d / "a.XMP").write_text(LIGHTROOM_XMP, encoding="utf-8")

    rows = plan_xmp([d / "a.jpg"], [])

    assert rows[0].xmp.name == "a.XMP" and rows[0].existing


def test_undo_removes_created_and_restores_updated(tmp_path):
    d = _shoot(tmp_path)
    (d / "a.xmp").write_text(LIGHTROOM_XMP, encoding="utf-8")
    results = write_xmp(plan_xmp([d / "a.jpg"], [d / "b.jpg"]), 5, tmp_path / "run")

    restored, skipped = undo_xmp(results)

    assert restored == 2 and skipped == []
    assert (d / "a.xmp").read_text(encoding="utf-8") == LIGHTROOM_XMP
    assert not (d / "b.xmp").exists()


def test_undo_leaves_files_changed_afterwards(tmp_path):
    d = _shoot(tmp_path)
    results = write_xmp(plan_xmp([d / "a.jpg"], []), 5, tmp_path / "run")
    (d / "a.xmp").write_text(LIGHTROOM_XMP, encoding="utf-8")  # 라이트룸이 다시 씀

    restored, skipped = undo_xmp(results)

    assert restored == 0 and len(skipped) == 1
    assert (d / "a.xmp").read_text(encoding="utf-8") == LIGHTROOM_XMP


def test_rating_out_of_range_is_refused(tmp_path):
    with pytest.raises(ValueError):
        write_xmp([], 6, tmp_path / "run")


def test_client_label_creates_or_updates_only_the_label(tmp_path):
    from xmp_rating import MARK_LABEL, undo_xmp

    shoot = tmp_path / "shoot"
    shoot.mkdir()
    (shoot / "a.jpg").write_bytes(b"jpg")
    (shoot / "a.CR3").write_bytes(b"raw")
    (shoot / "b.CR3").write_bytes(b"raw")
    existing = (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:Rating="4" xmp:Label="Red"'
        ' xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/" crs:Exposure2012="+0.50"/>'
        "</rdf:RDF></x:xmpmeta>"
    )
    (shoot / "b.xmp").write_text(existing, encoding="utf-8")
    (shoot / "c.jpg").write_bytes(b"jpg only")

    rows = plan_xmp([], [], [shoot / "a.jpg", shoot / "b.CR3", shoot / "c.jpg"])
    assert [row.mark for row in rows] == [MARK_LABEL] * 3
    results = write_xmp(rows, 0, tmp_path / "run", label='녹색 & "고객"')

    assert [r.action for r in results] == ["created", "updated", "skipped"]
    created = (shoot / "a.xmp").read_text(encoding="utf-8")
    assert 'xmp:Label="녹색 &amp; &quot;고객&quot;"' in created
    assert "Rating" not in created  # 별점은 건드리지 않음
    updated = (shoot / "b.xmp").read_text(encoding="utf-8")
    assert 'xmp:Rating="4"' in updated and 'crs:Exposure2012="+0.50"' in updated
    assert "Red" not in updated and "녹색 &amp;" in updated

    restored, skipped = undo_xmp(results)
    assert restored == 2 and not skipped
    assert not (shoot / "a.xmp").exists()
    assert (shoot / "b.xmp").read_text(encoding="utf-8") == existing


def test_label_must_not_be_empty(tmp_path):
    shoot = tmp_path / "s"
    shoot.mkdir()
    (shoot / "a.CR3").write_bytes(b"raw")
    with pytest.raises(ValueError):
        write_xmp(plan_xmp([], [], [shoot / "a.CR3"]), 0, tmp_path / "run", label="  ")
