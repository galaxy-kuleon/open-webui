"""
Behavioral tests for the KB content loader in research.py.

Tests the pure functions: load_kb_content, format_kb_context, estimate_tokens,
_should_include_path, _classify_md_file, tokenize_query, score_query_relevance.

Uses real temporary directories (no mocks for filesystem) so we test actual
I/O behavior: file reading, directory walking, encoding tolerance, etc.
"""

import os
import tempfile
from pathlib import Path

import pytest

from open_webui.utils.research import (
    _KB_EXCLUDED_DIRS,
    _KB_EXCLUDED_FILES,
    _classify_md_file,
    _is_cjk_char,
    _save_report,
    _should_include_path,
    estimate_tokens,
    format_kb_context,
    load_kb_content,
    score_query_relevance,
    tokenize_query,
)


# ---------------------------------------------------------------------------
# Helpers: create temporary KB structures
# ---------------------------------------------------------------------------

def _make_kb(tmp_path: Path, files: dict[str, str]) -> str:
    """Create a temporary KB directory with the given file structure.

    Args:
        tmp_path: pytest's tmp_path fixture
        files: mapping from relative path (e.g. "legal/doc.md") to content

    Returns:
        The KB root directory as a string.
    """
    kb_root = tmp_path / "kb"
    kb_root.mkdir()
    for rel_path, content in files.items():
        fpath = kb_root / rel_path
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content, encoding="utf-8")
    return str(kb_root)


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 0

    def test_short_ascii(self):
        # "hello" = 5 chars -> 5 // 4 = 1
        assert estimate_tokens("hello") == 1

    def test_round_number(self):
        # 400 chars -> 100 tokens
        assert estimate_tokens("a" * 400) == 100

    def test_cjk_text(self):
        # CJK chars are 3 bytes in UTF-8, but len() counts unicode codepoints
        text = "你好世界" * 10  # 40 chars
        assert estimate_tokens(text) == 10


# ---------------------------------------------------------------------------
# _classify_md_file
# ---------------------------------------------------------------------------

class TestClassifyMdFile:
    def test_index_file(self):
        assert _classify_md_file("doc.index.md") == "index"

    def test_full_file(self):
        assert _classify_md_file("doc.md") == "full"

    def test_cjk_index_file(self):
        assert _classify_md_file("聘僱合約書.index.md") == "index"

    def test_cjk_full_file(self):
        assert _classify_md_file("聘僱合約書.md") == "full"

    def test_not_index_if_just_contains_word(self):
        # "index.md" without the dot prefix is a full file
        assert _classify_md_file("my-index.md") == "full"


# ---------------------------------------------------------------------------
# _should_include_path
# ---------------------------------------------------------------------------

class TestShouldIncludePath:
    def test_normal_md_file(self, tmp_path):
        root = tmp_path / "kb"
        root.mkdir()
        fpath = root / "legal" / "doc.md"
        fpath.parent.mkdir(parents=True)
        fpath.touch()
        assert _should_include_path(fpath, root) is True

    def test_index_md_file(self, tmp_path):
        root = tmp_path / "kb"
        root.mkdir()
        fpath = root / "legal" / "doc.index.md"
        fpath.parent.mkdir(parents=True)
        fpath.touch()
        assert _should_include_path(fpath, root) is True

    def test_excluded_dir_opencode(self, tmp_path):
        root = tmp_path / "kb"
        root.mkdir()
        fpath = root / ".opencode" / "something.md"
        fpath.parent.mkdir(parents=True)
        fpath.touch()
        assert _should_include_path(fpath, root) is False

    def test_excluded_dir_reports(self, tmp_path):
        root = tmp_path / "kb"
        root.mkdir()
        fpath = root / "_reports" / "report.md"
        fpath.parent.mkdir(parents=True)
        fpath.touch()
        assert _should_include_path(fpath, root) is False

    def test_excluded_dir_inbox(self, tmp_path):
        root = tmp_path / "kb"
        root.mkdir()
        fpath = root / "inbox" / "pending.md"
        fpath.parent.mkdir(parents=True)
        fpath.touch()
        assert _should_include_path(fpath, root) is False

    def test_excluded_file_opencode_json(self, tmp_path):
        root = tmp_path / "kb"
        root.mkdir()
        fpath = root / "opencode.json"
        fpath.touch()
        assert _should_include_path(fpath, root) is False

    def test_excluded_file_catalog(self, tmp_path):
        root = tmp_path / "kb"
        root.mkdir()
        fpath = root / "_catalog.md"
        fpath.touch()
        assert _should_include_path(fpath, root) is False

    def test_excluded_hidden_file(self, tmp_path):
        root = tmp_path / "kb"
        root.mkdir()
        fpath = root / ".webui_secret_key"
        fpath.touch()
        assert _should_include_path(fpath, root) is False

    def test_non_md_file_excluded(self, tmp_path):
        root = tmp_path / "kb"
        root.mkdir()
        fpath = root / "data.json"
        fpath.touch()
        assert _should_include_path(fpath, root) is False

    def test_nested_excluded_dir(self, tmp_path):
        """File inside a nested .opencode dir should be excluded."""
        root = tmp_path / "kb"
        root.mkdir()
        fpath = root / "category" / ".opencode" / "nested.md"
        fpath.parent.mkdir(parents=True)
        fpath.touch()
        assert _should_include_path(fpath, root) is False


