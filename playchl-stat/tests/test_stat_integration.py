import unittest
from unittest.mock import patch
from ea_club import normalize,player_summary
import bot

class StatCardIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.record={"PLAYER":"Buckley0306","TEAM":"WSH","G":"12","A":"15","P":"27"}
        self.history=normalize([{
          "matchId":"sample-game-101","timestamp":"1794700000",
          "clubs":{"1940":{"goals":"5"},"100":{"goals":"4"}},
          "players":{"1940":{"abc":{"playername":"Buckley0306","skgoals":"2",
                                    "skassists":"1","skshots":"5"}}}
        }])

    def test_ea_attaches_only_to_verified_washington_player(self):
        ea=bot.ea_for_record(self.record,self.history)
        self.assertIsNotNone(ea)
        self.assertEqual(ea["goals"],2)
        self.assertIsNone(bot.ea_for_record(dict(self.record,TEAM="SJS"),self.history))
        self.assertIsNone(bot.ea_for_record(dict(self.record,PLAYER="Buckley"),self.history))
        self.assertIsNone(bot.ea_for_record(self.record,None))

    def test_player_embed_preserves_league_values_and_separates_ea(self):
        ea=bot.ea_for_record(self.record,self.history)
        embed=bot.advanced_embed(self.record,ea_summary=ea)
        self.assertEqual(embed.fields[0].name,"Statistics")
        self.assertIn("**G**  12",embed.fields[0].value)
        self.assertTrue(any("EA private" in field.name for field in embed.fields))
        priv=next(field.value for field in embed.fields if "EA private" in field.name)
        self.assertIn("G 2",priv)
        self.assertIn("NOT added",priv)

    def test_profile_image_renders_with_ea_tag(self):
        ea=bot.ea_for_record(self.record,self.history)
        picture=bot.chel_card_bytes(self.record,ea_summary=ea)
        self.assertGreater(len(picture.getvalue()),5000)
        self.assertEqual(picture.getvalue()[:8],b'\x89PNG\r\n\x1a\n')

if __name__=="__main__":unittest.main()
