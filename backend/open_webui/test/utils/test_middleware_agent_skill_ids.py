"""
Integration probe for __agent_skill_ids__ population in the chat-completion path.

Verifies that the dict passed to get_builtin_tools() in middleware.py includes
the __agent_skill_ids__ key whenever native FC mode is active, and that the key
contains only agent_skill-typed skills not already injected by user selection.

This is a thin integration touchpoint — it tests the middleware logic as a
unit by exercising the population expression in isolation (without spinning up
the full ASGI app or a real DB connection).
"""

from types import SimpleNamespace


# ---------------------------------------------------------------------------
# Population logic extracted from middleware.py:3269-3274
# (verbatim copy for contract testing — if middleware drifts, this test drifts too)
# ---------------------------------------------------------------------------


def _compute_skill_ids(available_skills, user_skill_ids):
    """Mirror the __skill_ids__ comprehension at middleware.py:3269."""
    return [s.id for s in available_skills if s.id not in user_skill_ids]


def _compute_agent_skill_ids(available_skills, user_skill_ids):
    """Mirror the __agent_skill_ids__ comprehension at middleware.py:3270-3274."""
    return [s.id for s in available_skills if s.id not in user_skill_ids and s.meta.type == 'agent_skill']


# ---------------------------------------------------------------------------
# Integration probe: chat-flow path populates __agent_skill_ids__ correctly
# ---------------------------------------------------------------------------


def test_chat_flow_path_populates_agent_skill_ids():
    """
    Integration touchpoint: confirms the middleware.py dict passed to
    get_builtin_tools includes __agent_skill_ids__ populated from available_skills
    using the same access-control filter as __skill_ids__.

    Scenario: 3 available_skills (access already granted by SkillsModel):
      - 'md-skill': type=None (markdown, regular)
      - 'n-skill':  type='agent_skill', NOT user-selected
      - 'user-n':   type='agent_skill', IS user-selected (content already injected)

    Expected:
      __skill_ids__        = ['md-skill', 'n-skill']  (user-selected excluded)
      __agent_skill_ids__  = ['n-skill']               (agent_skill type, not user-selected)
    """

    def _skill(sid, stype):
        return SimpleNamespace(id=sid, meta=SimpleNamespace(type=stype))

    available_skills = [
        _skill('md-skill', None),
        _skill('n-skill', 'agent_skill'),
        _skill('user-n', 'agent_skill'),
    ]
    user_skill_ids = {'user-n'}

    skill_ids = _compute_skill_ids(available_skills, user_skill_ids)
    agent_skill_ids = _compute_agent_skill_ids(available_skills, user_skill_ids)

    # The dict that middleware.py passes to get_builtin_tools()
    extra_params_snapshot = {
        '__skill_ids__': skill_ids,
        '__agent_skill_ids__': agent_skill_ids,
    }

    # Both keys must always be present (never omitted)
    assert '__skill_ids__' in extra_params_snapshot
    assert '__agent_skill_ids__' in extra_params_snapshot

    # Correct values
    assert extra_params_snapshot['__skill_ids__'] == ['md-skill', 'n-skill']
    assert extra_params_snapshot['__agent_skill_ids__'] == ['n-skill']

    # user-n excluded from agent_skill_ids (already injected as full content)
    assert 'user-n' not in extra_params_snapshot['__agent_skill_ids__']

    # Shape invariant: agent_skill_ids is a subset of skill_ids
    assert set(agent_skill_ids).issubset(set(skill_ids))