# ---------------------------------------------------------------------------
# load_kb_content — core behavior
# ---------------------------------------------------------------------------

class TestLoadKbContent:
    def test_empty_directory(self, tmp_path):
        kb_dir = _make_kb(tmp_path, {})
        result = load_kb_content(kb_dir)
        assert result == []

    def test_nonexistent_directory(self):
        result = load_kb_content("/nonexistent/path/to/kb")
        assert result == []

    def test_loads_index_and_full_files(self, tmp_path):
        kb_dir = _make_kb(tmp_path, {
            "legal/contract.md": "Full contract content here.",
            "legal/contract.index.md": "Index: entities, events.",
        })
        result = load_kb_content(kb_dir, token_budget=100_000)

        assert len(result) == 2
        # Index files come first
        assert result[0]["type"] == "index"
        assert result[0]["filename"] == "contract.index.md"
        assert "Index: entities" in result[0]["content"]

        assert result[1]["type"] == "full"
        assert result[1]["filename"] == "contract.md"
        assert "Full contract content" in result[1]["content"]

    def test_relative_paths_are_correct(self, tmp_path):
        kb_dir = _make_kb(tmp_path, {
            "healthcare/accreditation/doc.md": "content",
            "healthcare/accreditation/doc.index.md": "index content",
        })
        result = load_kb_content(kb_dir, token_budget=100_000)
        rel_paths = {e["relative_path"] for e in result}
        assert "healthcare/accreditation/doc.md" in rel_paths
        assert "healthcare/accreditation/doc.index.md" in rel_paths

    def test_excludes_opencode_dir(self, tmp_path):
        kb_dir = _make_kb(tmp_path, {
            "legal/doc.md": "good content",
            ".opencode/config.md": "should be excluded",
        })
        result = load_kb_content(kb_dir, token_budget=100_000)
        assert len(result) == 1
        assert result[0]["filename"] == "doc.md"

    def test_excludes_reports_dir(self, tmp_path):
        kb_dir = _make_kb(tmp_path, {
            "legal/doc.md": "good content",
            "_reports/report.md": "should be excluded",
        })
        result = load_kb_content(kb_dir, token_budget=100_000)
        assert len(result) == 1

    def test_excludes_inbox_dir(self, tmp_path):
        kb_dir = _make_kb(tmp_path, {
            "legal/doc.md": "good content",
            "inbox/pending.md": "should be excluded",
        })
        result = load_kb_content(kb_dir, token_budget=100_000)
        assert len(result) == 1

    def test_excludes_catalog_file(self, tmp_path):
        kb_dir = _make_kb(tmp_path, {
            "legal/doc.md": "good content",
            "_catalog.md": "catalog stuff",
        })
        result = load_kb_content(kb_dir, token_budget=100_000)
        assert len(result) == 1
        assert result[0]["filename"] == "doc.md"

    def test_excludes_non_md_files(self, tmp_path):
        kb_dir = _make_kb(tmp_path, {
            "legal/doc.md": "good content",
            "legal/data.json": '{"key": "value"}',
            "opencode.json": '{}',
        })
        result = load_kb_content(kb_dir, token_budget=100_000)
        assert len(result) == 1

    def test_skips_empty_files(self, tmp_path):
        kb_dir = _make_kb(tmp_path, {
            "legal/doc.md": "good content",
            "legal/empty.md": "",
            "legal/whitespace.md": "   \n  \n  ",
        })
        result = load_kb_content(kb_dir, token_budget=100_000)
        assert len(result) == 1
        assert result[0]["filename"] == "doc.md"

    def test_token_count_present(self, tmp_path):
        content = "x" * 400  # 100 tokens
        kb_dir = _make_kb(tmp_path, {"doc.md": content})
        result = load_kb_content(kb_dir, token_budget=100_000)
        assert result[0]["tokens"] == 100

    def test_cjk_filenames(self, tmp_path):
        kb_dir = _make_kb(tmp_path, {
            "healthcare/評鑑文件.md": "中文內容",
            "healthcare/評鑑文件.index.md": "索引內容",
        })
        result = load_kb_content(kb_dir, token_budget=100_000)
        assert len(result) == 2
        filenames = {e["filename"] for e in result}
        assert "評鑑文件.md" in filenames
        assert "評鑑文件.index.md" in filenames


