import unittest
from playchl_game import valid_game_url,parse_detail,game_lines,fallback_lines

ROW={
    "AWAY":"WSH","HOME":"UTAH",
    "DATE":"Nov 15","TIME":"8:00 PM ET","STATUS":"Final",
    "GAME_URL":"https://playchl.com/games/abcdef123456"
}
PAYLOAD={
    "body":"Washington Capitals versus Utah Hockey Club, match summary and statistics",
    "tables":[
        {"heading":"Match Stats","header":["Washington","Statistic","Utah"],
         "rows":[["6","Goals","7"],["18","Shots","21"],["9","Hits","13"]]},
        {"heading":"Washington Capitals Skaters",
         "header":["Player","Pos","G","A","P","SH","HIT"],
         "rows":[["Buckley0306","C","2","3","5","5","0"],
                 ["Reqhzii","RW","1","1","2","3","2"]]},
        {"heading":"Utah Hockey Club Skaters",
         "header":["Player","Pos","G","A","P","SH"],
         "rows":[["OffsKripT","C","4","2","6","8"]]},
        {"heading":"Washington Capitals Goalies",
         "header":["Player","Pos","SV","SA","SV%"],
         "rows":[["Charlielucia13","G","14","21","66.7%"]]},
    ]
}

class PlayCHLGameTests(unittest.TestCase):
    def test_url_allowlist(self):
        self.assertTrue(valid_game_url(ROW["GAME_URL"]))
        self.assertFalse(valid_game_url("https://other-site.com/games/abcdef"))
        self.assertFalse(valid_game_url("https://playchl.com/games/a/../login"))
        self.assertFalse(valid_game_url("http://playchl.com/games/abc"))

    def test_verified_box_stats_render_inside_discord(self):
        detail=parse_detail(PAYLOAD,"WSH","UTAH")
        self.assertEqual(detail["match"]["Goals"],("6","7"))
        self.assertEqual(len(detail["players"]),4)
        lines=game_lines(ROW,detail)
        joined="\n".join(lines)
        for marker in ("Goals: 6 — 7","Buckley0306","G 2","A 3",
                       "Charlielucia13","SV 14","OffsKripT"):
            self.assertIn(marker,joined)
        self.assertNotIn("https://",joined)

    def test_missing_page_data_must_not_invent_stats(self):
        with self.assertRaises(ValueError):
            parse_detail({"body":"Washington Capitals", "tables":[]},
                         "WSH","UTAH")
        lines="\n".join(fallback_lines(ROW))
        self.assertNotIn("G 2",lines)
        self.assertIn("not expose a readable",lines)

    def test_wrong_team_section_cannot_inject_player(self):
        payload=dict(PAYLOAD)
        payload["tables"]=[{"heading":"Random Team Skaters","header":["Player","Pos","G","A","P"],
                            "rows":[["UnknownGuy","C","8","8","16"]]},
                           PAYLOAD["tables"][0]]
        detail=parse_detail(payload,"WSH","UTAH")
        self.assertFalse(detail["players"])
        self.assertIn("Goals",detail["match"])

    def test_corrupt_statistics_not_displayed(self):
        payload=dict(PAYLOAD)
        payload["tables"]=[
            PAYLOAD["tables"][0],
            {"heading":"Washington Capitals Skaters","header":["Player","Pos","G","A","P"],
             "rows":[["Fake","C","five","none","?"]]}]
        detail=parse_detail(payload,"WSH","UTAH")
        self.assertEqual(detail["players"],[])

if __name__=="__main__":
    unittest.main()
