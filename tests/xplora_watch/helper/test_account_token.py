"""Tests for the account-token resolver (helper.account_token).

The token differentiates the same watch when it is linked to several accounts. Its resolution
order is **Account alias -> Account display name (`getUserName()`) -> opaque account id**. The
alias is now user-settable (config + options flow), so these tests cover all three steps.
"""

from __future__ import annotations

import pytest
from homeassistant.util import slugify

from custom_components.xplora_watch.helper import account_token
from tests.xplora_watch.fixtures.graphql_payloads import DEFAULT_ACCOUNT_NAME, DEFAULT_USER_ID


def test_account_token_prefers_alias() -> None:
    """A non-empty alias is the token, taking precedence over the display name and account id."""
    assert account_token("Mom", DEFAULT_ACCOUNT_NAME, DEFAULT_USER_ID) == "Mom"


@pytest.mark.parametrize("alias", ["", "   "])
def test_account_token_falls_back_to_display_name(alias: str) -> None:
    """With no (empty/whitespace) alias and a non-empty display name, the token is the display name."""
    assert account_token(alias, DEFAULT_ACCOUNT_NAME, DEFAULT_USER_ID) == DEFAULT_ACCOUNT_NAME


@pytest.mark.parametrize("alias", ["", "   "])
@pytest.mark.parametrize("display_name", ["", "   "])
def test_account_token_falls_back_to_account_id(alias: str, display_name: str) -> None:
    """With neither an alias nor a display name, the token falls back to the opaque account id."""
    assert account_token(alias, display_name, DEFAULT_USER_ID) == DEFAULT_USER_ID


@pytest.mark.parametrize("alias", ["👍", "!!!", "———"])
def test_account_token_returns_non_slugifiable_alias_verbatim(alias: str) -> None:
    """A non-whitespace alias that carries no slug-usable characters (emoji, punctuation-only) still
    wins the resolver verbatim: its job is to label the *device* ("Dana Watch (👍)"), so it is
    returned as-is rather than pretending an empty alias to fall through to the display name. It
    simply contributes nothing to the *entity slug* -- that is cosmetic (see helper.account_token /
    entity.branded_object_id), and HA de-duplicates any resulting slug collision on its own."""
    assert account_token(alias, DEFAULT_ACCOUNT_NAME, DEFAULT_USER_ID) == alias


@pytest.mark.parametrize("alias", ["👍", "!!!", "———"])
def test_non_slugifiable_alias_contributes_nothing_to_the_slug(alias: str) -> None:
    """The honest half of the above: such a token drops out of the joined slug, so the entity id
    falls back predictably to its pre-token form instead of gaining a bogus differentiator."""
    token = account_token(alias, DEFAULT_ACCOUNT_NAME, DEFAULT_USER_ID)
    assert slugify(f"xplora_kid_one_watch_battery {token}") == slugify("xplora_kid_one_watch_battery")
