"""Contract tests for the supplemental EA private-match data source."""
import unittest
from ea_club import (
    endpoint,normalize,normalize_game,recent_lines,player_lines,
    games_in,integer,aggregate_players,player_summary,summary_lines,
    ranking_lines,game_report_lines
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

    def test_supplemental_aggregation_not_season_totals(self):
        matches=normalize(FIXTURE)
        result=player_summary(matches,"Buckley0306")
        self.assertEqual(result["matches"],1)
        self.assertEqual(result["goals"],2)
        self.assertEqual(result["assists"],3)
        self.assertEqual(result["shots"],4)
        self.assertIsNone(result["saves"])
        self.assertIn("NOT added to PlayCHL season totals"," ".join(summary_lines(result)))

    def test_deduplicate_matches_and_contributors(self):
        first=normalize(FIXTURE)[0]
        copy=dict(first,id="EA-988")
        copy["players"]=[dict(x) for x in first["players"]]
        combined=aggregate_players([first,first,copy])
        # Caller is expected to feed a deduplicated EA history, just as
        # normalize() does before production aggregation.
        cleaned=aggregate_players(normalize(FIXTURE))
        self.assertEqual(cleaned[0]["matches"],1)
        self.assertEqual(len(cleaned),2)
        self.assertEqual(combined[0]["matches"],3)

    def test_exact_player_matching_rejects_partial_names(self):
        matches=normalize(FIXTURE)
        self.assertIsNone(player_summary(matches,"Buckley"))
        self.assertIsNotNone(player_summary(matches,"buckley0306"))
        altered=dict(matches[0])
        altered["id"]="EA-999"
        altered["players"]=[dict(matches[0]["players"][0],
                                 id="different-ea-account")]
        self.assertIsNone(player_summary([matches[0],altered],"Buckley0306"))

    def test_rankings_and_game_report_are_source_labeled(self):
        matches=normalize(FIXTURE)
        rank=ranking_lines(matches,"points")
        self.assertIn("EA CLUB-PRIVATE",rank[0])
        self.assertTrue(any("Buckley0306" in x for x in rank))
        self.assertIn("NOT official PlayCHL",rank[1])
        report=game_report_lines(matches,"EA-987")
        self.assertIn("EA PRIVATE-MATCH REPORT",report[0])
        self.assertTrue(any("Charlielucia13" in x for x in report))
        missing=game_report_lines(matches,"not-a-match")
        self.assertIn("not found",missing[0])

    def test_reject_unknown_formats(self):
        for invalid in ({},None,"{}"):
            with self.assertRaises(ValueError):games_in(invalid)
        self.assertIsNone(integer("not-available"))

if __name__=="__main__":
    unittest.main()
