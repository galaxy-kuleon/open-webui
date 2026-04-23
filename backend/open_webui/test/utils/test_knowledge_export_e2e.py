"""
End-to-end integration tests for the knowledge organizer pipeline.

Exercises the full _organize_inbox flow with mocked LLM responses:
- Creates real files in temp directories
- Mocks _run_organizer_planner (the LLM boundary) to return valid JSON plans
- Verifies files are physically moved to correct destinations
- Verifies inbox is emptied after processing
- Verifies batching behavior when documents exceed ORGANIZER_BATCH_SIZE
"""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_app_with_config(export_dir: str, model: str = "test-model"):
    """Build an app-like object with state.config carrying RAG_KNOWLEDGE_* settings."""
    config = SimpleNamespace(
        RAG_KNOWLEDGE_EXPORT_DIR=export_dir,
        RAG_KNOWLEDGE_ORGANIZER_MODEL=model,
    )
    state = SimpleNamespace(config=config)
    return SimpleNamespace(state=state)


def _write_md_file(inbox_dir: Path, base_name: str, content: str) -> Path:
    """Write a .md file into the inbox with standard frontmatter."""
    path = inbox_dir / f"{base_name}.md"
    frontmatter = (
        f"---\n"
        f"source_file_id: test-{base_name}\n"
        f"original_filename: {base_name}.pdf\n"
        f"exported_at: 2026-03-30T00:00:00\n"
        f"---\n\n"
    )
    path.write_text(frontmatter + content, encoding="utf-8")
    return path


def _write_index_file(inbox_dir: Path, base_name: str, content: str) -> Path:
    """Write a .index.md file into the inbox with standard frontmatter."""
    path = inbox_dir / f"{base_name}.index.md"
    frontmatter = (
        f"---\n"
        f"source_file_id: test-{base_name}\n"
        f"original_filename: {base_name}.pdf\n"
        f"type: index\n"
        f"exported_at: 2026-03-30T00:00:00\n"
        f"---\n\n"
    )
    path.write_text(frontmatter + content, encoding="utf-8")
    return path


def _build_llm_response(documents: list[dict], destination: str, reason: str = "test filing") -> str:
    """Build a JSON string that _parse_organizer_plan will accept for a given batch."""
    moves = [
        {"doc_id": doc["doc_id"], "destination": destination, "reason": reason}
        for doc in documents
    ]
    return json.dumps({"moves": moves})


def _build_llm_response_per_doc(doc_destinations: dict[str, str]) -> str:
    """Build a JSON response mapping each doc_id to its own destination."""
    moves = [
        {"doc_id": doc_id, "destination": dest, "reason": f"filed to {dest}"}
        for doc_id, dest in doc_destinations.items()
    ]
    return json.dumps({"moves": moves})


# ---------------------------------------------------------------------------
# Test: Single document moves from inbox to destination
# ---------------------------------------------------------------------------

def test_single_document_full_pipeline(tmp_path):
    """
    One .md + .index.md pair in inbox. Mock LLM says move to 'finance/reports'.
    After _organize_inbox, inbox should be empty and files should exist at destination.
    """
    from open_webui.utils.knowledge_export import _organize_inbox, _collect_inbox_documents

    export_dir = tmp_path
    inbox = export_dir / "inbox"
    inbox.mkdir()

    _write_md_file(inbox, "quarterly-earnings", "Q4 2025 earnings report content.")
    _write_index_file(inbox, "quarterly-earnings", "Keywords: earnings, Q4, finance")

    # Verify precondition: inbox has files
    assert len(list(inbox.iterdir())) == 2

    app = _make_app_with_config(export_dir=str(export_dir))

    def mock_planner(app, model, system_prompt, user_prompt, timeout=300.0):
        # Parse the context JSON from the user_prompt to get the doc_ids
        # The user_prompt starts with "Context JSON:\n" then the JSON
        json_text = user_prompt.split("Context JSON:\n", 1)[1]
        context = json.loads(json_text)
        docs = context["documents"]
        return _build_llm_response(
            [{"doc_id": d["doc_id"]} for d in docs],
            destination="finance/reports",
        )

    with patch(
        "open_webui.utils.knowledge_export._run_organizer_planner",
        side_effect=mock_planner,
    ):
        _organize_inbox(app=app)

    # Inbox should be empty
    remaining = [f for f in inbox.iterdir() if f.name.endswith(".md")]
    assert remaining == [], f"Inbox should be empty, still has: {[f.name for f in remaining]}"

    # Files should exist at destination
    dest_dir = export_dir / "finance" / "reports"
    assert dest_dir.is_dir(), f"Destination dir should exist: {dest_dir}"

    moved_md = dest_dir / "quarterly-earnings.md"
    moved_index = dest_dir / "quarterly-earnings.index.md"
    assert moved_md.is_file(), f"MD file should be at {moved_md}"
    assert moved_index.is_file(), f"Index file should be at {moved_index}"

    # Verify content integrity
    md_content = moved_md.read_text(encoding="utf-8")
    assert "Q4 2025 earnings report content." in md_content

    index_content = moved_index.read_text(encoding="utf-8")
    assert "Keywords: earnings, Q4, finance" in index_content