# ---------------------------------------------------------------------------
# load_kb_content — budget enforcement
# ---------------------------------------------------------------------------

class TestLoadKbContentBudget:
    def test_index_files_prioritized_over_full(self, tmp_path):
        """When budget is tight, index files are included first; full files
        get the remainder (possibly truncated)."""
        index_content = "i" * 400  # 100 tokens
        full_content = "f" * 2000  # 500 tokens
        kb_dir = _make_kb(tmp_path, {
            "doc.index.md": index_content,
            "doc.md": full_content,
        })
        # Budget: 150 tokens — index takes 100, full gets truncated to ~50
        result = load_kb_content(kb_dir, token_budget=150)
        assert result[0]["type"] == "index"
        assert result[0]["tokens"] == 100
        # Full file present but truncated
        assert len(result) == 2
        assert result[1]["type"] == "full"
        assert result[1]["tokens"] <= 50  # only remainder of budget
        total = sum(e["tokens"] for e in result)
        assert total <= 150

    def test_index_only_when_no_room_for_full(self, tmp_path):
        """When budget is exhausted by index files, full files are omitted."""
        index_content = "i" * 400  # 100 tokens
        full_content = "f" * 2000  # 500 tokens
        kb_dir = _make_kb(tmp_path, {
            "doc.index.md": index_content,
            "doc.md": full_content,
        })
        # Budget: exactly 100 tokens — index takes it all, no room for full
        result = load_kb_content(kb_dir, token_budget=100)
        assert len(result) == 1
        assert result[0]["type"] == "index"

    def test_full_file_truncated_when_budget_tight(self, tmp_path):
        """A full file should be truncated to fit remaining budget."""
        index_content = "i" * 400  # 100 tokens
        full_content = "f" * 2000  # 500 tokens
        kb_dir = _make_kb(tmp_path, {
            "doc.index.md": index_content,
            "doc.md": full_content,
        })
        # Budget: 300 tokens — index takes 100, full gets truncated to ~200 tokens
        result = load_kb_content(kb_dir, token_budget=300)
        assert len(result) == 2
        assert result[0]["type"] == "index"
        assert result[1]["type"] == "full"
        # Full file should be truncated
        assert result[1]["tokens"] <= 200
        assert len(result[1]["content"]) < len(full_content)

    def test_zero_budget_returns_nothing(self, tmp_path):
        kb_dir = _make_kb(tmp_path, {"doc.md": "x" * 1000})
        result = load_kb_content(kb_dir, token_budget=0)
        assert result == []

    def test_very_small_budget_skips_if_not_meaningful(self, tmp_path):
        """Budget of 50 tokens (200 chars) — too small for 100 chars? No, 100 chars = 25 tokens."""
        kb_dir = _make_kb(tmp_path, {"doc.md": "x" * 100})
        result = load_kb_content(kb_dir, token_budget=50)
        assert len(result) == 1
        assert result[0]["tokens"] == 25

    def test_multiple_full_files_budget(self, tmp_path):
        """Only as many full files as budget allows."""
        kb_dir = _make_kb(tmp_path, {
            "a/doc1.md": "a" * 400,  # 100 tokens
            "b/doc2.md": "b" * 400,  # 100 tokens
            "c/doc3.md": "c" * 400,  # 100 tokens
        })
        # Budget: 250 — fits 2 full files, third gets truncated
        result = load_kb_content(kb_dir, token_budget=250)
        total_tokens = sum(e["tokens"] for e in result)
        assert total_tokens <= 250
        # Should have at least 2 entries
        assert len(result) >= 2

    def test_index_file_truncated_when_budget_very_tight(self, tmp_path):
        """Even index files get truncated when budget is extremely tight."""
        kb_dir = _make_kb(tmp_path, {
            "doc.index.md": "i" * 2000,  # 500 tokens
        })
        result = load_kb_content(kb_dir, token_budget=200)
        assert len(result) == 1
        assert result[0]["tokens"] <= 200
        assert len(result[0]["content"]) < 2000

    def test_budget_respected_across_many_files(self, tmp_path):
        """Total tokens across all returned entries should not exceed budget."""
        files = {}
        for i in range(20):
            files[f"cat{i}/doc{i}.md"] = f"content-{i} " * 50  # ~62 chars each -> ~15 tokens
            files[f"cat{i}/doc{i}.index.md"] = f"index-{i} " * 20  # ~48 chars -> ~12 tokens
        kb_dir = _make_kb(tmp_path, files)

        budget = 100
        result = load_kb_content(kb_dir, token_budget=budget)
        total_tokens = sum(e["tokens"] for e in result)
        assert total_tokens <= budget


