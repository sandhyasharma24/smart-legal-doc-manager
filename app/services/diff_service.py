"""
Diff Service
============
Uses Python's built-in `difflib.SequenceMatcher` to compare two document
versions line-by-line.

Algorithm overview
------------------
1. Split both texts into lines.
2. Run SequenceMatcher with `autojunk=False` (important for legal text –
   common legal boilerplate should NOT be auto-junk'd away).
3. Walk the opcode list produced by SequenceMatcher:
   - "equal"   → unchanged lines (shown collapsed in UI)
   - "replace" → block of lines swapped for another block (shown as Before/After)
   - "insert"  → lines added in version B (shown green)
   - "delete"  → lines removed from version A (shown red)
4. Compute similarity_ratio from SequenceMatcher.ratio() – this is the
   fraction of matching characters across both texts (0.0 – 1.0).
5. Derive is_significant: change is significant when the similarity ratio
   drops below (1 - threshold/100).

Why SequenceMatcher?
--------------------
- It's part of the stdlib (no extra deps).
- autojunk=False makes it safe for repetitive legal boilerplate.
- ratio() gives a character-level similarity score which is more nuanced
  than a simple line-count diff for deciding "is this change significant?".
- For lawyers, inline "replace" hunks (Before/After per changed block) are
  more readable than unified-diff @@-style output.
"""

import difflib
from typing import List, Tuple
from app.core.config import settings
from app.schemas.document import DiffLine, DiffResult
from app.models.document import DocumentVersion, Document


def _split_lines(text: str) -> List[str]:
    """Split on newlines, keeping the line terminator stripped."""
    return text.splitlines() or [""]


def compute_diff(
    doc: Document,
    version_a: DocumentVersion,
    version_b: DocumentVersion,
) -> DiffResult:
    """
    Produce a structured diff between two DocumentVersion objects.
    version_a = 'before', version_b = 'after'.
    """
    lines_a = _split_lines(version_a.content_text)
    lines_b = _split_lines(version_b.content_text)

    matcher = difflib.SequenceMatcher(None, lines_a, lines_b, autojunk=False)
    similarity = round(matcher.ratio() * 100, 2)
    threshold = settings.CHANGE_SIGNIFICANCE_THRESHOLD
    is_significant = similarity < (100.0 - threshold)

    diff_lines: List[DiffLine] = []
    stats = {"added": 0, "removed": 0, "replaced": 0, "unchanged": 0}

    # line number counters (1-based)
    ln_a = 1
    ln_b = 1

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                diff_lines.append(DiffLine(
                    line_number_before=ln_a + offset,
                    line_number_after=ln_b + offset,
                    tag="equal",
                    content_before=lines_a[i1 + offset],
                    content_after=lines_b[j1 + offset],
                ))
            stats["unchanged"] += i2 - i1
            ln_a += i2 - i1
            ln_b += j2 - j1

        elif tag == "replace":
            # Pair up lines where possible; remainder are pure insert/delete
            block_a = lines_a[i1:i2]
            block_b = lines_b[j1:j2]
            max_len = max(len(block_a), len(block_b))
            for offset in range(max_len):
                line_before = block_a[offset] if offset < len(block_a) else None
                line_after = block_b[offset] if offset < len(block_b) else None
                actual_tag = "replace" if (line_before is not None and line_after is not None) else \
                             "delete" if line_after is None else "insert"
                diff_lines.append(DiffLine(
                    line_number_before=ln_a + offset if offset < len(block_a) else None,
                    line_number_after=ln_b + offset if offset < len(block_b) else None,
                    tag=actual_tag,
                    content_before=line_before,
                    content_after=line_after,
                ))
            stats["replaced"] += min(len(block_a), len(block_b))
            stats["removed"] += max(0, len(block_a) - len(block_b))
            stats["added"] += max(0, len(block_b) - len(block_a))
            ln_a += i2 - i1
            ln_b += j2 - j1

        elif tag == "insert":
            for offset in range(j2 - j1):
                diff_lines.append(DiffLine(
                    line_number_before=None,
                    line_number_after=ln_b + offset,
                    tag="insert",
                    content_before=None,
                    content_after=lines_b[j1 + offset],
                ))
            stats["added"] += j2 - j1
            ln_b += j2 - j1

        elif tag == "delete":
            for offset in range(i2 - i1):
                diff_lines.append(DiffLine(
                    line_number_before=ln_a + offset,
                    line_number_after=None,
                    tag="delete",
                    content_before=lines_a[i1 + offset],
                    content_after=None,
                ))
            stats["removed"] += i2 - i1
            ln_a += i2 - i1

    author_a = version_a.created_by_user.username if version_a.created_by_user else None
    author_b = version_b.created_by_user.username if version_b.created_by_user else None

    return DiffResult(
        document_id=doc.id,
        document_title=doc.title,
        version_a=version_a.version_number,
        version_b=version_b.version_number,
        created_at_a=version_a.created_at,
        created_at_b=version_b.created_at,
        author_a=author_a,
        author_b=author_b,
        stats=stats,
        lines=diff_lines,
        is_significant=is_significant,
        similarity_percent=similarity,
    )


def is_content_significantly_different(text_a: str, text_b: str) -> Tuple[bool, float]:
    """
    Quick check used before saving a new version and before sending notifications.
    Returns (is_significant, similarity_percent).
    """
    if text_a == text_b:
        return False, 100.0
    matcher = difflib.SequenceMatcher(None, text_a, text_b, autojunk=False)
    similarity = round(matcher.ratio() * 100, 2)
    threshold = settings.CHANGE_SIGNIFICANCE_THRESHOLD
    return similarity < (100.0 - threshold), similarity