# ---------------------------------------------------------------------------
# Test: Multiple documents to different destinations
# ---------------------------------------------------------------------------

def test_multiple_documents_different_destinations(tmp_path):
    """
    Three documents in inbox, each directed to a different destination.
    Verifies all three move correctly.
    """
    from open_webui.utils.knowledge_export import _organize_inbox

    export_dir = tmp_path
    inbox = export_dir / "inbox"
    inbox.mkdir()

    _write_md_file(inbox, "tax-return-2025", "Tax return for 2025.")
    _write_index_file(inbox, "tax-return-2025", "Keywords: tax, 2025")

    _write_md_file(inbox, "project-proposal", "New project proposal for Q1.")
    _write_index_file(inbox, "project-proposal", "Keywords: project, proposal")

    _write_md_file(inbox, "meeting-notes-march", "Notes from March standup.")
    # No index file for this one — test .md-only document

    assert len(list(inbox.iterdir())) == 5  # 2+2+1

    app = _make_app_with_config(export_dir=str(export_dir))

    # The documents will be collected in sorted order by base_name:
    # meeting-notes-march, project-proposal, tax-return-2025
    # They get doc-0001, doc-0002, doc-0003 respectively
    destination_map = {
        "doc-0001": "meetings/standup",
        "doc-0002": "projects/proposals",
        "doc-0003": "finance/tax",
    }

    def mock_planner(app, model, system_prompt, user_prompt, timeout=300.0):
        json_text = user_prompt.split("Context JSON:\n", 1)[1]
        context = json.loads(json_text)
        docs = context["documents"]
        per_doc = {d["doc_id"]: destination_map[d["doc_id"]] for d in docs}
        return _build_llm_response_per_doc(per_doc)

    with patch(
        "open_webui.utils.knowledge_export._run_organizer_planner",
        side_effect=mock_planner,
    ):
        _organize_inbox(app=app)

    # Inbox should be empty
    remaining = [f for f in inbox.iterdir() if f.name.endswith(".md")]
    assert remaining == []

    # Verify each document at its destination
    assert (export_dir / "meetings" / "standup" / "meeting-notes-march.md").is_file()
    assert not (export_dir / "meetings" / "standup" / "meeting-notes-march.index.md").exists()

    assert (export_dir / "projects" / "proposals" / "project-proposal.md").is_file()
    assert (export_dir / "projects" / "proposals" / "project-proposal.index.md").is_file()

    assert (export_dir / "finance" / "tax" / "tax-return-2025.md").is_file()
    assert (export_dir / "finance" / "tax" / "tax-return-2025.index.md").is_file()

    # Verify catalog was written
    catalog = export_dir / "_catalog.md"
    assert catalog.is_file(), "Catalog should be generated"
    catalog_text = catalog.read_text(encoding="utf-8")
    assert "Knowledge Base Catalog" in catalog_text


# ---------------------------------------------------------------------------
# Test: Batch processing when documents exceed ORGANIZER_BATCH_SIZE
# ---------------------------------------------------------------------------

