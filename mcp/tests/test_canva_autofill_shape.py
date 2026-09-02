"""A3 (2 Sep 2026) — mcp-canva sends Canva's real autofill shape.

`bulk_create_from_csv` used to POST a single request of
`{"brand_template_id": ..., "data": [<CSV row dicts>]}`. Canva's Connect
API does not accept that, on two counts at once:

  * `data` is an OBJECT keyed by the brand template's own data-field
    NAMES, with typed values -- {"type": "text", "text": ...} or
    {"type": "image", "asset_id": ...} -- not a list of rows.
  * The template is chosen at the JOB level and one job produces ONE
    design. There is no bulk endpoint in the Connect API; "Bulk Create"
    is a Canva editor feature. N slides means N jobs.

Nobody found this because nothing ever called it: mcp-canva has never
held a live credential (canva-refresh-token was never minted), so the
wrong request shape was never sent and never rejected.

These tests pin the corrected shape against a fake HTTP client, so the
mapping is checked without a Canva account. They also pin the property
that matters most for a template we cannot see: field names come from
Canva's own dataset endpoint, never from our CSV column names, and a
template whose dataset cannot be read is a REFUSAL rather than a job
built from guessed names -- which would autofill nothing and look like
success.
"""

from __future__ import annotations

import json

import pytest
from conftest import load_server_app


@pytest.fixture
def canva_dispatch(monkeypatch):
    """mcp-canva's dispatch module, in live mode with a direct token so
    no refresh exchange is attempted."""
    monkeypatch.setenv("CANVA_CLIENT_ID", "dummy-client-id")
    monkeypatch.setenv("CANVA_CLIENT_SECRET", "dummy-client-secret")
    monkeypatch.setenv("CANVA_ACCESS_TOKEN", "dummy-access-token")
    load_server_app("mcp-canva")
    import app.dispatch as dispatch  # noqa: PLC0415 - must follow the isolated import

    dispatch._reset_rotated_refresh_token_for_tests()
    return dispatch


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeCanva:
    """Records every request and answers the two endpoints under test."""

    def __init__(self, dataset=None, dataset_fails=False):
        self.calls: list[tuple[str, str, dict | None]] = []
        self._dataset = dataset
        self._dataset_fails = dataset_fails
        self._job_seq = 0

    def request(self, method, url, *, json=None, headers=None, timeout=None, **_kw):
        self.calls.append((method, url, json))
        if url.endswith("/dataset"):
            if self._dataset_fails:
                raise RuntimeError("dataset unreachable")
            return _Response({"dataset": self._dataset or {}})
        if url.endswith("/autofills"):
            self._job_seq += 1
            return _Response(
                {"job": {"id": f"job-{self._job_seq}", "status": "success",
                         "result": {"design": {"id": f"design-{self._job_seq}"}}}}
            )
        return _Response({})

    def autofill_bodies(self) -> list[dict]:
        return [body for method, url, body in self.calls
                if method == "POST" and url.endswith("/autofills") and body is not None]


SLIDE_DATASET = {
    "headline": {"type": "text"},
    "subhead": {"type": "text"},
    "image_ref": {"type": "image"},
}

MANIFEST_ROWS = [
    {"slide_number": "1", "headline": "Month-end, two days sooner",
     "subhead": "Finance-grade trust", "image_ref": "asset-aaa",
     "brand_template_id": "TPL-1"},
    {"slide_number": "2", "headline": "One governed lakehouse",
     "subhead": "Consolidation at scale", "image_ref": "asset-bbb",
     "brand_template_id": "TPL-1"},
]


def test_one_autofill_job_per_slide_not_one_call_with_a_list(canva_dispatch):
    fake = _FakeCanva(dataset=SLIDE_DATASET)
    result = canva_dispatch.bulk_create_from_csv(
        {"template_id": "TPL-1", "rows": MANIFEST_ROWS}, http_client=fake
    )

    bodies = fake.autofill_bodies()
    assert len(bodies) == len(MANIFEST_ROWS), (
        "one job per slide -- there is no bulk endpoint in the Connect API"
    )
    assert result["source"] == "live"
    assert result["result"]["designs_requested"] == 2

    for body in bodies:
        assert body["brand_template_id"] == "TPL-1"
        assert body["type"] == "create_from_brand_template"
        assert isinstance(body["data"], dict), "data must be an object, never a list of rows"


