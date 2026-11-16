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
        if match:
            previous=result.get(match["id"])
            # The same match may appear twice with different detail levels.
            # Keep the richer verified record, never overwrite it with a stub.
            richness=lambda m:len(m["players"])*2+int(m["own_goals"] is not None)+int(m["opponent_goals"] is not None)
            if previous is None or richness(match)>richness(previous):
                result[match["id"]]=match
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


def aggregate_players(matches):
    """Aggregate only the deduplicated private-match records returned by EA.

    Key by both EA player ID and gamertag to avoid silently merging different
    identities. Missing fields remain missing, never interpreted as zeros.
    """
    buckets={}
    for match in matches:
        matchid=match.get("id")
        if not matchid:continue
        seen=set()
        for p in match.get("players",[]):
            identity=(str(p.get("id") or ""),str(p.get("name") or "").strip().casefold())
            if not identity[1] or identity in seen:continue
            seen.add(identity)
            if identity not in buckets:
                buckets[identity]={
                    "id":identity[0],"name":p["name"],"matches":0,
                    "goals":None,"assists":None,"shots":None,"hits":None,
                    "blocks":None,"takeaways":None,"giveaways":None,
                    "saves":None,"shots_against":None,"latest_match":matchid
                }
            result=buckets[identity]
            result["matches"]+=1
            for stat in ("goals","assists","shots","hits","blocks","takeaways",
                         "giveaways","saves","shots_against"):
                value=p.get(stat)
                if value is not None:
                    result[stat]=(result[stat] or 0)+value
    return sorted(buckets.values(),key=lambda p:(-(p["goals"] or 0)-(p["assists"] or 0),
                                                -p["matches"],p["name"].casefold()))

def player_summary(matches,name):
    """Return one precisely matched gamertag; reject ambiguous EA identities."""
    query=str(name).strip().casefold()
    if not query:return None
    entries=[p for p in aggregate_players(matches) if p["name"].casefold()==query]
    if len(entries)!=1:return None
    return entries[0]

def summary_lines(p):
    if not p:return []
    stats=[]
    for label,key in (("G","goals"),("A","assists"),("SH","shots"),
                      ("HIT","hits"),("BLK","blocks"),("TK","takeaways"),
                      ("GV","giveaways"),("SV","saves"),("SA","shots_against")):
        if p.get(key) is not None:stats.append(f"{label} {p[key]}")
    return [
        f"EA PRIVATE MATCHES • CLUB {CLUB_ID} • {p['matches']} recorded matches in fetched history",
        " | ".join(stats) if stats else "No numeric player statistics exposed by EA",
        "NOT added to PlayCHL season totals; fetched history may be incomplete."
    ]

def ranking_lines(matches,metric="points",limit=10):
    if metric not in ("points","goals","assists","shots","saves"):
        raise ValueError("Unsupported EA ranking metric")
    scored=[]
    for p in aggregate_players(matches):
        if metric=="points":
            if p["goals"] is None or p["assists"] is None:continue
            score=p["goals"]+p["assists"]
        else:
            score=p[metric]
            if score is None:continue
        scored.append((score,p))
    scored.sort(key=lambda item:(-item[0],-item[1]["matches"],item[1]["name"].casefold()))
    lines=[f"EA CLUB-PRIVATE {metric.upper()} LEADERS • CLUB {CLUB_ID}",
           "Verified EA history only; NOT official PlayCHL league standings or awards."]
    if not scored:return lines+["No players with verified data for this category."]
    for rank,(value,p) in enumerate(scored[:limit],1):
        lines.append(f"#{rank} {p['name']} — {value} {metric} across {p['matches']} listed match(es)")
    return lines

def game_report_lines(matches,match_id):
    match_id=str(match_id).strip()
    found=[x for x in matches if x["id"]==match_id]
    if len(found)!=1:return ["EA match ID not found in fetched club-private history."]
    g=found[0]
    score=(f"{g['own_goals']}–{g['opponent_goals']}" if
           g["own_goals"] is not None and g["opponent_goals"] is not None
           else "score not available")
    date=(dt.datetime.fromtimestamp(g["timestamp"],dt.timezone.utc).strftime("%b %d, %Y %H:%M UTC")
          if g.get("timestamp") else "date not available")
    lines=[f"EA PRIVATE-MATCH REPORT • {g['id']}",
           f"{g['club']} vs {g['opponent']} • {score} • {date}",
           "Supplementary EA source; no league-game match is presumed."]
    if not g["players"]:return lines+["Player statistics not available for this match."]
    for p in g["players"][:20]:
        stat=[]
        for title,key in (("G","goals"),("A","assists"),("SH","shots"),("SV","saves"),
                          ("HIT","hits"),("BLK","blocks")):
            if p[key] is not None:stat.append(f"{title} {p[key]}")
        lines.append(p["name"]+" — "+(", ".join(stat) if stat else "stats unavailable"))
    return lines
