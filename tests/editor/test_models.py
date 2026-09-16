"""Task 02 — document models, defaults and validation (spec §5–6).

Covers:
- the three schemas validate their own fixtures (Draft 2020-12),
- ``new_document`` defaults (200×200 mm, 5 mm padding, 3 mm extrusion,
  0 mm gap, revision 0, empty maps),
- the three-layer fixture validates unmodified,
- every unauthorized field / bad value produces a structured error,
- cycle A>B>C>A and missing asset are rejected before any geometry work,
- booleans-as-numbers, NaN and Infinity are rejected,
- duplicate sibling orders are rejected.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from silhouettes.editor.models import (
    DocumentError,
    new_document,
    validate_document,
)

ROOT = Path(__file__).resolve().parents[2]
REF = ROOT / "_ref" / "Silhouettes_editor_Qwen"
FIXTURE = REF / "fixtures" / "project" / "project.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# --- schemas ---------------------------------------------------------------


@pytest.mark.parametrize(
    "schema,fixture",
    [
        ("project.schema.json", "project/project.json"),
        ("fit-request.schema.json", "fit-request.json"),
        ("export-request.schema.json", "export-request.json"),
    ],
)
def test_schemas_are_valid_and_fixtures_pass(schema: str, fixture: str) -> None:
    schema_doc = json.loads((ROOT / "schemas" / schema).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema_doc)
    data = json.loads((REF / "fixtures" / fixture).read_text(encoding="utf-8"))
    Draft202012Validator(schema_doc).validate(data)


# --- new_document defaults ---------------------------------------------------


def test_new_document_defaults() -> None:
    doc = new_document("Prueba")
    assert doc["schema_version"] == 2
    assert doc["units"] == "mm"
    assert doc["revision"] == 0
    assert doc["canvas"]["width_mm"] == 200
    assert doc["canvas"]["height_mm"] == 200
    assert doc["canvas"]["padding_mm"] == {
        "top": 5, "right": 5, "bottom": 5, "left": 5,
    }
    assert doc["default_extrusion_mm"] == 3
    assert doc["stack_gap_mm"] == 0
    assert doc["assets"] == {}
    assert doc["layers"] == {}
    assert doc["name"] == "Prueba"
    assert doc["id"]  # non-empty, pattern-checked by the schema


def test_new_document_accepts_canvas_override() -> None:
    doc = new_document("Otro", canvas={"width_mm": 300, "height_mm": 150})
    assert doc["canvas"]["width_mm"] == 300
    assert doc["canvas"]["height_mm"] == 150
    # padding defaults are preserved unless overridden
    assert doc["canvas"]["padding_mm"]["left"] == 5


def test_new_document_rejects_bad_name() -> None:
    with pytest.raises(DocumentError):
        new_document("")
    with pytest.raises(DocumentError):
        new_document("x" * 201)


# --- fixture validates unmodified -------------------------------------------


def test_three_layer_fixture_validates_unmodified() -> None:
    doc = load_fixture()
    assert validate_document(doc) is doc  # returned unchanged, not mutated
    assert doc["layers"]["B"]["parent_id"] == "A"
    assert doc["layers"]["C"]["parent_id"] == "B"


# --- structured rejections ---------------------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 1),
        ("units", "px"),
        ("revision", -1),
        ("name", ""),
        ("unknown_field", 1),
    ],
)
def test_invalid_project_fields_rejected(field: str, value) -> None:
    doc = load_fixture()
    doc[field] = value
    with pytest.raises(DocumentError) as exc:
        validate_document(doc)
    assert exc.value.code in ("SCHEMA", "BAD_NAME")


@pytest.mark.parametrize("value", [0, -1, True])
def test_bad_scale_rejected(value) -> None:
    doc = load_fixture()
    doc["layers"]["A"]["pose"]["scale"] = value
    with pytest.raises(DocumentError):
        validate_document(doc)


def test_zero_extrusion_rejected_and_null_is_inheritance() -> None:
    doc = load_fixture()
    doc["layers"]["A"]["extrusion_mm"] = 0
    with pytest.raises(DocumentError):
        validate_document(doc)
    # null must remain legal (means "inherit document default")
    doc = load_fixture()
    doc["layers"]["A"]["extrusion_mm"] = None
    validate_document(doc)


def test_boolean_as_number_rejected() -> None:
    doc = load_fixture()
    doc["canvas"]["width_mm"] = True
    with pytest.raises(DocumentError) as exc:
        validate_document(doc)
    assert exc.value.code == "BAD_NUMBER"

    doc = load_fixture()
    doc["layers"]["A"]["pose"]["tx"] = False
    with pytest.raises(DocumentError) as exc:
        validate_document(doc)
    assert exc.value.code == "BAD_NUMBER"


def test_nan_and_infinity_rejected() -> None:
    doc = load_fixture()
    doc["layers"]["A"]["pose"]["tx"] = float("nan")
    with pytest.raises(DocumentError) as exc:
        validate_document(doc)
    assert exc.value.code == "BAD_NUMBER"

    doc = load_fixture()
    doc["layers"]["A"]["pose"]["scale"] = float("inf")
    with pytest.raises(DocumentError) as exc:
        validate_document(doc)
    assert exc.value.code == "BAD_NUMBER"


def test_cycle_rejected_before_geometry() -> None:
    doc = load_fixture()
    # A > B > C, now C > A closes the cycle
    doc["layers"]["A"]["parent_id"] = "C"
    with pytest.raises(DocumentError) as exc:
        validate_document(doc)
    assert exc.value.code == "CYCLE"


def test_self_parent_rejected() -> None:
    doc = load_fixture()
    doc["layers"]["A"]["parent_id"] = "A"
    with pytest.raises(DocumentError) as exc:
        validate_document(doc)
    assert exc.value.code == "CYCLE"


def test_missing_asset_rejected() -> None:
    doc = load_fixture()
    doc["layers"]["B"]["asset_id"] = "nope"
    with pytest.raises(DocumentError) as exc:
        validate_document(doc)
    assert exc.value.code == "MISSING_ASSET"


def test_missing_parent_rejected() -> None:
    doc = load_fixture()
    doc["layers"]["B"]["parent_id"] = "ghost"
    with pytest.raises(DocumentError) as exc:
        validate_document(doc)
    assert exc.value.code == "MISSING_PARENT"


def test_id_must_match_map_key() -> None:
    doc = load_fixture()
    doc["layers"]["A"]["id"] = "Z"
    with pytest.raises(DocumentError) as exc:
        validate_document(doc)
    assert exc.value.code == "ID_MISMATCH"


def test_duplicate_sibling_orders_rejected() -> None:
    doc = load_fixture()
    # B and a new sibling D under A both claim order 0
    doc["layers"]["D"] = copy.deepcopy(doc["layers"]["B"])
    doc["layers"]["D"]["id"] = "D"
    with pytest.raises(DocumentError) as exc:
        validate_document(doc)
    assert exc.value.code == "DUPLICATE_ORDER"


def test_padding_cannot_consume_canvas() -> None:
    doc = load_fixture()
    doc["canvas"]["padding_mm"]["left"] = 100
    doc["canvas"]["padding_mm"]["right"] = 100
    with pytest.raises(DocumentError) as exc:
        validate_document(doc)
    assert exc.value.code == "CANVAS_PADDING"


def test_validate_does_not_mutate_input() -> None:
    doc = load_fixture()
    before = json.dumps(doc, sort_keys=True)
    validate_document(doc)
    assert json.dumps(doc, sort_keys=True) == before


def test_non_mapping_document_rejected() -> None:
    with pytest.raises(DocumentError) as exc:
        validate_document([1, 2, 3])  # type: ignore[arg-type]
    assert exc.value.code == "NOT_A_DOCUMENT"
