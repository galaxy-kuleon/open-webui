from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from open_webui.functions import get_function_module_by_id


class _Valves(BaseModel):
    hermes_api_key: str


@pytest.mark.asyncio
async def test_get_function_module_preserves_env_seeded_valves_when_db_empty(monkeypatch):
    seeded = _Valves(hermes_api_key='env-seeded-key')
    function_module = SimpleNamespace(Valves=_Valves, valves=seeded)

    monkeypatch.setattr(
        'open_webui.functions.get_function_module_from_cache',
        AsyncMock(return_value=(function_module, 'pipe', {})),
    )
    monkeypatch.setattr(
        'open_webui.functions.Functions.get_function_valves_by_id',
        AsyncMock(return_value={}),
    )

    result = await get_function_module_by_id(SimpleNamespace(), 'hermes_agent')

    assert result.valves.hermes_api_key == 'env-seeded-key'
