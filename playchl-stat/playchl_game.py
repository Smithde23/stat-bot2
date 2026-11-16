"""Verified PlayCHL game-detail reader for direct Discord reporting.

The schedule anchor is a verified /games/<ID> URL. Score numbers are shown
in the order used by PlayCHL's "Goals" match-stat row; no home/away mapping is
invented when the page does not explicitly label its score order.
"""
import logging
import re
from urllib.parse import urlparse

log=logging.getLogger("playchl.game")
GAME_URL=re.compile(r"^https://playchl\.com/games/[A-Za-z0-9_-]+/?$")
TEAM_NAMES={
    "WSH":("Washington Capitals","Washington"),
    "ANA":("Anaheim Ducks","Anaheim"),
    "BUF":("Buffalo Sabres","Buffalo"),
    "CGY":("Calgary Flames","Calgary"),
    "DET":("Detroit Red Wings","Detroit"),
    "MIN":("Minnesota Wild","Minnesota"),
    "SEA":("Seattle Kraken","Seattle"),
    "SJS":("San Jose Sharks","San Jose"),
    "UTAH":("Utah Mammoth","Utah Hockey Club","Utah"),
    "VGK":("Vegas Golden Knights","Vegas"),
}
PLAYER_KEYS=("G","A","P","SH","HIT","TK","GV","BLK","PIM","SV","SA","GA","SV%")
PREFERRED_MATCH_KEYS=("Goals","Shots","Hits","Faceoffs%","TOA","PIM","Blocks","Takeaways","Giveaways","Interceptions")
def valid_game_url(url):
    return isinstance(url,str) and bool(GAME_URL.fullmatch(url))

def clean(text):
    return " ".join(str(text or "").split())

def team_in(heading,team):
    heading=clean(heading).casefold()
    return any(alias.casefold() in heading for alias in TEAM_NAMES.get(team,()))

def valid_number(text):
    return isinstance(text,str) and bool(re.fullmatch(r"-?\d+(?:\.\d+)?%?",text.strip()))

def parse_detail(payload,away,home):
    """Only display team-specific stats when a table explicitly names that club."""
    if away not in TEAM_NAMES or home not in TEAM_NAMES or away==home:
        raise ValueError("Invalid PlayCHL teams for a game")
    body=clean(payload.get("body",""))
    if not (team_in(body,away) and team_in(body,home)):
        raise ValueError("Game detail does not name both clubs")
    tables=payload.get("tables",[])
    matches={}
    match_order=None
    people=[]
    for table in tables:
        heading=clean(table.get("heading"))
        cols=[clean(x) for x in table.get("header",[])]
        rows=table.get("rows",[])
        if "match stats" in heading.casefold():
            if len(cols)>=3 and {cols[0],cols[2]}=={away,home}:
                match_order=(cols[0],cols[2])
            for row in rows:
                if len(row)>=3 and clean(row[1]) in PREFERRED_MATCH_KEYS:
                    label=clean(row[1])
                    values=(clean(row[0]),clean(row[2]))
                    if all(valid_number(v) for v in values):
                        matches[label]=values
        if not ("skaters" in heading.casefold() or "goalies" in heading.casefold()):
            continue
        candidates=[t for t in (away,home) if team_in(heading,t)]
        if len(candidates)!=1:continue
        side=candidates[0]
        if len(cols)<4 or not cols[0].casefold().startswith("player"):continue
        for row in rows:
            if len(row)<len(cols):continue
            cells=[clean(x) for x in row[-len(cols):]]
            player=re.sub(r"^\d+(?:\.\d+)?\s*","",cells[0])
            for alias in TEAM_NAMES.get(side,()):
                player=re.sub(r"^"+re.escape(alias)+r"\s*","",player,flags=re.I)
            player=re.sub(r"\s*(?:Xbox|PlayStation)\s*$","",player,flags=re.I).strip()
            if not player or len(player)>80:continue
            stat=dict(zip(cols[1:],cells[1:]))
            position=stat.get("Pos",stat.get("POS",stat.get("Position","")))
            is_goalie="goalies" in heading.casefold()
            if is_goalie and position.strip() in ("","—","-"):
                position="G"
            if position.upper() not in ("C","LW","RW","D","LD","RD","G"):
                continue
            numbers={k:stat[k] for k in PLAYER_KEYS if k in stat and valid_number(stat[k])}
            # A table needs at least a meaningful hockey statistic to
            # establish that this is a player box-score row.
            if not numbers:continue
            people.append({"team":side,"name":player,"position":position,"stats":numbers})
    if not matches and not people:
        raise ValueError("Game page exposes no recognized score or box-score tables")
    return {"match":matches,"players":people,"match_order":match_order}

