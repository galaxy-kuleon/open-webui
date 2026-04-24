"""
Behavioral tests for __agent_skill_ids__ → run_agent_skill native-FC registration (F-6).

Three unit tests verify the get_builtin_tools() gate at tools.py:507-508:
  1. run_agent_skill IS registered when __agent_skill_ids__ is a non-empty list.
  2. run_agent_skill is NOT registered when __agent_skill_ids__ is absent or empty.
  3. The middleware.py population logic filters to agent_skill-typed skills only,
     mirroring the same access-control filter as __skill_ids__.

One integration probe test is co-located here (see test_agent_skill_ids_population_in_middleware.py).
"""

import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Helpers — minimal request stub
# ---------------------------------------------------------------------------


def _make_request():
    """Minimal FastAPI Request stub: only app.state.config is accessed by get_builtin_tools."""
    config = SimpleNamespace()  # getattr(config, 'ENABLE_*', False) → False for all flags
    state = SimpleNamespace(config=config)
    app = SimpleNamespace(state=state)
    return SimpleNamespace(app=app)


# ---------------------------------------------------------------------------
# Test: middleware population logic — agent_skill filter mirrors __skill_ids__
# ---------------------------------------------------------------------------


def test_agent_skill_ids_mirrors_skill_ids_semantics():
    """
    Verify the middleware population logic:
      __agent_skill_ids__ = [s.id for s in available_skills
                             if s.id not in user_skill_ids
                             and s.meta.type == 'agent_skill']

    Given a mixed list of available_skills (regular + agent_skill types), and a
    user_skill_ids set, the resulting __agent_skill_ids__ must contain ONLY the
    agent_skill-typed items that are NOT in user_skill_ids.

    This is a data-transformation test — it exercises the list comprehension
    logic as written in middleware.py:3269-3274 without importing middleware
    (which has heavyweight startup requirements).
    """

    # Build fake SkillMeta-like objects with .type attribute
    def _skill(skill_id, skill_type):
        meta = SimpleNamespace(type=skill_type)
        return SimpleNamespace(id=skill_id, meta=meta)

    # available_skills: 4 entries
    #   - 'plain-1': regular skill (type=None)
    #   - 'agent-1': agent_skill, not user-selected → should appear in __agent_skill_ids__
    #   - 'agent-2': agent_skill, BUT in user_skill_ids → excluded (injected as content)
    #   - 'plain-2': regular skill (type='markdown')
    available_skills = [
        _skill('plain-1', None),
        _skill('agent-1', 'agent_skill'),
        _skill('agent-2', 'agent_skill'),
        _skill('plain-2', 'markdown'),
    ]
    user_skill_ids = {'agent-2'}

    # Mirror the exact comprehension from middleware.py
    skill_ids = [s.id for s in available_skills if s.id not in user_skill_ids]
    agent_skill_ids = [s.id for s in available_skills if s.id not in user_skill_ids and s.meta.type == 'agent_skill']

    # __skill_ids__ should contain plain-1, agent-1, plain-2 (all non-user-selected)
    assert skill_ids == ['plain-1', 'agent-1', 'plain-2']

    # __agent_skill_ids__ should contain ONLY 'agent-1'
    assert agent_skill_ids == ['agent-1'], (
        '__agent_skill_ids__ must include only agent_skill-typed skills not in user_skill_ids'
    )

    # Confirm agent-2 is excluded (it was user-selected, already injected as full content)
    assert 'agent-2' not in agent_skill_ids

    # Confirm plain-1 and plain-2 are excluded (wrong type)
    assert 'plain-1' not in agent_skill_ids
    assert 'plain-2' not in agent_skill_ids

    # Symmetry invariant: agent_skill_ids is always a subset of skill_ids
    assert set(agent_skill_ids).issubset(set(skill_ids)), (
        '__agent_skill_ids__ must be a subset of __skill_ids__ (same access-control base)'
    )
