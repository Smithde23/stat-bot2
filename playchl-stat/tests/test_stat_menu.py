"""Interaction-contract tests for the source and game dropdowns."""
import unittest
from types import SimpleNamespace
import bot

class SourceMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_dropdown_shows_two_separate_feeds(self):
        menu=bot.StatSourceView(22)
        self.assertEqual(len(menu.children),1)
        select=menu.children[0]
        self.assertEqual({item.value for item in select.options},{"playchl","ea"})
        self.assertIn("EA",select.options[0].label)

    async def test_ea_game_picker_uses_index_not_match_ids(self):
        matches=[
            {"id":f"private-match-id-{index}",
             "timestamp":1794700000+index,
             "opponent":"Other club","own_goals":2,"opponent_goals":1,
             "club":"Washington","players":[]}
            for index in range(30)
        ]
        view=bot.EASelectView(22,matches)
        self.assertEqual(len(view.matches),25)
        picker=view.children[0]
        self.assertEqual(len(picker.options),25)
        self.assertEqual(picker.options[0].value,"0")
        self.assertEqual(picker.options[-1].value,"24")
        self.assertIn("Other club",picker.options[0].label)

    async def test_playchl_schedule_picker_uses_game_links(self):
        rows=[
            {"DATE":"Nov 15","AWAY":"WSH","HOME":"UTAH","TIME":"9:00 PM ET",
             "STATUS":"Final","GAME_URL":"https://playchl.com/games/abc"}
        ]
        view=bot.PlayCHLSelectView(22,rows)
        self.assertEqual(view.children[0].options[0].value,"0")
        self.assertIn("WSH vs UTAH",view.children[0].options[0].label)

    async def test_commands_available_and_ea_id_optional(self):
        names={c.name for c in bot.bot.tree.get_commands()}
        self.assertIn("stats_menu",names)
        self.assertIn("ea_game",names)
        self.assertIn("ea_recent",names)
        command=next(c for c in bot.bot.tree.get_commands() if c.name=="ea_game")
        self.assertFalse(command._params["match_id"].required)

if __name__=="__main__":
    unittest.main()
