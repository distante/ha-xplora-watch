"""Tests for the account-token resolver (helper.account_token).

The token differentiates the same watch when it is linked to several accounts. Its resolution
order is Account alias -> Account display name (`getUserName()`) -> opaque account id. The alias
is not user-settable yet, so callers pass the display name and these tests cover the
display-name and account-id steps.
"""

from __future__ import annotations

import pytest

from custom_components.xplora_watch.helper import account_token
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_ACCOUNT_NAME, DEFAULT_USER_ID


def test_account_token_uses_display_name() -> None:
    """With a non-empty Account display name and no alias, the token is the display name."""
    assert account_token(DEFAULT_ACCOUNT_NAME, DEFAULT_USER_ID) == DEFAULT_ACCOUNT_NAME


@pytest.mark.parametrize("display_name", ["", "   "])
def test_account_token_falls_back_to_account_id(display_name: str) -> None:
    """An empty or whitespace-only display name falls back to the opaque account id."""
    assert account_token(display_name, DEFAULT_USER_ID) == DEFAULT_USER_ID