# ---------------------------------------------------------------------------
# load_kb_content — ordering
# ---------------------------------------------------------------------------

class TestLoadKbContentOrdering:
    def test_index_files_come_before_full_files(self, tmp_path):
        kb_dir = _make_kb(tmp_path, {
            "z_last/doc.md": "full content",
            "a_first/doc.index.md": "index content",
        })
        result = load_kb_content(kb_dir, token_budget=100_000)
        assert len(result) == 2
        assert result[0]["type"] == "index"
        assert result[1]["type"] == "full"

    def test_within_type_sorted_by_relative_path(self, tmp_path):
        kb_dir = _make_kb(tmp_path, {
            "z/doc.md": "z content",
            "a/doc.md": "a content",
            "m/doc.md": "m content",
        })
        result = load_kb_content(kb_dir, token_budget=100_000)
        paths = [e["relative_path"] for e in result]
        assert paths == sorted(paths)


# ---------------------------------------------------------------------------
# format_kb_context
# ---------------------------------------------------------------------------

class TestFormatKbContext:
    def test_empty_list(self):
        assert format_kb_context([]) == ""

    def test_single_entry(self):
        entries = [{
            "filename": "doc.index.md",
            "relative_path": "legal/doc.index.md",
            "content": "Entity index content",
            "type": "index",
            "tokens": 5,
        }]
        result = format_kb_context(entries)
        assert "--- legal/doc.index.md (index) ---" in result
        assert "Entity index content" in result

    def test_multiple_entries_separated(self):
        entries = [
            {
                "filename": "a.index.md",
                "relative_path": "cat/a.index.md",
                "content": "Index A",
                "type": "index",
                "tokens": 2,
            },
            {
                "filename": "b.md",
                "relative_path": "cat/b.md",
                "content": "Full B",
                "type": "full",
                "tokens": 2,
            },
        ]
        result = format_kb_context(entries)
        # Both sections present
        assert "--- cat/a.index.md (index) ---" in result
        assert "--- cat/b.md (full) ---" in result
        assert "Index A" in result
        assert "Full B" in result
        # Separated by double newline
        assert "\n\n" in result

    def test_preserves_content_verbatim(self):
        content = "Line 1\nLine 2\n  Indented\n中文內容"
        entries = [{
            "filename": "doc.md",
            "relative_path": "doc.md",
            "content": content,
            "type": "full",
            "tokens": 10,
        }]
        result = format_kb_context(entries)
        assert content in result


# ---------------------------------------------------------------------------
# _is_cjk_char
# ---------------------------------------------------------------------------

