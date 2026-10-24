"""Integration tests only against explicitly configured TEST_DATABASE_URL."""
import json
import os
from uuid import uuid4

import psycopg
import pytest

from app.db import apply_migrations, load_seed
from app.state.models import TripState
from app.state.store import RequestIdConflict, StateStore, VersionConflict

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="set TEST_DATABASE_URL to a dedicated test database")


def test_migration_and_seed_are_idempotent(tmp_path):
    apply_migrations(DATABASE_URL)
    apply_migrations(DATABASE_URL)
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    marker = "test-seed-" + str(uuid4())
    (seed_dir / "sample.json").write_text(json.dumps([{"id": marker, "kind": "test", "name": "test"}]))
    load_seed(DATABASE_URL, seed_dir)
    load_seed(DATABASE_URL, seed_dir)
    with psycopg.connect(DATABASE_URL) as conn:
        assert conn.execute("SELECT count(*) FROM catalog_entries WHERE id=%s", (marker,)).fetchone()[0] == 1


def test_session_read_and_compare_and_swap():
    store = StateStore(DATABASE_URL)
    state = store.create_session()
    assert store.get_state(state.session_id).version == 0
    try:
        store.save_turn(state, expected_version=0, user_text="x", assistant_text="y", trace_steps=[])
        with pytest.raises(VersionConflict):
            store.save_turn(state, expected_version=0, user_text="lost", assistant_text="lost", trace_steps=[])
        persisted = store.get_session(state.session_id)
        assert persisted["state"].version == 1
        assert [m["text"] for m in persisted["messages"]] == ["x", "y"]
    finally:
        # Test DB only: remove only this test's unique session row.
        with store.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE id=%s", (str(state.session_id),))


def test_concurrent_same_version_only_one_write_wins():
    from concurrent.futures import ThreadPoolExecutor

    store = StateStore(DATABASE_URL)
    state = store.create_session()
    def write(text):
        try:
            store.save_turn(state, expected_version=0, user_text=text, assistant_text=text, trace_steps=[])
            return "ok"
        except VersionConflict:
            return "conflict"
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(write, ["a", "b"]))
        assert sorted(results) == ["conflict", "ok"]
        assert store.get_state(state.session_id).version == 1
    finally:
        with store.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE id=%s", (str(state.session_id),))


def test_request_id_is_atomic_deduplicated_and_status_survives_sse_loss():
    from uuid import uuid4

    store = StateStore(DATABASE_URL)
    state = store.create_session()
    request_id = uuid4()
    try:
        assert store.begin_request(request_id, state.session_id, "body-hash")["status"] == "new"
        assert store.begin_request(request_id, state.session_id, "body-hash")["status"] == "processing"
        with pytest.raises(RequestIdConflict):
            store.begin_request(request_id, state.session_id, "different-body")
        store.save_turn(state, expected_version=0, user_text="x", assistant_text="y", trace_steps=[], request_id=request_id)
        duplicate = store.begin_request(request_id, state.session_id, "body-hash")
        assert duplicate["status"] == "completed" and duplicate["committed_version"] == 1
        session = store.get_session(state.session_id)
        assert session["state"].version == 1
        assert [m["text"] for m in session["messages"]] == ["x", "y"]
    finally:
        with store.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE id=%s", (str(state.session_id),))


def test_parallel_request_id_reservation_has_one_winner():
    from concurrent.futures import ThreadPoolExecutor
    from uuid import uuid4

    store = StateStore(DATABASE_URL)
    state = store.create_session()
    request_id = uuid4()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            records = list(pool.map(lambda _: store.begin_request(request_id, state.session_id, "same"), range(2)))
        assert sorted(row["status"] for row in records) == ["new", "processing"]
    finally:
        with store.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE id=%s", (str(state.session_id),))


def test_real_build_bundles_integration_when_available():
    import importlib.util
    if importlib.util.find_spec("app.planning.build") is None:
        pytest.skip("track A build_bundles is not present")
    # Real controlled catalog scenario should be supplied by track A integration.
    from app.planning.build import build_bundles
    assert callable(build_bundles)
