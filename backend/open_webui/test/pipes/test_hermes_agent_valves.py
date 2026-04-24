"""Unit tests for Pipe.Valves mandatory hermes_api_key validation.

Verifies:
- Valves() with no hermes_api_key raises pydantic.ValidationError.
- Valves(hermes_api_key='   ') (whitespace-only) raises pydantic.ValidationError.
- Valves(hermes_api_key='abc') succeeds and preserves the value.
"""

import pytest
from pydantic import ValidationError

from open_webui.pipes.hermes_agent import Pipe


class TestHermesApiKeyValidation:
    def test_missing_key_raises_validation_error(self):
        """Instantiating Valves without hermes_api_key must raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            Pipe.Valves()
        errors = exc_info.value.errors()
        assert any(e['loc'] == ('hermes_api_key',) for e in errors), (
            f'Expected hermes_api_key field error, got: {errors}'
        )

    def test_empty_string_key_raises_validation_error(self):
        """Valves(hermes_api_key='') must raise ValidationError (min_length=1)."""
        with pytest.raises(ValidationError) as exc_info:
            Pipe.Valves(hermes_api_key='')
        errors = exc_info.value.errors()
        assert any(e['loc'] == ('hermes_api_key',) for e in errors), (
            f'Expected hermes_api_key field error, got: {errors}'
        )

    def test_whitespace_only_key_raises_validation_error(self):
        """Valves(hermes_api_key='   ') must raise ValidationError (model_validator rejects whitespace)."""
        with pytest.raises(ValidationError) as exc_info:
            Pipe.Valves(hermes_api_key='   ')
        errors = exc_info.value.errors()
        # The model_validator fires after field-level checks; the error loc may be
        # either the field or the model root depending on pydantic version.
        assert len(errors) >= 1, f'Expected at least one validation error, got: {errors}'

    def test_tab_and_newline_whitespace_raises_validation_error(self):
        """Whitespace variants (tab, newline) are also rejected."""
        for ws in ('\t', '\n', ' \t\n '):
            with pytest.raises(ValidationError, match='whitespace'):
                Pipe.Valves(hermes_api_key=ws)

    def test_valid_key_succeeds(self):
        """Valves(hermes_api_key='abc') must succeed and expose the key unchanged."""
        valves = Pipe.Valves(hermes_api_key='abc')
        assert valves.hermes_api_key == 'abc'

    def test_valid_key_with_defaults(self):
        """Other fields keep their defaults when only hermes_api_key is provided."""
        valves = Pipe.Valves(hermes_api_key='my-secret-token')
        assert valves.hermes_api_url == 'http://localhost:8642'
        assert valves.request_timeout == 600
        assert valves.health_check_timeout == 2.0

    def test_valid_key_preserves_surrounding_whitespace(self):
        """A key with surrounding whitespace is accepted (strip is NOT applied to the key itself)."""
        valves = Pipe.Valves(hermes_api_key='  token  ')
        # The model_validator only rejects all-whitespace; a key with non-whitespace chars is fine.
        assert valves.hermes_api_key == '  token  '