def test_batch_processing_exceeds_batch_size(tmp_path):
    """
    Create more documents than ORGANIZER_BATCH_SIZE (12).
    Verify that _organize_inbox processes them in multiple batches and
    all documents end up at their destinations.
    """
    from open_webui.utils.knowledge_export import (
        _organize_inbox,
        ORGANIZER_BATCH_SIZE,
    )

    export_dir = tmp_path
    inbox = export_dir / "inbox"
    inbox.mkdir()

    total_docs = ORGANIZER_BATCH_SIZE + 3  # 15 documents
    doc_names = [f"document-{i:03d}" for i in range(total_docs)]

    for name in doc_names:
        _write_md_file(inbox, name, f"Content of {name}.")
        _write_index_file(inbox, name, f"Index of {name}.")

    assert len(list(inbox.iterdir())) == total_docs * 2  # .md + .index.md each

    app = _make_app_with_config(export_dir=str(export_dir))

    # Track how many times the planner is called
    planner_call_count = {"value": 0}

    def mock_planner(app, model, system_prompt, user_prompt, timeout=300.0):
        planner_call_count["value"] += 1
        json_text = user_prompt.split("Context JSON:\n", 1)[1]
        context = json.loads(json_text)
        docs = context["documents"]
        # All docs in this batch go to "archive/batch-N"
        batch_num = planner_call_count["value"]
        per_doc = {d["doc_id"]: f"archive/batch-{batch_num}" for d in docs}
        return _build_llm_response_per_doc(per_doc)

    with patch(
        "open_webui.utils.knowledge_export._run_organizer_planner",
        side_effect=mock_planner,
    ):
        _organize_inbox(app=app)

    # Planner should have been called at least 2 times (12 + 3)
    assert planner_call_count["value"] >= 2, (
        f"Expected at least 2 planner calls for {total_docs} docs, "
        f"got {planner_call_count['value']}"
    )

    # Inbox should be completely empty
    remaining = [f for f in inbox.iterdir() if f.name.endswith(".md")]
    assert remaining == [], f"Inbox should be empty, still has: {[f.name for f in remaining]}"

    # Verify all documents were moved somewhere under archive/
    all_moved_md = list((export_dir / "archive").rglob("*.md"))
    # Filter out index files for counting unique documents
    content_files = [f for f in all_moved_md if not f.name.endswith(".index.md")]
    index_files = [f for f in all_moved_md if f.name.endswith(".index.md")]

    assert len(content_files) == total_docs, (
        f"Expected {total_docs} content files, found {len(content_files)}"
    )
    assert len(index_files) == total_docs, (
        f"Expected {total_docs} index files, found {len(index_files)}"
    )


# ---------------------------------------------------------------------------
# Test: .md file without .index.md still moves correctly
# ---------------------------------------------------------------------------

def test_md_only_document_moves(tmp_path):
    """A document with only .md (no .index.md) should still move correctly."""
    from open_webui.utils.knowledge_export import _organize_inbox

    export_dir = tmp_path
    inbox = export_dir / "inbox"
    inbox.mkdir()

    _write_md_file(inbox, "solo-doc", "A standalone markdown document.")
    assert len(list(inbox.iterdir())) == 1

    app = _make_app_with_config(export_dir=str(export_dir))

    def mock_planner(app, model, system_prompt, user_prompt, timeout=300.0):
        json_text = user_prompt.split("Context JSON:\n", 1)[1]
        context = json.loads(json_text)
        docs = context["documents"]
        return _build_llm_response(
            [{"doc_id": d["doc_id"]} for d in docs],
            destination="misc/standalone",
        )

    with patch(
        "open_webui.utils.knowledge_export._run_organizer_planner",
        side_effect=mock_planner,
    ):
        _organize_inbox(app=app)

    remaining = [f for f in inbox.iterdir() if f.name.endswith(".md")]
    assert remaining == []

    moved = export_dir / "misc" / "standalone" / "solo-doc.md"
    assert moved.is_file()
    assert "A standalone markdown document." in moved.read_text(encoding="utf-8")

    # No index file should exist at destination
    assert not (export_dir / "misc" / "standalone" / "solo-doc.index.md").exists()


# ---------------------------------------------------------------------------
# Test: .index.md file without .md still moves correctly
# ---------------------------------------------------------------------------

def test_index_only_document_moves(tmp_path):
    """A document with only .index.md (no .md) should still move correctly."""
    from open_webui.utils.knowledge_export import _organize_inbox

    export_dir = tmp_path
    inbox = export_dir / "inbox"
    inbox.mkdir()

    _write_index_file(inbox, "index-only", "Index content without body.")
    assert len(list(inbox.iterdir())) == 1

    app = _make_app_with_config(export_dir=str(export_dir))

    def mock_planner(app, model, system_prompt, user_prompt, timeout=300.0):
        json_text = user_prompt.split("Context JSON:\n", 1)[1]
        context = json.loads(json_text)
        docs = context["documents"]
        return _build_llm_response(
            [{"doc_id": d["doc_id"]} for d in docs],
            destination="misc/indexes",
        )

    with patch(
        "open_webui.utils.knowledge_export._run_organizer_planner",
        side_effect=mock_planner,
    ):
        _organize_inbox(app=app)

    remaining = [f for f in inbox.iterdir() if f.name.endswith(".md")]
    assert remaining == []

    moved_index = export_dir / "misc" / "indexes" / "index-only.index.md"
    assert moved_index.is_file()
    assert "Index content without body." in moved_index.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Test: Catalog file is written after organization
# ---------------------------------------------------------------------------

