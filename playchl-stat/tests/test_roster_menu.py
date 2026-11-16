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

    def test_only_one_advanced_command_and_picker_uses_current_source(self):
        commands=bot.bot.tree.get_commands()
        names=[x.name for x in commands]
        self.assertNotIn("advanced",names)
        self.assertIn("advance",names)
        self.assertIn("stats_menu",names)
        self.assertEqual(len(names),len(set(names)), "Duplicate slash commands")
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

    def test_native_command_choices_include_tc(self):
        self.assertTrue(any(x.value=="savsman99" for x in bot._ADVANCE_PLAYER_CHOICES))
        self.assertTrue(any("(TC)" in x.name for x in bot._ADVANCE_PLAYER_CHOICES))

    def test_advance_has_static_native_choices_not_flashing_autocomplete(self):
        command=next(x for x in bot.bot.tree.get_commands() if x.name=="advance")
        self.assertIn("player",command._params)
        self.assertFalse(command._params["player"].required)
        self.assertIsNone(command._params["player"].autocomplete)
        choices=command._params["player"].choices
        self.assertEqual(len(choices),23)
        self.assertEqual(len({x.value for x in choices}),23)
        self.assertTrue(any(x.value=="savsman99" and "(TC)" in x.name for x in choices))
        self.assertTrue(any(x.value=="Wristshot_34" for x in choices))
        self.assertIn("TC",bot.CapitalsRosterView(111,parse_roster(SNAPSHOT)).summary)

    def test_reject_incorrect_team_or_missing_sections(self):
        with self.assertRaises(ValueError):
            parse_roster("Washington Capitals\nRoster")
        with self.assertRaises(ValueError):
            parse_roster(SNAPSHOT.replace("Washington Capitals","San Jose Sharks"))

if __name__=="__main__":unittest.main()