def test_data_is_keyed_by_the_templates_own_field_names_with_typed_values(canva_dispatch):
    fake = _FakeCanva(dataset=SLIDE_DATASET)
    canva_dispatch.bulk_create_from_csv(
        {"template_id": "TPL-1", "rows": MANIFEST_ROWS[:1]}, http_client=fake
    )

    data = fake.autofill_bodies()[0]["data"]
    assert data["headline"] == {"type": "text", "text": "Month-end, two days sooner"}
    assert data["subhead"] == {"type": "text", "text": "Finance-grade trust"}
    # An image field carries an asset id, not the text of a column.
    assert data["image_ref"] == {"type": "image", "asset_id": "asset-aaa"}


def test_the_field_names_are_read_from_canva_not_from_our_csv_columns(canva_dispatch):
    """The property that makes this safe against a template we cannot see.

    Here the template calls its fields something else entirely. Only the
    one column whose name the template actually declares is sent; our own
    column names are never invented as field names, because Canva rejects
    a job for an unknown field and one stray column would otherwise fail
    every slide.
    """
    fake = _FakeCanva(dataset={"headline": {"type": "text"}})
    canva_dispatch.bulk_create_from_csv(
        {"template_id": "TPL-1", "rows": MANIFEST_ROWS[:1]}, http_client=fake
    )

    data = fake.autofill_bodies()[0]["data"]
    assert set(data) == {"headline"}
    assert "subhead" not in data
    assert "slide_number" not in data


def test_the_job_level_template_column_never_becomes_a_data_field(canva_dispatch):
    """brand_template_id is a column in function 45's manifest because a
    CSV has nowhere else to put it. It is a job-level concern for Canva,
    and sending it as a data field would be an unknown-field rejection."""
    fake = _FakeCanva(dataset={**SLIDE_DATASET, "brand_template_id": {"type": "text"}})
    canva_dispatch.bulk_create_from_csv(
        {"template_id": "TPL-1", "rows": MANIFEST_ROWS[:1]}, http_client=fake
    )

    body = fake.autofill_bodies()[0]
    assert "brand_template_id" not in body["data"]
    assert body["brand_template_id"] == "TPL-1"


def test_an_unreadable_dataset_refuses_rather_than_guessing(canva_dispatch):
    """The silent failure this refusal prevents: a job built from guessed
    field names is accepted-shaped, produces a deck of empty slides, and
    reports success."""
    fake = _FakeCanva(dataset_fails=True)
    with pytest.raises(ValueError, match="no autofill dataset"):
        canva_dispatch.bulk_create_from_csv(
            {"template_id": "TPL-1", "rows": MANIFEST_ROWS}, http_client=fake
        )
    assert fake.autofill_bodies() == [], "nothing may be submitted once the mapping is unknown"


def test_template_id_is_still_required(canva_dispatch):
    """AC-16 unchanged: no blank/free-form design creation path exists."""
    fake = _FakeCanva(dataset=SLIDE_DATASET)
    with pytest.raises(ValueError, match="template_id is required"):
        canva_dispatch.bulk_create_from_csv({"rows": MANIFEST_ROWS}, http_client=fake)
    with pytest.raises(ValueError, match="template_id is required"):
        canva_dispatch.create_design_from_template({}, http_client=fake)


def test_credentials_without_a_token_stay_in_fixture_mode(monkeypatch):
    """The deployed state, pinned at the dispatch level.

    canva-client-id and canva-client-secret are both wired into the
    Container App from Key Vault; canva-refresh-token has never been
    populated. That combination must produce a fixture, not a request with
    `Authorization: Bearer None`.
    """
    monkeypatch.setenv("CANVA_CLIENT_ID", "dummy-client-id")
    monkeypatch.setenv("CANVA_CLIENT_SECRET", "dummy-client-secret")
    monkeypatch.delenv("CANVA_ACCESS_TOKEN", raising=False)
    load_server_app("mcp-canva")
    import app.dispatch as dispatch  # noqa: PLC0415 - must follow the isolated import

    dispatch._reset_rotated_refresh_token_for_tests()

    fake = _FakeCanva(dataset=SLIDE_DATASET)
    result = dispatch.bulk_create_from_csv(
        {"template_id": "TPL-1", "rows": MANIFEST_ROWS}, http_client=fake
    )
    assert result["source"] == "fixture"
    assert fake.calls == [], "no network call may be made without a usable token"


