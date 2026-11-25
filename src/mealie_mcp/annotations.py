"""MCP hints for every tool, in one table.

Clients use these to decide when to ask a human first: a read can run freely,
an overwrite or delete deserves a prompt. They live here, not on 43 decorators,
so a reviewer sees the whole safety surface at once and a test can prove that
no tool ships unlabelled.
"""

from __future__ import annotations

from mcp.types import ToolAnnotations


def _read(*, open_world: bool = False) -> dict[str, bool]:
    return {"readOnlyHint": True, "openWorldHint": open_world}


def _add(*, open_world: bool = False) -> dict[str, bool]:
    """Creates something new; repeating the call makes another one."""
    return {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": open_world,
    }


def _set(*, open_world: bool = False) -> dict[str, bool]:
    """Overwrites fields with the values given; repeating it changes nothing more."""
    return {
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": open_world,
    }


def _overwrite() -> dict[str, bool]:
    """Changes or replaces existing data in a way a repeat can compound."""
    return {
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": False,
    }


TOOL_HINTS: dict[str, dict[str, bool]] = {
    # recipes
    "search_recipes": _read(),
    "get_recipe": _read(),
    "suggest_recipes": _read(),
    "create_recipe": _add(),
    "update_recipe": _overwrite(),
    "set_recipe_image": _set(open_world=True),
    "upload_recipe_image": _set(),
    "bulk_tag_recipes": _overwrite(),
    "delete_recipe": _overwrite(),
    "import_recipe_from_url": _add(open_world=True),
    "duplicate_recipe": _add(),
    "import_recipe_from_images": _add(),
    # meal plan
    "get_meal_plan": _read(),
    "get_todays_meals": _read(),
    "add_meal_plan_entry": _add(),
    "update_meal_plan_entry": _set(),
    "delete_meal_plan_entry": _overwrite(),
    "random_meal_plan": _add(),
    # cookbooks
    "list_cookbooks": _read(),
    "get_cookbook_recipes": _read(),
    "create_cookbook": _add(),
    "update_cookbook": _set(),
    "delete_cookbook": _overwrite(),
    # shopping
    "list_shopping_lists": _read(),
    "get_shopping_list": _read(),
    "create_shopping_list": _add(),
    "add_shopping_item": _add(),
    "update_shopping_item": _set(),
    "delete_shopping_item": _overwrite(),
    "add_recipe_to_shopping_list": _add(),
    "delete_shopping_list": _overwrite(),
    # feedback and history
    "get_recipe_timeline": _read(),
    "get_recipe_rating": _read(),
    "get_recipe_comments": _read(),
    "mark_recipe_made": _add(),
    "rate_recipe": _set(),
    "add_recipe_comment": _add(),
    "delete_recipe_comment": _overwrite(),
    # library and admin
    "parse_ingredients": _read(),
    "library_stats": _read(),
    "find_duplicate_recipes": _read(),
    "check_recipe_links": _read(open_world=True),
    # Reads by default; its write actions only exist when the server is not
    # read-only, so hints_for() flips it per mode.
    "manage_taxonomy": _overwrite(),
}


def hints_for(name: str, read_only: bool = False) -> ToolAnnotations:
    """The annotations for one tool.

    Args:
        name: A tool name registered by this server.
        read_only: True when the server runs with MEALIE_READ_ONLY, which
            leaves manage_taxonomy with only its read actions.

    Returns:
        The tool's annotations.

    Raises:
        KeyError: If the tool has no entry, so an unlabelled tool fails loudly.
    """
    if name == "manage_taxonomy" and read_only:
        return ToolAnnotations.model_validate(_read())
    return ToolAnnotations.model_validate(TOOL_HINTS[name])
