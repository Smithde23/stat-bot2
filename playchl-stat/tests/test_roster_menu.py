"""Tests based on the live PlayCHL Washington team page's roster format."""
import unittest
import bot
from roster import parse_roster,TEAM_PROFILE,last_verified_roster

SNAPSHOT="""CHL
TEAM PROFILE
Washington Capitals
WSH
TEAM LEADERS
ROSTER
SKATERS
Wristshot_34
RW/LW
$1M
GP 18
G 44
A 25
Pts 69
Sh 115
Hit 55
LEAN BEEEF
D
$1M
GP 15
G 7
A 21
Pts 28
Sh 43
Hit 45
Reqhzii
AGM
Exempt
GP 28
G 9
A 39
Pts 48
Sh 83
Hit 42
Charlielucia13
RW
$1M
GP 0
G 0
A 0
Pts 0
Sh 0
Hit 0
GOALIES
No goalies yet.
TC PLAYERS
CH3LK1NG
(TC)
C / LW / RW / LD / RD / G
savsman99
(TC)
Exempt
Bad at NHL
(TC)
Exempt
BusBeanWPG
(TC)
Exempt
foodude56
(TC)
C / LW / RW / LD / RD / G
Continental Hockey League · EA NHL League Platform"""

class RosterTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_live_page_roster_and_tcs(self):
        records=parse_roster(SNAPSHOT)
        self.assertEqual(len(records),9)
        names={p["name"] for p in records}
        for name in ("Wristshot_34","LEAN BEEEF","Reqhzii","Charlielucia13",
                     "CH3LK1NG","savsman99","Bad at NHL","BusBeanWPG","foodude56"):
            self.assertIn(name,names)
        self.assertEqual({p["name"] for p in records if p["status"]=="TC"},
                         {"CH3LK1NG","savsman99","Bad at NHL","BusBeanWPG","foodude56"})
        player=next(p for p in records if p["name"]=="LEAN BEEEF")
        self.assertEqual(player["stats"]["PTS"],"28")
        self.assertEqual(player["position"],"D")

    def test_roster_and_tc_stay_distinct_from_advanced_stats(self):
        records=parse_roster(SNAPSHOT)
        tc=next(p for p in records if p["name"]=="CH3LK1NG")
        embed=bot.roster_player_embed(tc)
        self.assertIn("Training Camp",embed.description)
        self.assertIn("No matching published",embed.fields[-1].value)
        self.assertEqual(embed.url,TEAM_PROFILE)

    def test_paginate_full_roster_without_25_option_limit(self):
        records=parse_roster(SNAPSHOT)
        extended=records+[{"name":f"Player{i}","status":"TC","position":"","stats":{}} for i in range(36)]
        first=bot.CapitalsRosterView(111,extended)
        self.assertEqual(len(next(x for x in first.children if hasattr(x,"options")).options),25)
        self.assertEqual(first.max_page,1)
        nextpage=bot.CapitalsRosterView(111,extended,page=1)
        self.assertEqual(len(next(x for x in nextpage.children if hasattr(x,"options")).options),20)
        self.assertEqual(next(x for x in nextpage.children if hasattr(x,"options")).options[0].value,"25")
        self.assertTrue(any("TC" in x.description for x in next(x for x in nextpage.children if hasattr(x,"options")).options))

    def test_advanced_command_persists_and_picker_uses_current_source(self):
        names={x.name for x in bot.bot.tree.get_commands()}
        self.assertIn("advanced",names)
        self.assertIn("advance",names)
        self.assertIn("stats_menu",names)
        self.assertIn("TC players",bot.CapitalsRosterView(111,parse_roster(SNAPSHOT)).summary)

    def test_menu_opens_without_live_network(self):
        snapshot=last_verified_roster()
        self.assertEqual(len(snapshot),23)
        self.assertEqual(len([x for x in snapshot if x["status"]=="TC"]),5)
        menu=bot.CapitalsRosterView(123,snapshot)
        self.assertEqual(len(next(x for x in menu.children if hasattr(x,"options")).options),23)
        self.assertIn("Last verified roster",menu.summary)
        self.assertTrue(any("Refresh live roster" in getattr(x,"label","")
                            for x in menu.children))

    async def test_slash_autocomplete_includes_tc(self):
        names=await bot.autocomplete_capital_players(None,"savs")
        self.assertTrue(any("savsman99"==x.value for x in names))
        self.assertTrue(any("(TC)" in x.name for x in names))

    def test_reject_incorrect_team_or_missing_sections(self):
        with self.assertRaises(ValueError):
            parse_roster("Washington Capitals\nRoster")
        with self.assertRaises(ValueError):
            parse_roster(SNAPSHOT.replace("Washington Capitals","San Jose Sharks"))

if __name__=="__main__":unittest.main()