def test_catalog_written_after_organization(tmp_path):
    """After organizing, a _catalog.md should exist with correct structure."""
    from open_webui.utils.knowledge_export import _organize_inbox

    export_dir = tmp_path
    inbox = export_dir / "inbox"
    inbox.mkdir()

    _write_md_file(inbox, "report-alpha", "Alpha report.")
    _write_md_file(inbox, "report-beta", "Beta report.")

    app = _make_app_with_config(export_dir=str(export_dir))

    def mock_planner(app, model, system_prompt, user_prompt, timeout=300.0):
        json_text = user_prompt.split("Context JSON:\n", 1)[1]
        context = json.loads(json_text)
        docs = context["documents"]
        per_doc = {d["doc_id"]: "reports/quarterly" for d in docs}
        return _build_llm_response_per_doc(per_doc)

    with patch(
        "open_webui.utils.knowledge_export._run_organizer_planner",
        side_effect=mock_planner,
    ):
        _organize_inbox(app=app)

    catalog = export_dir / "_catalog.md"
    assert catalog.is_file()
    text = catalog.read_text(encoding="utf-8")

    assert "# Knowledge Base Catalog" in text
    assert "## Directory Structure" in text
    assert "## Recent Changes" in text
    # Verify the moved files show up in recent changes
    assert "report-alpha" in text
    assert "report-beta" in text


# ---------------------------------------------------------------------------
# Test: Existing directories are preserved and reused
# ---------------------------------------------------------------------------

def test_existing_directories_preserved(tmp_path):
    """
    Pre-existing directories and files in export_dir are not disturbed.
    New files are moved alongside existing ones.
    """
    from open_webui.utils.knowledge_export import _organize_inbox

    export_dir = tmp_path
    inbox = export_dir / "inbox"
    inbox.mkdir()

    # Pre-existing directory with a file
    existing_dir = export_dir / "finance" / "reports"
    existing_dir.mkdir(parents=True)
    existing_file = existing_dir / "old-report.md"
    existing_file.write_text("Pre-existing report.", encoding="utf-8")

    # New document to be organized into the same directory
    _write_md_file(inbox, "new-report", "A new financial report.")

    app = _make_app_with_config(export_dir=str(export_dir))

    def mock_planner(app, model, system_prompt, user_prompt, timeout=300.0):
        json_text = user_prompt.split("Context JSON:\n", 1)[1]
        context = json.loads(json_text)
        docs = context["documents"]
        return _build_llm_response(
            [{"doc_id": d["doc_id"]} for d in docs],
            destination="finance/reports",
        )

    with patch(
        "open_webui.utils.knowledge_export._run_organizer_planner",
        side_effect=mock_planner,
    ):
        _organize_inbox(app=app)

    # Old file untouched
    assert existing_file.is_file()
    assert existing_file.read_text(encoding="utf-8") == "Pre-existing report."

    # New file moved alongside it
    new_file = existing_dir / "new-report.md"
    assert new_file.is_file()
    assert "A new financial report." in new_file.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Test: Deduplication — same base_name at destination gets suffix
# ---------------------------------------------------------------------------

def test_name_collision_at_destination_gets_suffix(tmp_path):
    """
    When a file with the same base_name already exists at the destination,
    the moved file should get a numeric suffix (e.g., report-2.md).
    """
    from open_webui.utils.knowledge_export import _organize_inbox

    export_dir = tmp_path
    inbox = export_dir / "inbox"
    inbox.mkdir()

    # Pre-existing file at destination
    dest = export_dir / "finance"
    dest.mkdir(parents=True)
    (dest / "report.md").write_text("Already here.", encoding="utf-8")

    # New document with same base_name
    _write_md_file(inbox, "report", "New report content.")

    app = _make_app_with_config(export_dir=str(export_dir))

    def mock_planner(app, model, system_prompt, user_prompt, timeout=300.0):
        json_text = user_prompt.split("Context JSON:\n", 1)[1]
        context = json.loads(json_text)
        docs = context["documents"]
        return _build_llm_response(
            [{"doc_id": d["doc_id"]} for d in docs],
            destination="finance",
        )

    with patch(
        "open_webui.utils.knowledge_export._run_organizer_planner",
        side_effect=mock_planner,
    ):
        _organize_inbox(app=app)

    # Original untouched
    assert (dest / "report.md").read_text(encoding="utf-8") == "Already here."

    # New one should have a suffix
    assert (dest / "report-2.md").is_file()
    assert "New report content." in (dest / "report-2.md").read_text(encoding="utf-8")

    # Inbox empty
    remaining = [f for f in inbox.iterdir() if f.name.endswith(".md")]
    assert remaining == []
