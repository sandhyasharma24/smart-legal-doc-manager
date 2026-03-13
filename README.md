# Smart Legal Document Manager

A FastAPI backend system that helps lawyers track changes in legal documents with full version history, intelligent diffing, and smart background notifications.

---

## Table of Contents
1. [Quick Start](#quick-start)
2. [Project Structure](#project-structure)
3. [User Guide – Testing Each Feature](#user-guide)
4. [How the Comparison Logic Works](#comparison-logic)
5. [Design Decisions & Edge Cases](#design-decisions)
6. [API Reference](#api-reference)

---

## Quick Start

### Option A – Local (No Docker)

```bash
# 1. Clone the repo
git clone <your-repo-url>
cd smart-legal-doc-manager

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy env config (optional – defaults work out of the box)
cp .env.example .env

# 5. Start the server
uvicorn app.main:app --reload

# 6. Open interactive API docs
open http://127.0.0.1:8000/docs
```

### Option B – Docker Compose (includes Redis + Celery worker)

```bash
docker-compose up --build
```

The API will be available at `http://localhost:8000`.

### Run Tests

```bash
pytest tests/ -v
```

All 16 tests should pass with no external services required.

---

## Project Structure

```
smart-legal-doc-manager/
├── app/
│   ├── main.py                        # FastAPI app entrypoint
│   ├── api/v1/
│   │   ├── router.py                  # Route aggregator
│   │   └── endpoints/
│   │       ├── auth.py                # Register / Login
│   │       └── documents.py           # All document & version endpoints
│   ├── core/
│   │   ├── config.py                  # Settings (pydantic-settings)
│   │   └── security.py                # JWT + bcrypt helpers
│   ├── db/
│   │   └── session.py                 # SQLAlchemy engine + session
│   ├── models/
│   │   └── document.py                # User, Document, DocumentVersion ORM models
│   ├── schemas/
│   │   └── document.py                # Pydantic request/response schemas
│   ├── services/
│   │   ├── document_service.py        # Business logic (CRUD, version management)
│   │   └── diff_service.py            # Comparison algorithm
│   └── workers/
│       └── notification_worker.py     # Celery task + sync fallback
├── tests/
│   └── test_documents.py              # 16 pytest integration tests
├── alembic/                           # DB migration support
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## User Guide

### Step 0 – Register and Login

All endpoints require a Bearer token. First create an account, then log in.

**Register:**
```http
POST /api/v1/auth/register
Content-Type: application/json

{
  "username": "jane_lawyer",
  "email": "jane@lawfirm.com",
  "password": "secure123",
  "full_name": "Jane Smith"
}
```

**Login:**
```http
POST /api/v1/auth/login
Content-Type: application/x-www-form-urlencoded

username=jane_lawyer&password=secure123
```

Copy the `access_token` from the response. Use it as `Authorization: Bearer <token>` in all subsequent requests.

> In the Swagger UI at `/docs`, click **Authorize** and paste the token.

---

### Feature 1 – Creating and Updating Documents

**Create a new document (saves as version 1 automatically):**
```http
POST /api/v1/documents
Authorization: Bearer <token>
Content-Type: application/json

{
  "title": "Acme Corp NDA",
  "content_text": "This Non-Disclosure Agreement ('Agreement') is entered into...\nClause 1: Confidentiality obligations...\nClause 2: Term of agreement is 2 years.",
  "change_summary": "Initial draft"
}
```
Response includes `id`, `version_count: 1`, and the full `latest_version` object.

**Save a new version (never overwrites old content):**
```http
POST /api/v1/documents/{id}/versions
Authorization: Bearer <token>
Content-Type: application/json

{
  "content_text": "This Non-Disclosure Agreement ('Agreement') is entered into...\nClause 1: Confidentiality obligations – updated to include digital assets.\nClause 2: Term of agreement is 3 years.",
  "change_summary": "Extended term to 3 years, added digital assets clause"
}
```
Returns the new version object. The old version is **never modified**.

**Test identical content guard:**
Send the same `content_text` again → you will receive `HTTP 409 Conflict`:
```json
{ "detail": "Content is identical to the current version. No new version created." }
```

---

### Feature 2 – Comparing Versions (The "Difference" Feature)

**Compare any two versions:**
```http
GET /api/v1/documents/{id}/diff?version_a=1&version_b=2
Authorization: Bearer <token>
```

**Example response:**
```json
{
  "document_id": 1,
  "document_title": "Acme Corp NDA",
  "version_a": 1,
  "version_b": 2,
  "created_at_a": "2025-03-12T10:00:00Z",
  "created_at_b": "2025-03-12T11:30:00Z",
  "author_a": "jane_lawyer",
  "author_b": "jane_lawyer",
  "stats": {
    "added": 1,
    "removed": 0,
    "replaced": 2,
    "unchanged": 1
  },
  "similarity_percent": 82.5,
  "is_significant": true,
  "lines": [
    {
      "line_number_before": 1,
      "line_number_after": 1,
      "tag": "equal",
      "content_before": "This Non-Disclosure Agreement...",
      "content_after": "This Non-Disclosure Agreement..."
    },
    {
      "line_number_before": 2,
      "line_number_after": 2,
      "tag": "replace",
      "content_before": "Clause 1: Confidentiality obligations...",
      "content_after": "Clause 1: Confidentiality obligations – updated to include digital assets."
    },
    ...
  ]
}
```

**Reading the diff:**
| `tag`     | Meaning                              | Display hint          |
|-----------|--------------------------------------|-----------------------|
| `equal`   | Line unchanged                       | Gray / collapsed      |
| `replace` | Line was edited (before → after)     | Yellow highlight      |
| `insert`  | New line added in version B          | Green highlight       |
| `delete`  | Line removed from version A          | Red highlight         |

`similarity_percent` – 100% means identical, 0% means completely different.  
`is_significant` – `true` when more than 5% of the content changed (configurable via `CHANGE_SIGNIFICANCE_THRESHOLD` in `.env`).

---

### Feature 3 – Smart Notification System

Notifications fire automatically when you add a new version via `POST /api/v1/documents/{id}/versions`.

**How to observe it:**
1. The API returns the new version immediately (non-blocking).
2. Check the server console logs – you will see a structured notification entry:
   ```
   INFO | [Notification] SIGNIFICANT CHANGE | doc_id=1 title='Acme Corp NDA'
         version=2 author=jane_lawyer similarity=65.2% at=2025-03-12T...
   ```
3. If SMTP is configured in `.env`, an email is also dispatched.

**Testing the threshold:**
- Make a tiny change (fix one letter) → `is_significant: false` in the diff → no notification log.
- Make a substantial change (replace multiple clauses) → `is_significant: true` → notification fires.

**No Redis?** The system automatically falls back to a background thread so the API never blocks. With Redis running, Celery handles it with full retry support.

---

### Feature 4 – Managing Document Details

**Update the title only (no new version created):**
```http
PATCH /api/v1/documents/{id}/title
Authorization: Bearer <token>
Content-Type: application/json

{ "title": "Acme Corp NDA – Final" }
```
The `version_count` stays the same. This is intentional: fixing a typo in the title should not pollute the version history.

**List all versions of a document:**
```http
GET /api/v1/documents/{id}/versions
```

**Retrieve a specific version:**
```http
GET /api/v1/documents/{id}/versions/1
```

**Soft-delete a single version (siblings are preserved):**
```http
DELETE /api/v1/documents/{id}/versions/2
```
Returns `204 No Content`. Version 1 and all other versions remain intact.  
> Attempting to delete the **last** remaining version returns `HTTP 400` – you must delete the entire document instead.

**Soft-delete the entire document (recoverable):**
```http
DELETE /api/v1/documents/{id}
```
Returns `204`. The document no longer appears in listings. Data is not erased.

**Permanently erase the document and all versions:**
```http
DELETE /api/v1/documents/{id}?force=true
```
Returns `204`. This is irreversible.

---

## Comparison Logic

### Algorithm

The diff engine lives in `app/services/diff_service.py` and uses Python's built-in `difflib.SequenceMatcher`.

**Step-by-step:**

1. **Split into lines** – both version texts are split on newlines, preserving all formatting.

2. **Run SequenceMatcher** with `autojunk=False`.  
   The `autojunk` flag is critical for legal text: without disabling it, `difflib` would treat common boilerplate phrases (e.g. "whereas", "hereinafter", standard clause openings) as noise to be ignored, producing misleading diffs.

3. **Walk the opcode list** – SequenceMatcher returns a list of operations:
   - `equal` – line range is identical in both versions
   - `replace` – a block of lines in A was swapped for a block in B
   - `insert` – lines were added in B with no counterpart in A
   - `delete` – lines in A were removed with no counterpart in B

4. **Pair up replace blocks** – Within a `replace` block, lines are paired positionally (line 1 of A with line 1 of B, etc.). Surplus lines become pure `insert` or `delete` entries. This gives lawyers a clear Before/After view per changed paragraph rather than a wall of red and green.

5. **Compute similarity** – `SequenceMatcher.ratio()` gives a character-level similarity score (0.0–1.0). This is more nuanced than a line count because it catches in-line edits within a single line (e.g. changing one word in a 50-word clause).

6. **Significance check** – A change is `is_significant` when `similarity < (100 - CHANGE_SIGNIFICANCE_THRESHOLD)`. Default threshold is 5%, meaning the texts must be at least 95% similar to be considered "just whitespace / trivial". This threshold is configurable per deployment.

### Why SequenceMatcher instead of a third-party lib?

- Zero extra dependencies (stdlib).
- `autojunk=False` makes it safe for repetitive legal boilerplate.
- The character-level `ratio()` is better than a naive line-count diff for deciding notification significance.
- The opcode model maps cleanly onto the "equal / added / removed / replaced" terminology lawyers already use.

---

## Design Decisions

| Question | Decision |
|----------|----------|
| **Never overwrite old content** | `DocumentVersion` rows are insert-only. The service layer raises `409 Conflict` if content is identical. |
| **What if a crash happens mid-upload?** | `db.flush()` (to get the doc ID) and `db.commit()` happen together in one transaction. If anything fails before `commit()`, the entire operation is rolled back – no partial data is written. |
| **Title vs. Content separation** | `PATCH /documents/{id}/title` touches only the `Document` row. `POST /documents/{id}/versions` touches only `DocumentVersion`. The two concerns are completely decoupled. |
| **Soft vs hard delete** | Default `DELETE` is soft (sets `is_deleted=True`). `?force=true` does a hard cascade. Deleting a single version never removes siblings. |
| **Notification non-blocking** | The endpoint returns `201` before the notification runs. With Celery+Redis it's a proper async task with retries. Without Redis it degrades to a daemon thread – the API never blocks. |
| **Significance threshold** | 5% configurable via `CHANGE_SIGNIFICANCE_THRESHOLD`. Whitespace-only edits (e.g. trailing space) will not trigger an alert because the character-level similarity stays above 95%. |

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/auth/register` | Create user account |
| `POST` | `/api/v1/auth/login` | Obtain JWT token |
| `GET`  | `/api/v1/documents` | List all active documents |
| `POST` | `/api/v1/documents` | Create document (saves as v1) |
| `GET`  | `/api/v1/documents/{id}` | Get document with latest version |
| `PATCH`| `/api/v1/documents/{id}/title` | Update title only |
| `DELETE`| `/api/v1/documents/{id}` | Soft-delete (`?force=true` for hard) |
| `GET`  | `/api/v1/documents/{id}/versions` | List all versions (no content) |
| `POST` | `/api/v1/documents/{id}/versions` | Save new version |
| `GET`  | `/api/v1/documents/{id}/versions/{n}` | Get specific version with content |
| `DELETE`| `/api/v1/documents/{id}/versions/{n}` | Soft-delete one version |
| `GET`  | `/api/v1/documents/{id}/diff?version_a=N&version_b=M` | Compare two versions |
| `GET`  | `/health` | Health check |

Interactive docs available at `/docs` (Swagger UI) and `/redoc`.
