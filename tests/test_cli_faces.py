import argparse
import csv
from pathlib import Path

import cli
import scoring
from scanner import PhotoMetadata


def test_faces_command_writes_one_row_per_face_and_compares_with_legacy(tmp_path, monkeypatch, capsys):
    with_faces, no_faces = tmp_path / "a.jpg", tmp_path / "b.jpg"
    monkeypatch.setattr(
        cli,
        "scan_and_extract",
        lambda path, recursive: [
            PhotoMetadata(path=with_faces, datetime_original=None, exif_source="exif"),
            PhotoMetadata(path=no_faces, datetime_original=None, exif_source="exif"),
        ],
    )
    faces = [
        scoring._DetectedFace(eyes_open=0.8, bbox=(0.0, 0.0, 0.1, 0.2), source="yunet", confidence=0.9),
        scoring._DetectedFace(eyes_open=None, bbox=(0.5, 0.5, 0.6, 0.6), source="yunet", confidence=0.7),
        scoring._DetectedFace(eyes_open=0.1, bbox=(0.9, 0.9, 0.91, 0.92), source="yunet", confidence=0.8),
    ]
    results = {
        with_faces: scoring.FaceDiagnosis(with_faces, 1000, 500, faces, legacy_faces=[]),
        no_faces: scoring.FaceDiagnosis(no_faces, 1000, 500, [], legacy_faces=[]),
    }
    monkeypatch.setattr(cli, "diagnose_faces", lambda path: results[path])
    output = tmp_path / "out" / "faces.csv"

    cli.cmd_faces(argparse.Namespace(input=str(tmp_path), recursive=False, output=str(output)))

    with output.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert [(Path(r["file"]).name, r["face_index"], r["eyes_open"]) for r in rows] == [
        ("a.jpg", "1", "0.800"),
        ("a.jpg", "2", ""),
        ("a.jpg", "3", "0.100"),
        ("b.jpg", "", ""),
    ]
    assert [r["background"] for r in rows[:3]] == ["", "", "Y"]
    assert rows[0]["face_width_px"] == "100" and rows[0]["face_height_px"] == "100"
    out = capsys.readouterr().out
    assert "얼굴을 찾은 사진: 1장 (예전 방식 0장)" in out
    assert "눈뜸을 판정할 수 있는 사진: 1장" in out
