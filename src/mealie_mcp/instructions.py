"""Server instructions: what a model needs to know before its first call.

Sent once per session in the MCP initialize handshake, so every word is paid for
on every session. Keep to what models get wrong and the tool signatures do not
say; a test caps the length. Found by running every tool against a live Mealie.
"""

from __future__ import annotations

READ = """\
Mealie recipe library. Be economical: search_recipes and get_recipe take \
fields=[...]; use one filtered search, or library_stats / find_duplicate_recipes \
/ check_recipe_links, rather than looping get_recipe.

- Foods: suggest_recipes and search_recipes(foods=) only know foods registered \
in Mealie (manage_taxonomy resource=foods). An ingredient with no registered \
food is plain text and will not match.
- search_recipes(max_total_minutes=) only matches recipes with a stored \
duration; a time kept as text does not count.
"""

WRITE = """\
- A rename changes the slug; use the slug the result returns. Week-long meal \
plan entries need end_date exactly 6 days after date.
- Editing ingredients or instructions: get_recipe first, change only the lines \
you mean to, and pass the whole list back. Lines passed back unchanged keep \
their IDs and links; edited lines are re-parsed. Never rebuild a list from memory.
- Tags, categories and tools merge, and unknown names are created: read the \
"created" note back, since a typo becomes a permanent tag.
- Deletes and overwrites are destructive: confirm with the user before any \
delete_*, before replacing a list, and before bulk_tag_recipes across many \
recipes. delete_recipe and delete_shopping_list take the identifier twice.
- Batch instead of looping: add_shopping_item takes a list, add_meal_plan_entry \
takes items, add_recipe_to_shopping_list takes a list of slugs.
- An error saying "partially completed" means earlier steps already happened: \
do not repeat the call.
- import_recipe_from_url needs a public address Mealie can fetch; a 400 usually \
means the page has no recipe or is unreachable.
"""


def instructions_for(read_only: bool) -> str:
    """The instructions to send for this server's mode.

    Args:
        read_only: True when write tools are not registered, so write advice
            would only cost tokens and point at tools that are not there.

    Returns:
        The instruction text.
    """
    return READ if read_only else READ + WRITE
