"""
Test suite for Smart Legal Document Manager.
Uses an in-memory SQLite DB – no external services required.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.db.session import Base, get_db

TEST_DB_URL = "sqlite:///./test_legal.db"
engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
Base.metadata.create_all(bind=engine)
client = TestClient(app)


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def auth_headers():
    client.post("/api/v1/auth/register", json={
        "username": "testlawyer",
        "email": "lawyer@test.com",
        "password": "secret123",
        "full_name": "Test Lawyer",
    })
    resp = client.post("/api/v1/auth/login", data={
        "username": "testlawyer", "password": "secret123"
    })
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def sample_doc(auth_headers):
    resp = client.post("/api/v1/documents", json={
        "title": "Contract A",
        "content_text": "This is the initial contract text.\nClause 1: Payment terms.",
        "change_summary": "Initial draft",
    }, headers=auth_headers)
    assert resp.status_code == 201
    return resp.json()


# ── auth tests ────────────────────────────────────────────────────────────────

def test_register_duplicate_username(auth_headers):
    resp = client.post("/api/v1/auth/register", json={
        "username": "testlawyer",
        "email": "other@test.com",
        "password": "pass123",
    })
    assert resp.status_code == 400


def test_login_wrong_password():
    resp = client.post("/api/v1/auth/login", data={
        "username": "testlawyer", "password": "wrongpass"
    })
    assert resp.status_code == 401


# ── document CRUD tests ───────────────────────────────────────────────────────

def test_create_document(auth_headers):
    resp = client.post("/api/v1/documents", json={
        "title": "NDA Agreement",
        "content_text": "This Non-Disclosure Agreement...",
    }, headers=auth_headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "NDA Agreement"
    assert data["version_count"] == 1
    assert data["latest_version"]["version_number"] == 1


def test_list_documents(auth_headers, sample_doc):
    resp = client.get("/api/v1/documents", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


def test_get_document(auth_headers, sample_doc):
    doc_id = sample_doc["id"]
    resp = client.get(f"/api/v1/documents/{doc_id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == doc_id


def test_update_title_no_new_version(auth_headers, sample_doc):
    doc_id = sample_doc["id"]
    resp = client.patch(f"/api/v1/documents/{doc_id}/title",
                        json={"title": "Contract A – Revised Title"},
                        headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Contract A – Revised Title"
    assert data["version_count"] == 1  # still only 1 version!


# ── version management tests ──────────────────────────────────────────────────

def test_add_version(auth_headers, sample_doc):
    doc_id = sample_doc["id"]
    resp = client.post(f"/api/v1/documents/{doc_id}/versions", json={
        "content_text": "This is the updated contract text.\nClause 1: Payment terms revised.",
        "change_summary": "Updated payment clause",
    }, headers=auth_headers)
    assert resp.status_code == 201
    assert resp.json()["version_number"] == 2


def test_reject_identical_content(auth_headers, sample_doc):
    doc_id = sample_doc["id"]
    original_text = sample_doc["latest_version"]["content_text"]
    resp = client.post(f"/api/v1/documents/{doc_id}/versions", json={
        "content_text": original_text,
    }, headers=auth_headers)
    assert resp.status_code == 409  # Conflict – identical content


def test_list_versions(auth_headers, sample_doc):
    doc_id = sample_doc["id"]
    resp = client.get(f"/api/v1/documents/{doc_id}/versions", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


def test_get_specific_version(auth_headers, sample_doc):
    doc_id = sample_doc["id"]
    resp = client.get(f"/api/v1/documents/{doc_id}/versions/1", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["version_number"] == 1


# ── diff tests ────────────────────────────────────────────────────────────────

def test_diff_between_versions(auth_headers):
    # Create a doc with 2 versions
    resp = client.post("/api/v1/documents", json={
        "title": "Diff Test Doc",
        "content_text": "Line one\nLine two\nLine three",
    }, headers=auth_headers)
    doc_id = resp.json()["id"]

    client.post(f"/api/v1/documents/{doc_id}/versions", json={
        "content_text": "Line one\nLine TWO – edited\nLine three\nLine four added",
    }, headers=auth_headers)

    resp = client.get(f"/api/v1/documents/{doc_id}/diff?version_a=1&version_b=2",
                      headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["version_a"] == 1
    assert data["version_b"] == 2
    assert data["stats"]["added"] >= 1
    assert data["stats"]["replaced"] >= 1
    tags = {line["tag"] for line in data["lines"]}
    assert "equal" in tags  # unchanged lines present
    assert "replace" in tags or "insert" in tags


def test_diff_similarity_percent(auth_headers):
    # Build a large document so a one-word change gives high similarity
    base_lines = "\n".join(f"Clause {i}: This is standard legal boilerplate text." for i in range(1, 51))
    resp = client.post("/api/v1/documents", json={
        "title": "Similarity Test",
        "content_text": base_lines,
    }, headers=auth_headers)
    doc_id = resp.json()["id"]
    # Change only the last word of the last clause – should remain highly similar
    modified = base_lines[:-1] + "X"
    client.post(f"/api/v1/documents/{doc_id}/versions", json={
        "content_text": modified,
    }, headers=auth_headers)
    diff = client.get(f"/api/v1/documents/{doc_id}/diff?version_a=1&version_b=2",
                      headers=auth_headers).json()
    assert diff["similarity_percent"] > 90
    assert diff["is_significant"] is False  # tiny change below threshold


# ── delete tests ──────────────────────────────────────────────────────────────

def test_soft_delete_version_keeps_document(auth_headers):
    resp = client.post("/api/v1/documents", json={
        "title": "Delete Version Test",
        "content_text": "Version 1 content",
    }, headers=auth_headers)
    doc_id = resp.json()["id"]
    client.post(f"/api/v1/documents/{doc_id}/versions", json={
        "content_text": "Version 2 content – significantly different text here for testing",
    }, headers=auth_headers)

    # Delete version 1
    resp = client.delete(f"/api/v1/documents/{doc_id}/versions/1", headers=auth_headers)
    assert resp.status_code == 204

    # Document still accessible
    resp = client.get(f"/api/v1/documents/{doc_id}", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["version_count"] == 1


def test_cannot_delete_last_version(auth_headers):
    resp = client.post("/api/v1/documents", json={
        "title": "Single Version Doc",
        "content_text": "Only version",
    }, headers=auth_headers)
    doc_id = resp.json()["id"]
    resp = client.delete(f"/api/v1/documents/{doc_id}/versions/1", headers=auth_headers)
    assert resp.status_code == 400


def test_soft_delete_document(auth_headers):
    resp = client.post("/api/v1/documents", json={
        "title": "To Be Deleted",
        "content_text": "Content",
    }, headers=auth_headers)
    doc_id = resp.json()["id"]
    resp = client.delete(f"/api/v1/documents/{doc_id}", headers=auth_headers)
    assert resp.status_code == 204
    resp = client.get(f"/api/v1/documents/{doc_id}", headers=auth_headers)
    assert resp.status_code == 404


def test_hard_delete_document(auth_headers):
    resp = client.post("/api/v1/documents", json={
        "title": "Hard Delete Test",
        "content_text": "Content",
    }, headers=auth_headers)
    doc_id = resp.json()["id"]
    resp = client.delete(f"/api/v1/documents/{doc_id}?force=true", headers=auth_headers)
    assert resp.status_code == 204
