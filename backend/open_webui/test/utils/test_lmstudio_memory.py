"""
Behavioral tests for lmstudio_memory.py — pure function logic.
Tests the data transformation pipeline, NOT the subprocess calls.
"""

import pytest
from unittest.mock import patch


# ── strip_provider_prefix ─────────────────────────────────────────


class TestStripProviderPrefix:
    def test_strips_lmstudio_prefix(self):
        from open_webui.utils.lmstudio_memory import strip_provider_prefix

        assert strip_provider_prefix("lmstudio.qwen3.5-122b-a10b") == "qwen3.5-122b-a10b"

    def test_preserves_non_lmstudio(self):
        from open_webui.utils.lmstudio_memory import strip_provider_prefix

        assert strip_provider_prefix("openrouter.qwen/qwen3-8b") == "openrouter.qwen/qwen3-8b"

    def test_preserves_bare_name(self):
        from open_webui.utils.lmstudio_memory import strip_provider_prefix

        assert strip_provider_prefix("qwen3.5-122b-a10b") == "qwen3.5-122b-a10b"

    def test_only_strips_first_lmstudio_prefix(self):
        from open_webui.utils.lmstudio_memory import strip_provider_prefix

        # Edge case: model name that starts with lmstudio
        assert strip_provider_prefix("lmstudio.lmstudio-test") == "lmstudio-test"


# ── find_model ────────────────────────────────────────────────────


class TestFindModel:
    MODELS = [
        {"modelKey": "qwen3.5-122b-a10b", "sizeBytes": 58_775_723_624},
        {"modelKey": "gemma-4-26b-a4b-it", "sizeBytes": 26_120_000_000},
        {"identifier": "qwen3.5-27b", "sizeBytes": 22_010_000_000},
    ]

    def test_find_by_model_key(self):
        from open_webui.utils.lmstudio_memory import find_model

        result = find_model(self.MODELS, "qwen3.5-122b-a10b")
        assert result is not None
        assert result["sizeBytes"] == 58_775_723_624

    def test_find_by_identifier(self):
        from open_webui.utils.lmstudio_memory import find_model

        result = find_model(self.MODELS, "qwen3.5-27b")
        assert result is not None
        assert result["sizeBytes"] == 22_010_000_000

    def test_not_found(self):
        from open_webui.utils.lmstudio_memory import find_model

        assert find_model(self.MODELS, "nonexistent") is None

    def test_empty_list(self):
        from open_webui.utils.lmstudio_memory import find_model

        assert find_model([], "qwen3.5-122b-a10b") is None


# ── estimate_runtime_bytes ────────────────────────────────────────


class TestEstimateRuntime:
    def test_applies_overhead_multiplier(self):
        from open_webui.utils.lmstudio_memory import estimate_runtime_bytes

        model_size = 58_000_000_000  # ~58 GB
        result = estimate_runtime_bytes(model_size)
        # Default multiplier is 1.25
        assert result == int(model_size * 1.25)

    def test_zero_size(self):
        from open_webui.utils.lmstudio_memory import estimate_runtime_bytes

        assert estimate_runtime_bytes(0) == 0


# ── loaded_memory_bytes ───────────────────────────────────────────


class TestLoadedMemory:
    def test_sums_loaded_model_sizes(self):
        from open_webui.utils.lmstudio_memory import loaded_memory_bytes

        mock_loaded = [
            {"modelKey": "a", "sizeBytes": 10_000_000_000},
            {"modelKey": "b", "sizeBytes": 20_000_000_000},
        ]
        with patch("open_webui.utils.lmstudio_memory.get_loaded_models", return_value=mock_loaded):
            assert loaded_memory_bytes() == 30_000_000_000

    def test_empty_loaded(self):
        from open_webui.utils.lmstudio_memory import loaded_memory_bytes

        with patch("open_webui.utils.lmstudio_memory.get_loaded_models", return_value=[]):
            assert loaded_memory_bytes() == 0

    def test_missing_size_bytes(self):
        from open_webui.utils.lmstudio_memory import loaded_memory_bytes

        mock_loaded = [{"modelKey": "a"}, {"modelKey": "b", "sizeBytes": 5_000_000_000}]
        with patch("open_webui.utils.lmstudio_memory.get_loaded_models", return_value=mock_loaded):
            assert loaded_memory_bytes() == 5_000_000_000


# ── check_can_load_model ──────────────────────────────────────────