class TestIsCjkChar:
    def test_common_chinese_char(self):
        assert _is_cjk_char("中") is True

    def test_common_chinese_char_2(self):
        assert _is_cjk_char("聘") is True

    def test_ascii_letter(self):
        assert _is_cjk_char("a") is False

    def test_digit(self):
        assert _is_cjk_char("1") is False

    def test_punctuation(self):
        assert _is_cjk_char(".") is False

    def test_cjk_extension_a(self):
        # U+3400 is CJK Extension A
        assert _is_cjk_char("\u3400") is True

    def test_cjk_compatibility(self):
        # U+F900 is CJK Compatibility
        assert _is_cjk_char("\uF900") is True

    def test_hangul(self):
        # Korean Hangul is NOT in CJK Unified Ideographs
        assert _is_cjk_char("한") is False

    def test_japanese_hiragana(self):
        # Hiragana is NOT CJK ideographs
        assert _is_cjk_char("あ") is False


# ---------------------------------------------------------------------------
# tokenize_query
# ---------------------------------------------------------------------------

class TestTokenizeQuery:
    def test_english_words(self):
        tokens = tokenize_query("employment contract analysis")
        assert tokens == {"employment", "contract", "analysis"}

    def test_cjk_characters(self):
        tokens = tokenize_query("聘僱合約")
        assert tokens == {"聘", "僱", "合", "約"}

    def test_mixed_english_cjk(self):
        tokens = tokenize_query("contract 合約 review")
        assert "contract" in tokens
        assert "review" in tokens
        assert "合" in tokens
        assert "約" in tokens

    def test_empty_string(self):
        assert tokenize_query("") == set()

    def test_whitespace_only(self):
        assert tokenize_query("   \t\n  ") == set()

    def test_none_returns_empty(self):
        """None is falsy so the early return produces an empty set.

        In practice, callers guard with `if query:` before calling, but
        the function is still safe if passed None.
        """
        assert tokenize_query(None) == set()

    def test_single_letters_dropped(self):
        """Single ASCII letters are too noisy and should be excluded."""
        tokens = tokenize_query("a b c contract")
        assert "a" not in tokens
        assert "b" not in tokens
        assert "c" not in tokens
        assert "contract" in tokens

    def test_numbers_included(self):
        tokens = tokenize_query("doc 2024 review")
        assert "2024" in tokens
        assert "doc" in tokens

    def test_punctuation_stripped(self):
        tokens = tokenize_query("what's the contract?")
        # "what" + "s" (split by apostrophe) -> "what" kept, "s" dropped (len 1)
        assert "what" in tokens
        assert "contract" in tokens
        assert "?" not in tokens

    def test_case_insensitive(self):
        tokens = tokenize_query("Employment CONTRACT Analysis")
        assert "employment" in tokens
        assert "contract" in tokens
        assert "analysis" in tokens
        # No uppercase variants
        assert "Employment" not in tokens

    def test_returns_set_not_list(self):
        result = tokenize_query("contract contract contract")
        assert isinstance(result, set)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# score_query_relevance
# ---------------------------------------------------------------------------

class TestScoreQueryRelevance:
    def test_full_match(self):
        tokens = tokenize_query("employment contract")
        score = score_query_relevance(tokens, "Employment contract document")
        assert score == 1.0

    def test_partial_match(self):
        tokens = tokenize_query("employment contract")
        score = score_query_relevance(tokens, "This contract is about leasing")
        assert score == 0.5

    def test_no_match(self):
        tokens = tokenize_query("employment contract")
        score = score_query_relevance(tokens, "Cats are wonderful pets")
        assert score == 0.0

    def test_empty_tokens_returns_zero(self):
        assert score_query_relevance(set(), "any text") == 0.0

    def test_cjk_full_match(self):
        tokens = tokenize_query("聘僱合約")
        score = score_query_relevance(tokens, "聘僱合約書的內容")
        assert score == 1.0

    def test_cjk_partial_match(self):
        tokens = tokenize_query("聘僱合約")
        score = score_query_relevance(tokens, "合約書的內容")
        assert score == 0.5  # 合 and 約 match

    def test_filename_contributes_to_score(self):
        tokens = tokenize_query("employment contract")
        # Text has no match, but filename does
        score = score_query_relevance(tokens, "Unrelated text", filename="employment_contract.md")
        assert score == 1.0

    def test_case_insensitive_matching(self):
        tokens = tokenize_query("EMPLOYMENT")
        score = score_query_relevance(tokens, "employment details")
        assert score == 1.0

    def test_mixed_cjk_english_query(self):
        tokens = tokenize_query("contract 合約")
        # Text has both
        score = score_query_relevance(tokens, "This contract 合約 is valid")
        assert score == 1.0

    def test_score_is_ratio(self):
        """Score should be the fraction of query tokens found."""
        tokens = tokenize_query("alpha bravo charlie delta")
        # Only 1 of 4 words matches
        score = score_query_relevance(tokens, "alpha is here only")
        assert score == pytest.approx(0.25)

    def test_pure_function_no_side_effects(self):
        """Calling the function should not mutate the input set."""
        tokens = tokenize_query("employment contract")
        original = tokens.copy()
        score_query_relevance(tokens, "employment details")
        assert tokens == original


