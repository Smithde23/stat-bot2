"""Current PlayCHL Washington Capitals roster, including TC players.

Uses the actual CHL team profile, not the NHL's Washington Capitals roster.
The profile contains separate SKATERS, GOALIES and TC PLAYERS sections.
"""
import re

TEAM_PROFILE="https://playchl.com/teams/6ac010959de0f7b0124df65f"
# Snapshot read from the official team profile on 2026-11-15.
# Used only when the live profile cannot be loaded. The bot labels it clearly.
LAST_VERIFIED_REGULAR=(
    "Wristshot_34","CrazyJHawk91","softnuts4u","Reqhzii",
    "Leafsfan349054","LEAN BEEEF","Buckley0306","XXGhxst-_-XX",
    "YESHUA_RETURNS","KASKADE_420","Masterpip16","V fonkas V",
    "WWuSamm","swatman27","D_is_key","Charlielucia13",
    "Wwusamm","Buckkey0306"
)
LAST_VERIFIED_TC=("CH3LK1NG","savsman99","Bad at NHL","BusBeanWPG","foodude56")

def last_verified_roster():
    return ([{"name":name,"status":"Roster","position":"","stats":{},"snapshot":True}
            for name in LAST_VERIFIED_REGULAR]
            +[{"name":name,"status":"TC","position":"","stats":{},"snapshot":True}
              for name in LAST_VERIFIED_TC])


def _lines(text):
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]

def _section(lines,heading,endings):
    indexes=[i for i,x in enumerate(lines) if x.casefold()==heading.casefold()]
    if not indexes:return []
    start=indexes[-1]+1
    end=next((i for i in range(start,len(lines))
              if lines[i].casefold() in [x.casefold() for x in endings]),len(lines))
    return lines[start:end]

def _player_rows(lines,kind):
    """Each published player card ends with a Hit N field after its GP/G/A block."""
    records=[]
    buffer=[]
    for line in lines:
        buffer.append(line)
        if not re.fullmatch(r"Hit\s+\d+",line,re.I):continue
        if not buffer:continue
        name=buffer[0].strip()
        if not name or name.casefold() in ("no skaters yet.","no goalies yet."):
            buffer=[];continue
        stats={}
        for part in buffer[1:]:
            m=re.fullmatch(r"(GP|G|A|Pts|Sh|Hit)\s+(\d+)",part,re.I)
            if m:
                stats[m.group(1).upper()]=m.group(2)
        position=next((x for x in buffer[1:5] if
                        re.fullmatch(r"(?:C|LW|RW|LD|RD|D|G)(?:\s*/\s*(?:C|LW|RW|LD|RD|D|G|None))*",x,re.I)),"")
        records.append({"name":name,"status":kind,"position":position,"stats":stats})
        buffer=[]
    # The goalies may expose separate stat cards rather than the skater GP-Hit
    # format; they are only included if their names can be safely identified.
    return records

def parse_roster(body):
    lines=_lines(body)
    if not any(x=="Washington Capitals" for x in lines):
        raise ValueError("Not a verified PlayCHL Washington Capitals profile")
    if "ROSTER" not in lines or "TC PLAYERS" not in lines:
        raise ValueError("PlayCHL did not expose roster and TC sections")
    idx=lines.index("ROSTER")
    skaters=_section(lines[idx:],"SKATERS",["GOALIES","TC PLAYERS"])
    goalies=_section(lines[idx:],"GOALIES",["TC PLAYERS"])
    tc=_section(lines[idx:],"TC PLAYERS",["Continental Hockey League"])
    roster=_player_rows(skaters,"Roster")+_player_rows(goalies,"Roster")
    for i,x in enumerate(tc):
        if x=="(TC)" and i>0:
            name=tc[i-1].strip()
            if name and len(name)<=80:
                roster.append({"name":name,"status":"TC","position":"","stats":{}})
    if not roster:
        raise ValueError("Roster section appeared but no player cards were parsed")
    # Preserve case-sensitive names and statuses from the roster. Some players
    # have similar gamertags, and casefold deduplication could conflate them.
    unique=[]
    seen=set()
    for p in roster:
        key=(p["name"],p["status"])
        if key not in seen:
            unique.append(p);seen.add(key)
    return unique

async def fetch_roster(browser):
    page=await browser.new_page()
    try:
        response=await page.goto(TEAM_PROFILE,wait_until="domcontentloaded",timeout=45000)
        if not response or response.status>=400:
            raise RuntimeError("PlayCHL Washington team profile unavailable")
        # The team profile is client-rendered.
        try:
            await page.get_by_text("TC PLAYERS",exact=True).first.wait_for(timeout=12000)
        except Exception:
            # Some builds render TC as nested plain text, not a standalone
            # element. The parser below validates both roster sections.
            pass
        text=await page.locator("body").inner_text()
        return parse_roster(text)
    finally:
        await page.close()
