"""Editing one ingredient or step must not rewrite the others.

Opt-in like the rest of tests/integration: MEALIE_INTEGRATION=1 plus MEALIE_URL
and MEALIE_API_TOKEN, against a throwaway instance (scripts/integration.sh).
"""

from __future__ import annotations

import os

import httpx
import pytest
from conftest import data
from fastmcp import Client
from fastmcp.exceptions import ToolError

from mealie_mcp.config import Config
from mealie_mcp.server import build_server

pytestmark = pytest.mark.skipif(
    os.environ.get("MEALIE_INTEGRATION") != "1",
    reason="integration tests run via scripts/integration.sh (MEALIE_INTEGRATION=1)",
)


@pytest.fixture
async def client():
    async with Client(build_server(Config.from_env())) as c:
        yield c


def _mealie() -> httpx.Client:
    return httpx.Client(
        base_url=os.environ["MEALIE_URL"],
        headers={"Authorization": f"Bearer {os.environ['MEALIE_API_TOKEN']}"},
        timeout=30,
    )


async def test_editing_one_line_keeps_the_rest_intact(client):
    created = await client.call_tool(
        "create_recipe",
        {
            "name": "Integration Round Trip Stew",
            "ingredients": ["2 cups flour", "1 tbsp sugar", "1 onion", "a pinch of salt"],
            "instructions": ["Mix.", "Simmer.", "Serve."],
        },
    )
    slug = data(created)["slug"]
    try:
        with _mealie() as mealie:
            raw = mealie.get(f"/api/recipes/{slug}").json()
            refs = [i["referenceId"] for i in raw["recipeIngredient"]]
            # Give step 1 a title and a link to the first ingredient, the data a
            # whole-list replace used to destroy.
            raw["recipeInstructions"][0]["title"] = "Dough"
            raw["recipeInstructions"][0]["ingredientReferences"] = [{"referenceId": refs[0]}]
            mealie.patch(f"/api/recipes/{slug}", json=raw).raise_for_status()

            shown = data(await client.call_tool("get_recipe", {"slug": slug}))
            ingredients = list(shown["ingredients"])
            steps = list(shown["instructions"])
            assert steps[0].startswith("Dough")
            ingredients[2] = "2 onions"
            steps[2] = "Serve hot."

            result = data(
                await client.call_tool(
                    "update_recipe",
                    {"slug": slug, "ingredients": ingredients, "instructions": steps},
                )
            )
            assert result["lists"]["ingredients"] == {"kept": 3, "rewritten": 1}
            assert result["lists"]["instructions"] == {"kept": 2, "rewritten": 1}

            after = mealie.get(f"/api/recipes/{slug}").json()
            after_refs = [i["referenceId"] for i in after["recipeIngredient"]]
            assert after_refs[:2] == refs[:2]
            assert after_refs[3] == refs[3]
            assert after_refs[2] != refs[2]
            first = after["recipeInstructions"][0]
            assert first["title"] == "Dough"
            assert [r["referenceId"] for r in first["ingredientReferences"]] == [refs[0]]
            assert after["recipeInstructions"][2]["text"] == "Serve hot."

        with pytest.raises(ToolError, match="confirm_replace_all"):
            await client.call_tool("update_recipe", {"slug": slug, "ingredients": []})
    finally:
        await client.call_tool("delete_recipe", {"slug": slug, "confirm_slug": slug})


async def test_times_written_here_are_found_by_duration_search(client):
    from test_live import MEALIE_VERSION

    created = await client.call_tool(
        "create_recipe",
        {"name": "Integration Timed Stew", "prep_time": "10 minutes", "cook_time": "15 minutes"},
    )
    slug = data(created)["slug"]
    try:
        assert data(created)["total_time"] == "25 minutes"

        async def found(minutes: int) -> bool:
            result = await client.call_tool(
                "search_recipes", {"query": "Integration Timed Stew", "max_total_minutes": minutes}
            )
            return any(r["slug"] == slug for r in data(result)["items"])

        if MEALIE_VERSION >= (3, 29):  # the duration filter is a 3.29 feature
            assert await found(30)
            assert not await found(20)

        updated = await client.call_tool("update_recipe", {"slug": slug, "cook_time": "2 hours"})
        assert data(updated)["total_time"] == "2 hours 10 minutes"
        if MEALIE_VERSION >= (3, 29):
            assert not await found(30)
            assert await found(180)
    finally:
        await client.call_tool("delete_recipe", {"slug": slug, "confirm_slug": slug})