# ---------------------------------------------------------------------------
# load_kb_content — query-aware loading
# ---------------------------------------------------------------------------

class TestLoadKbContentQueryAware:
    def test_query_none_is_alphabetical(self, tmp_path):
        """query=None should produce identical results to no query (backward compat)."""
        kb_dir = _make_kb(tmp_path, {
            "b/doc.md": "bravo content " * 20,
            "a/doc.md": "alpha content " * 20,
        })
        result_none = load_kb_content(kb_dir, query=None, token_budget=100_000)
        result_default = load_kb_content(kb_dir, token_budget=100_000)
        assert [e["relative_path"] for e in result_none] == [e["relative_path"] for e in result_default]

    def test_query_reorders_full_files_by_relevance(self, tmp_path):
        """Full files should be sorted by relevance when query is provided."""
        kb_dir = _make_kb(tmp_path, {
            "a_cats.md": "Cats are wonderful pets that purr and play " * 20,
            "z_contract.md": "Employment contract with salary and benefits " * 20,
        })
        # Without query: a_cats.md comes first (alphabetical)
        result_no_query = load_kb_content(kb_dir, token_budget=100_000)
        assert result_no_query[0]["relative_path"] == "a_cats.md"

        # With query about contracts: z_contract.md should come first
        result = load_kb_content(kb_dir, query="employment contract", token_budget=100_000)
        assert result[0]["relative_path"] == "z_contract.md"

    def test_query_reorders_cjk_files(self, tmp_path):
        """CJK query should prioritize files with matching CJK content."""
        kb_dir = _make_kb(tmp_path, {
            "a_unrelated.md": "This document is about random unrelated things " * 20,
            "z_聘僱合約.md": "聘僱合約書 包含薪資條件和福利制度 " * 20,
        })
        result = load_kb_content(kb_dir, query="聘僱合約", token_budget=100_000)
        assert result[0]["relative_path"] == "z_聘僱合約.md"

    def test_index_files_still_come_first_with_query(self, tmp_path):
        """Index files remain before full files even when query is provided."""
        kb_dir = _make_kb(tmp_path, {
            "z_relevant.md": "Employment contract salary details " * 20,
            "a_doc.index.md": "Index of topics and categories",
        })
        result = load_kb_content(kb_dir, query="employment contract", token_budget=100_000)
        assert result[0]["type"] == "index"
        assert result[1]["type"] == "full"

    def test_budget_respected_with_query(self, tmp_path):
        """Token budget is still respected when query reorders files."""
        kb_dir = _make_kb(tmp_path, {
            "a_irrelevant.md": "Cats cats cats " * 100,      # ~450 tokens
            "z_relevant.md": "Contract terms salary " * 100,  # ~660 tokens
        })
        result = load_kb_content(kb_dir, query="contract salary", token_budget=200)
        total = sum(e["tokens"] for e in result)
        assert total <= 200
        # The relevant file should be loaded (possibly truncated)
        assert result[0]["relative_path"] == "z_relevant.md"

    def test_relevant_file_loaded_instead_of_irrelevant_when_budget_tight(self, tmp_path):
        """When budget only fits one file, the relevant one should be picked."""
        kb_dir = _make_kb(tmp_path, {
            "a_irrelevant.md": "Cats are amazing " * 50,  # ~212 tokens
            "z_relevant.md": "Contract salary benefits " * 50,  # ~300 tokens
        })
        # Budget fits roughly one file
        result = load_kb_content(kb_dir, query="contract salary", token_budget=350)
        paths = [e["relative_path"] for e in result]
        assert "z_relevant.md" in paths
        # The irrelevant file might be partially there or absent
        total = sum(e["tokens"] for e in result)
        assert total <= 350

    def test_empty_query_string_same_as_none(self, tmp_path):
        """Empty string query should behave like no query (alphabetical)."""
        kb_dir = _make_kb(tmp_path, {
            "b/doc.md": "bravo content " * 20,
            "a/doc.md": "alpha content " * 20,
        })
        result_empty = load_kb_content(kb_dir, query="", token_budget=100_000)
        result_none = load_kb_content(kb_dir, query=None, token_budget=100_000)
        assert [e["relative_path"] for e in result_empty] == [e["relative_path"] for e in result_none]

    def test_query_with_no_matching_files_still_loads(self, tmp_path):
        """If no files match the query, all files are still loaded (score 0 for all)."""
        kb_dir = _make_kb(tmp_path, {
            "doc1.md": "Content about alpha " * 20,
            "doc2.md": "Content about bravo " * 20,
        })
        result = load_kb_content(kb_dir, query="zyxwvut nonexistent", token_budget=100_000)
        # Both files should still be loaded (all score 0 — stable sort by original walk order)
        assert len(result) == 2

    def test_multiple_files_relevance_ordering(self, tmp_path):
        """Multiple files with varying relevance should be ordered by score."""
        kb_dir = _make_kb(tmp_path, {
            "a_none.md": "Completely unrelated topic about birds " * 20,
            "b_partial.md": "This document discusses contract law " * 20,
            "c_full.md": "Employment contract with salary and benefits " * 20,
        })
        result = load_kb_content(kb_dir, query="employment contract salary", token_budget=100_000)
        paths = [e["relative_path"] for e in result]
        # c_full.md matches 3/3 words, b_partial.md matches 1/3, a_none.md matches 0/3
        assert paths[0] == "c_full.md"
        assert paths[1] == "b_partial.md"
        assert paths[2] == "a_none.md"