class TestCheckCanLoadModel:
    """Integration-level tests for the main check function."""

    def _patch_lms(self, loaded, all_models):
        """Helper: patch both get_loaded_models and get_all_models."""
        return [
            patch("open_webui.utils.lmstudio_memory.get_loaded_models", return_value=loaded),
            patch("open_webui.utils.lmstudio_memory.get_all_models", return_value=all_models),
            patch("open_webui.utils.lmstudio_memory.MEMORY_BUDGET_GB", 120),
        ]

    def test_non_lmstudio_model_returns_none(self):
        from open_webui.utils.lmstudio_memory import check_can_load_model

        assert check_can_load_model("openrouter.qwen/qwen3-8b") is None

    def test_already_loaded_returns_none(self):
        from open_webui.utils.lmstudio_memory import check_can_load_model

        loaded = [{"modelKey": "qwen3.5-122b-a10b", "sizeBytes": 58_000_000_000}]
        all_models = [{"modelKey": "qwen3.5-122b-a10b", "sizeBytes": 58_000_000_000}]
        patches = self._patch_lms(loaded, all_models)
        for p in patches:
            p.start()
        try:
            assert check_can_load_model("lmstudio.qwen3.5-122b-a10b") is None
        finally:
            for p in patches:
                p.stop()

    def test_model_not_in_ls_returns_none(self):
        from open_webui.utils.lmstudio_memory import check_can_load_model

        patches = self._patch_lms([], [])
        for p in patches:
            p.start()
        try:
            assert check_can_load_model("lmstudio.nonexistent") is None
        finally:
            for p in patches:
                p.stop()

    def test_enough_memory_returns_none(self):
        """Budget 120GB, nothing loaded, target 22GB → fits."""
        from open_webui.utils.lmstudio_memory import check_can_load_model

        loaded = []
        all_models = [{"modelKey": "qwen3.5-27b", "sizeBytes": 22_000_000_000}]
        patches = self._patch_lms(loaded, all_models)
        for p in patches:
            p.start()
        try:
            result = check_can_load_model("lmstudio.qwen3.5-27b")
            assert result is None  # Fits: 22 * 1.25 = 27.5 < 120
        finally:
            for p in patches:
                p.stop()

    def test_insufficient_memory_returns_warning(self):
        """Budget 120GB, 59GB loaded, target 60GB → doesn't fit."""
        from open_webui.utils.lmstudio_memory import check_can_load_model

        loaded = [{"modelKey": "qwen3.5-122b-a10b", "sizeBytes": 59_000_000_000}]
        all_models = [
            {"modelKey": "qwen3.5-122b-a10b", "sizeBytes": 59_000_000_000},
            {"modelKey": "mistral-small-4-119b", "sizeBytes": 60_000_000_000},
        ]
        patches = self._patch_lms(loaded, all_models)
        for p in patches:
            p.start()
        try:
            result = check_can_load_model("lmstudio.mistral-small-4-119b")
            assert result is not None
            assert "warning" in result
            assert "mistral-small-4-119b" in result["warning"]
            assert result["headroom_gb"] < 0
        finally:
            for p in patches:
                p.stop()

    def test_warning_includes_loaded_model_names(self):
        """Warning message should list currently loaded models."""
        from open_webui.utils.lmstudio_memory import check_can_load_model

        loaded = [{"modelKey": "big-model", "sizeBytes": 100_000_000_000}]
        all_models = [
            {"modelKey": "big-model", "sizeBytes": 100_000_000_000},
            {"modelKey": "another-big", "sizeBytes": 50_000_000_000},
        ]
        patches = self._patch_lms(loaded, all_models)
        for p in patches:
            p.start()
        try:
            result = check_can_load_model("lmstudio.another-big")
            assert result is not None
            assert "big-model" in result["warning"]
        finally:
            for p in patches:
                p.stop()

    def test_tight_fit_still_ok(self):
        """Budget 120GB, nothing loaded, target 90GB → 90*1.25=112.5 < 120 → OK."""
        from open_webui.utils.lmstudio_memory import check_can_load_model

        loaded = []
        all_models = [{"modelKey": "model-90gb", "sizeBytes": 90_000_000_000}]
        patches = self._patch_lms(loaded, all_models)
        for p in patches:
            p.start()
        try:
            # 90 * 1.25 = 112.5 GB, budget = 120 → fits
            assert check_can_load_model("lmstudio.model-90gb") is None
        finally:
            for p in patches:
                p.stop()

    def test_tight_fit_over_budget(self):
        """Budget 120 GiB, nothing loaded, target 100 GiB → 100*1.25=125 > 120 → warn."""
        from open_webui.utils.lmstudio_memory import check_can_load_model

        GiB = 1024**3
        loaded = []
        # Use exact GiB so the math is unambiguous
        all_models = [{"modelKey": "model-100gib", "sizeBytes": 100 * GiB}]
        patches = self._patch_lms(loaded, all_models)
        for p in patches:
            p.start()
        try:
            # 100 GiB * 1.25 = 125 GiB > 120 GiB budget → warn
            result = check_can_load_model("lmstudio.model-100gib")
            assert result is not None
            assert result["headroom_gb"] < 0
        finally:
            for p in patches:
                p.stop()


# ── format_gb ─────────────────────────────────────────────────────


class TestFormatGb:
    def test_format(self):
        from open_webui.utils.lmstudio_memory import format_gb

        assert format_gb(58_775_723_624) == "54.7 GB"

    def test_zero(self):
        from open_webui.utils.lmstudio_memory import format_gb

        assert format_gb(0) == "0.0 GB"
