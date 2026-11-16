"""Read EA NHL private-club match history as a supplementary data source.

EA's endpoint is undocumented and may be empty, rate-limited, or change schema.
Never blend private-club matches into PlayCHL's published league aggregates.
"""
import asyncio
import datetime as dt
import logging
import os
import time
from urllib.parse import urlencode
from playwright.async_api import async_playwright

log=logging.getLogger("playchl.ea")
BASE_URL="https://proclubs.ea.com/api/nhl/clubs/matches"
PLATFORM="common-gen5"
CLUB_ID="1940"
MATCH_TYPE="club_private"
TTL=300
_state={"at":0.0,"data":None}
_lock=asyncio.Lock()

def endpoint():
    return BASE_URL+"?"+urlencode({
        "platform":PLATFORM,"clubIds":CLUB_ID,"matchType":MATCH_TYPE
    })

def integer(value):
    if value is None or isinstance(value,bool):return None
    try:
        val=int(float(str(value).strip()))
        return val if val>=0 else None
    except (ValueError,TypeError,OverflowError):return None

def games_in(response):
    if isinstance(response,list):return response
    if isinstance(response,dict):
        for key in ("matches","data","results"):
            found=response.get(key)
            if isinstance(found,list):return found
        # Some responses group records by club ID.
        found=response.get(CLUB_ID)
        if isinstance(found,list):return found
    raise ValueError("EA returned an unrecognized match-history format")

def club_name(blob,fallback):
    if not isinstance(blob,dict):return fallback
    detail=blob.get("details")
    if isinstance(detail,dict) and detail.get("name"):
        return str(detail["name"])[:70]
    return str(blob.get("name") or fallback)[:70]

def normalize_game(item):
    if not isinstance(item,dict):return None
    clubs=item.get("clubs")
    players=item.get("players")
    if not isinstance(clubs,dict) or CLUB_ID not in clubs:return None
    if not isinstance(players,dict):players={}
    club=clubs[CLUB_ID]
    if not isinstance(club,dict):return None
    matchid=str(item.get("matchId") or "").strip()
    if not matchid or len(matchid)>150:return None
    other=str(club.get("opponentClubId") or "").strip()
    opponent=clubs.get(other) if other else None
    if other==CLUB_ID or (other and other not in clubs):
        # Do not infer another team's score or name from partial records.
        opponent=None
    if not other:
        rivals=[k for k in clubs if str(k)!=CLUB_ID]
        if len(rivals)==1:
            other=str(rivals[0]);opponent=clubs[other]
    when=integer(item.get("timestamp"))
    if when and when>10_000_000_000:when//=1000
    if when and not (1_600_000_000<when<4_000_000_000):when=None
    own_goals=integer(club.get("goals"))
    opponent_goals=integer(opponent.get("goals")) if isinstance(opponent,dict) else None
    sides=players.get(CLUB_ID,{})
    records=[]
    if isinstance(sides,dict):
        for playerid,stat in sides.items():
            if not isinstance(stat,dict):continue
            name=str(stat.get("playername") or "").strip()
            if not name or len(name)>75:continue
            record={
                "id":str(playerid),"name":name,
                "position":str(stat.get("posSorted") or stat.get("position") or ""),
                "goals":integer(stat.get("skgoals")),
                "assists":integer(stat.get("skassists")),
                "shots":integer(stat.get("skshots")),
                "hits":integer(stat.get("skhits")),
                "blocks":integer(stat.get("skbs")),
                "takeaways":integer(stat.get("sktakeaways")),
                "giveaways":integer(stat.get("skgiveaways")),
                "saves":integer(stat.get("glsaves")),
                "shots_against":integer(stat.get("glshots")),
            }
            records.append(record)
    records.sort(key=lambda x:(x["goals"] or 0)+(x["assists"] or 0),reverse=True)
    return {
        "id":matchid,"timestamp":when,
        "club":club_name(club,"Washington club 1940"),
        "opponent":club_name(opponent,"Opponent club "+other if other else "Unknown opponent"),
        "own_goals":own_goals,"opponent_goals":opponent_goals,
        "players":records,
    }

def normalize(response):
    result={}
    for item in games_in(response):
        match=normalize_game(item)
        if match:result[match["id"]]=match
    return sorted(result.values(),key=lambda x:(x["timestamp"] or 0,x["id"]),reverse=True)

async def fetch_matches(force=False):
    """Cache successful response briefly; return empty list for valid empty history."""
    async with _lock:
        if not force and _state["data"] is not None and time.monotonic()-_state["at"]<TTL:
            return _state["data"]
        async with async_playwright() as p:
            request=await p.request.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36",
                extra_http_headers={
                    "Accept":"application/json, text/plain, */*",
                    "Accept-Language":"en-US,en;q=0.9",
                    "Referer":"https://www.ea.com/",
                    "Origin":"https://www.ea.com",
                },
                timeout=17000,
            )
            try:
                resp=await request.get(endpoint(),timeout=17000)
                if resp.status!=200:
                    raise RuntimeError("EA club-private endpoint returned HTTP "+str(resp.status))
                raw=await resp.json()
            finally:
                await request.dispose()
        data=normalize(raw)
        _state.update(at=time.monotonic(),data=data)
        log.info("EA supplemental match source: %d deduplicated club %s matches",len(data),CLUB_ID)
        return data

def recent_lines(matches,limit=10):
    lines=[f"EA CLUB-PRIVATE MATCH HISTORY • CLUB {CLUB_ID} • {PLATFORM}",
           "Source: "+endpoint(),"Private matches are supplementary, NOT PlayCHL league standings."]
    if not matches:
        return lines+["EA returned no recorded private matches for this club."]
    for match in matches[:limit]:
        if match["timestamp"]:
            when=dt.datetime.fromtimestamp(match["timestamp"],dt.timezone.utc).strftime("%b %d %Y")
        else:when="date unavailable"
        score=(str(match["own_goals"])+"-"+str(match["opponent_goals"])
               if match["own_goals"] is not None and match["opponent_goals"] is not None
               else "score unavailable")
        lines.append(f"{when} • {match['club']} vs {match['opponent']} • {score} • Match {match['id']}")
        leaders=[x for x in match["players"] if x["goals"] is not None or x["assists"] is not None]
        for p in leaders[:3]:
            goals=str(p["goals"]) if p["goals"] is not None else "?"
            assists=str(p["assists"]) if p["assists"] is not None else "?"
            lines.append(f"   {p['name']}: {goals} G, {assists} A")
    return lines

def player_lines(matches,query,limit=8):
    query=query.strip().casefold()
    if len(query)<2:raise ValueError("Enter at least two characters of the player's name.")
    found=[]
    for match in matches:
        for p in match["players"]:
            if query in p["name"].casefold():
                found.append((match,p))
    lines=["EA PRIVATE-MATCH PLAYER STATS • CLUB 1940",
           "These are individual EA match records, NOT PlayCHL season totals."]
    if not found:return lines+["No matching player found in EA's available private-match history."]
    for match,p in found[:limit]:
        fields=[]
        for label,key in (("G","goals"),("A","assists"),("SH","shots"),("HIT","hits"),
                          ("BLK","blocks"),("TK","takeaways"),("SV","saves")):
            if p[key] is not None:fields.append(f"{label} {p[key]}")
        lines.append(p["name"]+" • Match "+match["id"]+" • "+", ".join(fields))
    return lines