# ---------------------------------------------------------------------------
# _save_report — YAML frontmatter injection prevention
# ---------------------------------------------------------------------------

class TestSaveReportYamlSafety:
    def test_query_with_colon_is_quoted(self, tmp_path):
        kb_dir = str(tmp_path / "kb")
        os.makedirs(kb_dir)
        report_path = _save_report(kb_dir, "what is this: a test", "# Report")
        content = Path(report_path).read_text(encoding="utf-8")
        assert 'query: "what is this: a test"' in content

    def test_query_with_brackets_is_quoted(self, tmp_path):
        kb_dir = str(tmp_path / "kb")
        os.makedirs(kb_dir)
        report_path = _save_report(kb_dir, "{malicious: true}", "# Report")
        content = Path(report_path).read_text(encoding="utf-8")
        assert 'query: "{malicious: true}"' in content

    def test_query_with_newline_escaped(self, tmp_path):
        kb_dir = str(tmp_path / "kb")
        os.makedirs(kb_dir)
        report_path = _save_report(kb_dir, "query\ninjected: hacked", "# Report")
        content = Path(report_path).read_text(encoding="utf-8")
        # The newline should be escaped, not creating a new YAML field
        lines = content.split("\n")
        # Count fields in frontmatter (between first --- and second ---)
        in_frontmatter = False
        field_count = 0
        for line in lines:
            if line == "---":
                if in_frontmatter:
                    break
                in_frontmatter = True
                continue
            if in_frontmatter and line.strip():
                field_count += 1
        assert field_count == 3  # type, query, generated_at — no injection

    def test_query_with_double_quotes_escaped(self, tmp_path):
        kb_dir = str(tmp_path / "kb")
        os.makedirs(kb_dir)
        report_path = _save_report(kb_dir, 'query with "quotes"', "# Report")
        content = Path(report_path).read_text(encoding="utf-8")
        assert r'query: "query with \"quotes\""' in content

    def test_cjk_query_is_quoted(self, tmp_path):
        kb_dir = str(tmp_path / "kb")
        os.makedirs(kb_dir)
        report_path = _save_report(kb_dir, "聘僱合約分析", "# Report")
        content = Path(report_path).read_text(encoding="utf-8")
        assert 'query: "聘僱合約分析"' in content