def game_lines(row,detail):
    away=row.get("AWAY","");home=row.get("HOME","")
    text=[f"PLAYCHL GAME REPORT • {away} vs {home}",
          f"Date: {row.get('DATE') or 'Unavailable'} | {row.get('TIME') or 'Time unavailable'}",
          f"Status: {row.get('STATUS') or 'Unconfirmed'}",
          "Source: PlayCHL game-detail page (reported stats only)"]
    stats=detail.get("match") or {}
    order=detail.get("match_order")
    if order and "Goals" in stats:
        score=dict(zip(order,stats["Goals"]))
        text.append(f"FINAL SCORE: {away} {score[away]} — {home} {score[home]}")
    for name in PREFERRED_MATCH_KEYS:
        if name in stats:
            a,b=stats[name]
            text.append(f"{name}: {order[0]} {a} — {order[1]} {b}" if order
                        else f"{name}: {a} — {b} (PlayCHL display order)")
    players=detail.get("players") or []
    if players:
        for club in (away,home):
            team=[p for p in players if p["team"]==club]
            if not team:continue
            text.append("")
            text.append(f"{club} VERIFIED PLAYER STATS")
            for player in team[:18]:
                fields=[f"{k} {player['stats'][k]}" for k in PLAYER_KEYS if k in player["stats"]]
                text.append(f"{player['name']} ({player['position']}) • "+", ".join(fields))
    else:
        text.append("Individual player statistics were not verified on this page.")
    return text

def fallback_lines(row):
    return [
        f"PLAYCHL GAME • {row.get('AWAY','?')} vs {row.get('HOME','?')}",
        f"Date: {row.get('DATE','Unavailable')} | {row.get('TIME','')}",
        f"Status: {row.get('STATUS') or 'Unconfirmed'}",
        "PlayCHL did not expose a readable, verified player box score for this selection.",
        "No goals, assists, saves or scores have been estimated.",
    ]

async def fetch_game_detail(browser,row):
    url=row.get("GAME_URL","")
    if not valid_game_url(url):
        raise ValueError("Only verified PlayCHL game detail URLs are supported")
    page=await browser.new_page()
    try:
        response=await page.goto(url,wait_until="domcontentloaded",timeout=45000)
        if response is None or response.status>=400:
            raise RuntimeError("PlayCHL game page unavailable")
        # Client-rendered game details may populate well after DOM content.
        # Give the published box-score tables time to appear.
        try:
            await page.locator("table tbody tr").first.wait_for(state="attached",timeout=16000)
        except Exception:
            await page.wait_for_timeout(1500)
        payload=await page.evaluate("""() => {
          const tables=[...document.querySelectorAll('table')].map(t=>{
            // PlayCHL's Nov 2026 game pages use ordinary text labels
            // immediately before tables, NOT h1-h6 elements:
            // e.g. "Calgary Flames SkatersPlayerPos..." in the wrapper.
            // Identify the section from the closest table wrapper.
            const previous=(t.previousElementSibling?.textContent||'').trim();
            const container=(t.parentElement?.parentElement?.textContent||'').trim();
            const prefix=container.slice(0,180);
            const label=prefix.match(/(?:Washington Capitals|Calgary Flames|Minnesota Wild|Anaheim Ducks|Buffalo Sabres|Detroit Red Wings|Seattle Kraken|San Jose Sharks|Utah Mammoth|Utah Hockey Club|Vegas Golden Knights)\\s*(?:Skaters|Goalies)/i);
            const heading=label?label[0]:(/Match Stats/i.test(previous+prefix)?"Match Stats":previous);
            const tr=[...t.querySelectorAll('tr')];
            let header=[...t.querySelectorAll('thead th')].map(n=>n.innerText.trim());
            if(!header.length&&tr.length)header=[...tr[0].querySelectorAll('th')].map(n=>n.innerText.trim());
            const rows=tr.slice(1).map(r=>[...r.querySelectorAll('th,td')].map(n=>n.innerText.trim()));
            return {heading,header,rows};
          });
          return {body:document.body.innerText.slice(0,150000),tables};
        }""")
        log.info("PlayCHL game page %s parsed DOM: %s tables, headings=%r, body_head=%r",
                 url,len(payload.get("tables",[])),
                 [t.get("heading") for t in payload.get("tables",[])][:10],
                 str(payload.get("body",""))[:200])
        return parse_detail(payload,row.get("AWAY"),row.get("HOME"))
    finally:
        await page.close()