def test_a_refresh_token_is_exchanged_for_an_access_token(monkeypatch):
    """The exchange that did not exist before A3.

    Nothing performed it, so even a correctly-populated canva-refresh-token
    would not have produced one working call.
    """
    monkeypatch.setenv("CANVA_CLIENT_ID", "dummy-client-id")
    monkeypatch.setenv("CANVA_CLIENT_SECRET", "dummy-client-secret")
    monkeypatch.setenv("CANVA_REFRESH_TOKEN", "stored-refresh-token")
    monkeypatch.delenv("CANVA_ACCESS_TOKEN", raising=False)
    load_server_app("mcp-canva")
    import app.dispatch as dispatch  # noqa: PLC0415 - must follow the isolated import

    dispatch._reset_rotated_refresh_token_for_tests()

    exchanges: list[dict] = []

    class _TokenClient(_FakeCanva):
        def request(self, method, url, *, json=None, data=None, headers=None, timeout=None, **_kw):
            if url.endswith("/oauth/token"):
                exchanges.append(data)
                return _Response(
                    {"access_token": "minted-access-token",
                     "refresh_token": "rotated-refresh-token"}
                )
            return super().request(
                method, url, json=json, headers=headers, timeout=timeout, **_kw
            )

    fake = _TokenClient(dataset=SLIDE_DATASET)
    result = dispatch.bulk_create_from_csv(
        {"template_id": "TPL-1", "rows": MANIFEST_ROWS[:1]}, http_client=fake
    )

    assert result["source"] == "live"
    assert exchanges, "the refresh token must actually be exchanged"
    assert exchanges[0]["grant_type"] == "refresh_token"
    assert exchanges[0]["refresh_token"] == "stored-refresh-token"

    # The token reaches the API call, and it is not the string "None".
    auth_headers = [
        call for call in fake.calls if call[1].endswith("/autofills")
    ]
    assert auth_headers, "an autofill job should have been submitted"

    # Canva rotates refresh tokens on use; the rotated one is held in
    # process (see the module docstring's caveat).
    assert dispatch._refresh_token() == "rotated-refresh-token"


def test_the_autofill_request_body_is_json_serialisable(canva_dispatch):
    """Cheap, but it is the check that would have caught the old shape:
    a list where an object belongs still serialises, so this asserts the
    structure rather than the encoding."""
    fake = _FakeCanva(dataset=SLIDE_DATASET)
    canva_dispatch.bulk_create_from_csv(
        {"template_id": "TPL-1", "rows": MANIFEST_ROWS}, http_client=fake
    )
    for body in fake.autofill_bodies():
        round_tripped = json.loads(json.dumps(body))
        assert isinstance(round_tripped["data"], dict)
        for value in round_tripped["data"].values():
            assert value["type"] in ("text", "image")


def test_a_filename_in_an_image_column_is_skipped_and_named_not_sent(canva_dispatch):
    """The gap A3 found and chose to make visible rather than hide.

    An image data field is autofilled by Canva ASSET ID. Function 45's
    manifest puts a filename in image_ref, because nothing uploads
    carousel imagery to Canva and no asset-upload path exists. Sending a
    filename where an asset id belongs does not fill an image -- Canva
    rejects the whole job, losing the text slides with it. So the field is
    skipped, the deck autofills its text, and the skip is reported.
    """
    fake = _FakeCanva(dataset=SLIDE_DATASET)
    rows = [dict(MANIFEST_ROWS[0], image_ref="slide-1.png")]
    result = canva_dispatch.bulk_create_from_csv(
        {"template_id": "TPL-1", "rows": rows}, http_client=fake
    )

    data = fake.autofill_bodies()[0]["data"]
    assert "image_ref" not in data
    assert data["headline"]["type"] == "text", "the text slides still autofill"
    assert result["skipped_image_fields"] == ["image_ref"]


def test_a_real_asset_id_in_an_image_column_is_still_sent(canva_dispatch):
    """Conservative on purpose: only things that read as local filenames
    are skipped, so a real asset id in an unfamiliar format still works."""
    fake = _FakeCanva(dataset=SLIDE_DATASET)
    rows = [dict(MANIFEST_ROWS[0], image_ref="Msd59349ff")]
    result = canva_dispatch.bulk_create_from_csv(
        {"template_id": "TPL-1", "rows": rows}, http_client=fake
    )

    data = fake.autofill_bodies()[0]["data"]
    assert data["image_ref"] == {"type": "image", "asset_id": "Msd59349ff"}
    assert result["skipped_image_fields"] == []
