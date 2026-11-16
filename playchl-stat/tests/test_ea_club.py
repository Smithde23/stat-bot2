"""Contract tests for the supplemental EA private-match data source."""
import unittest
from ea_club import (
    endpoint,normalize,normalize_game,recent_lines,player_lines,
    games_in,integer
)

FIXTURE=[
    {"matchId":"EA-987","timestamp":"1794700000",
     "clubs":{
         "1940":{"details":{"name":"Washington Capitals"},"opponentClubId":"929","goals":"6"},
         "929":{"details":{"name":"Utah Mammoth"},"opponentClubId":"1940","goals":"7"}
     },
     "players":{"1940":{
         "player-a":{"playername":"Buckley0306","posSorted":"C",
                     "skgoals":"2","skassists":"3","skshots":"4","skhits":"0"},
         "goalie-1":{"playername":"Charlielucia13","posSorted":"G",
                     "glsaves":"13","glshots":"20"}
     }}
    },
    {"matchId":"EA-987","timestamp":"1794700000",
     "clubs":{"1940":{"details":{"name":"Washington Capitals"}}}},
    {"matchId":"EA-unrelated","timestamp":"1794700001",
     "clubs":{"222":{"details":{"name":"Other Team"}}}}
]

class EAClubTests(unittest.TestCase):
    def test_endpoint_contract(self):
        uri=endpoint()
        self.assertIn("platform=common-gen5",uri)
        self.assertIn("clubIds=1940",uri)
        self.assertIn("matchType=club_private",uri)

    def test_validated_players_and_scores(self):
        match=normalize_game(FIXTURE[0])
        self.assertEqual(match["id"],"EA-987")
        self.assertEqual((match["own_goals"],match["opponent_goals"]),(6,7))
        self.assertEqual(match["opponent"],"Utah Mammoth")
        self.assertEqual(len(match["players"]),2)
        self.assertEqual(match["players"][0]["goals"],2)
        self.assertEqual(match["players"][0]["assists"],3)
        self.assertEqual(match["players"][1]["saves"],13)

    def test_club_scoped_and_deduplicated(self):
        matches=normalize(FIXTURE)
        self.assertEqual(len(matches),1)
        self.assertEqual(matches[0]["id"],"EA-987")
        self.assertEqual(len(matches[0]["players"]),2)
        self.assertNotIn("EA-unrelated",str(matches))

    def test_output_labels_are_supplementary(self):
        matches=normalize(FIXTURE[:1])
        recent="\n".join(recent_lines(matches))
        player="\n".join(player_lines(matches,"Buckley"))
        self.assertIn("NOT PlayCHL league standings",recent)
        self.assertIn("NOT PlayCHL season totals",player)
        self.assertIn("G 2",player)
        self.assertIn("A 3",player)
        self.assertIn("EA-987",player)

    def test_missing_fields_are_not_fabricated(self):
        record=normalize_game({"matchId":"test","clubs":{"1940":{}},"players":{}})
        self.assertIsNone(record["own_goals"])
        self.assertIsNone(record["opponent_goals"])
        self.assertEqual(record["players"],[])
        self.assertNotIn("0-0","\n".join(recent_lines([record])))

    def test_reject_unknown_formats(self):
        for invalid in ({},None,"{}"):
            with self.assertRaises(ValueError):games_in(invalid)
        self.assertIsNone(integer("not-available"))

if __name__=="__main__":
    unittest.main()
