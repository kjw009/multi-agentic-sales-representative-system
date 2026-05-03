from unittest.mock import patch

import pytest

from packages.agents.pricing import agent


def test_get_sentence_model_missing_dependency_raises():
    agent._META = {"sentence_model_name": "all-MiniLM-L6-v2"}
    agent._ST_MODEL = None
    agent._ST_LOAD_ATTEMPTED = False

    with (
        patch("packages.agents.pricing.agent.find_spec", return_value=None),
        pytest.raises(RuntimeError, match="sentence-transformers is required for pricing v3"),
    ):
        agent._get_sentence_model()
