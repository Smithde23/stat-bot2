import os, asyncio, logging, re, io
from datetime import datetime, timezone, date, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from dotenv import load_dotenv
import discord
from discord import app_commands
from discord.ext import commands, tasks
from playwright.async_api import async_playwright
from PIL import Image, ImageDraw, ImageFont

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN', '')
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '0'))
GUILD_ID = int(os.getenv('GUILD_ID', '0'))
BASE = 'https://playchl.com'
EASTERN = ZoneInfo('America/New_York')
SCHEDULE_HOUR_ET = 9
logging.basicConfig(level=logging.INFO)
log = logging.getLogger('playchl')

# Keep scheduled jobs and slash commands from launching Chromium simultaneously.
BROWSER_LOCK = asyncio.Lock()

async def page_tables(path, tab=None):
    """Read visible HTML tables, with only one Chromium instance at a time."""
    async with BROWSER_LOCK:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--disable-dev-shm-usage'])
            try:
                page = await browser.new_page()
                response = await page.goto(BASE + path, wait_until='domcontentloaded', timeout=45000)
                if not response or response.status >= 400:
                    raise RuntimeError(f'Website returned {response.status if response else "no response"}')
                if tab:
                    control = page.get_by_role('button', name=tab, exact=True)
                    if await control.count() == 0:
                        control = page.get_by_text(tab, exact=True)
                    await control.first.click(timeout=12000)
                try:
                    await page.locator('table tbody tr').first.wait_for(timeout=12000)
                except Exception:
                    log.warning('No table rows appeared at %s (tab=%s)', path, tab)
                return await page.locator('table').evaluate_all("""els => els.map(t => ({
                    headers: [...t.querySelectorAll('thead th')].map(e => e.innerText.trim()),
                    rows: [...t.querySelectorAll('tbody tr')].map(tr =>
                        [...tr.querySelectorAll('th,td')].map(e => e.innerText.trim()))
                }))""")
            finally:
                await browser.close()

def pick_tables(tables, required):
    found = []
    for t in tables:
        hdr = [x.upper().strip() for x in t['headers']]
        if all(any(x == term for x in hdr) for term in required):
            found.append((hdr, t['rows']))
    return found

async def standings():
    tables = pick_tables(await page_tables('/standings'), ['GP','W','PTS'])
    if not tables:
        raise RuntimeError('No standings HTML table detected. The site may use div-based grids; inspect the page and update the extractor.')
    results = []
    for headers, rows in tables:
        for cells in rows:
            if len(cells) < len(headers): continue
            d = dict(zip(headers, cells))
            team = d.get('TEAM','').strip()
            if team:
                results.append(d)
    if not results: raise RuntimeError('Standings table contained no readable teams')
    return results

# Conference assignments visible on the CHL standings page (Nov 2026).
# Update these if the league realigns its teams.
CONFERENCES = {
    'Bowman Conference': ('Buffalo Sabres', 'Anaheim Ducks', 'Seattle Kraken',
                          'Detroit Red Wings', 'Calgary Flames'),
    'Sather Conference': ('San Jose Sharks', 'Utah Mammoth', 'Minnesota Wild',
                          'Washington Capitals', 'Vegas Golden Knights'),
}
TEAM_ABBREVIATIONS = {
    'Buffalo Sabres':'BUF', 'Anaheim Ducks':'ANA', 'Seattle Kraken':'SEA',
    'Detroit Red Wings':'DET', 'Calgary Flames':'CGY', 'San Jose Sharks':'SJS',
    'Utah Mammoth':'UTAH', 'Minnesota Wild':'MIN',
    'Washington Capitals':'WSH', 'Vegas Golden Knights':'VGK',
}

def conference_for(team):
    clean = re.sub(r'\s+', ' ', team).strip().casefold()
    for conf, teams in CONFERENCES.items():
        for full in teams:
            if clean in (full.casefold(), TEAM_ABBREVIATIONS[full].casefold()):
                return conf
    return None

def stat_int(row, key):
    try:
        return int(re.sub(r'[^0-9-]', '', str(value(row, key))) or '0')
    except ValueError:
        return 0

def grouped_standings(rows):
    grouped = {name: [] for name in CONFERENCES}
    unknown = []
    for row in rows:
        conf = conference_for(value(row, 'TEAM'))
        if conf:
            grouped[conf].append(row)
        else:
            unknown.append(value(row, 'TEAM'))
    if unknown:
        raise RuntimeError('Unknown CHL teams; update conference mapping: ' + ', '.join(unknown))
    for teams in grouped.values():
        teams.sort(key=lambda r: (stat_int(r, 'PTS'), stat_int(r, 'W'),
                                  stat_int(r, 'DIFF')), reverse=True)
    return grouped

def standing_lines(rows):
    output = []
    for conf, teams in grouped_standings(rows).items():
        output.append('=== ' + conf.upper() + ' ===')
        output.append('RK TEAM                 GP  W  L OTL PTS DIFF')
        for rank, r in enumerate(teams, 1):
            team = value(r, 'TEAM')[:20]
            output.append(f"{rank:>2} {team:<20} {value(r,'GP'):>2} {value(r,'W'):>2} {value(r,'L'):>2} {value(r,'OTL'):>3} {value(r,'PTS'):>3} {value(r,'DIFF'):>4}")
        output.append('')
    return output

async def skaters():
    tables = pick_tables(await page_tables('/statistics', 'Skaters'), ['GP','G','A'])
    if not tables: raise RuntimeError('No skater statistics table detected; page layout may have changed.')
    results=[]
    for headers, rows in tables:
        for cells in rows:
            if len(cells) < len(headers): continue
            d=dict(zip(headers,cells))
            if d.get('PLAYER'): results.append(d)
    if not results: raise RuntimeError('No readable player statistics')
    return results

def value(d, *keys):
    for k in keys:
        if d.get(k) is not None: return d[k]
    return '-'

def chunks(lines, limit=1700):
    out=[]; current=''
    for line in lines:
        if len(current)+len(line)+1 > limit and current:
            out.append(current); current=''
        current+=line+'\n'
    if current: out.append(current)
    return out[:8]

class CHLBot(commands.Bot):
    async def setup_hook(self):
        if GUILD_ID:
            guild=discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        auto_update.start()
        daily_schedule.start()
        daily_awards.start()

bot=CHLBot(command_prefix='!', intents=discord.Intents.default())

async def reply(interaction, lines):
    parts=chunks(lines)
    for i, part in enumerate(parts):
        await interaction.followup.send('```\n'+part+'```', ephemeral=False)

@bot.tree.command(name='standings',description='Show standings for every CHL team')
async def standings_command(interaction:discord.Interaction):
    await interaction.response.defer(thinking=True)
    try:
        rows=await standings()
        await reply(interaction, standing_lines(rows))
    except Exception as e: await interaction.followup.send(f'Unable to read live standings: {e}',ephemeral=True)

@bot.tree.command(name='team',description='Show one CHL team by abbreviation, e.g. WSH')
@app_commands.describe(abbreviation='Team abbreviation such as WSH, BUF, SJS')
async def team_command(interaction:discord.Interaction,abbreviation:str):
    await interaction.response.defer(thinking=True)
    try:
        rows=await standings()
        matches=[r for r in rows if value(r,'TEAM').casefold()==abbreviation.strip().casefold()]
        if not matches: return await interaction.followup.send('Team not found in the current standings.',ephemeral=True)
        r=matches[0]
        await reply(interaction,[f'{k}: {v}' for k,v in r.items()])
    except Exception as e: await interaction.followup.send(f'Unable to read team: {e}',ephemeral=True)

async def goalies():
    tables = pick_tables(await page_tables('/statistics', 'Goalies'), ['GP', 'W', 'SV%', 'GAA'])
    if not tables:
        raise RuntimeError('Goalie table not found after switching to Goalies tab')
    results=[]
    for headers, rows in tables:
        for cells in rows:
            if len(cells) < len(headers):
                continue
            item=dict(zip(headers,cells))
            if item.get('PLAYER'):
                results.append(item)
    if not results:
        raise RuntimeError('Goalie table has no readable rows')
    return results

def number(item, field):
    try:
        return float(re.sub(r'[^0-9.\-]', '', str(value(item,field))) or '0')
    except ValueError:
        return 0.0

@app_commands.choices(category=[
    app_commands.Choice(name='All skaters',value='skaters'),
    app_commands.Choice(name='Forwards',value='forwards'),
    app_commands.Choice(name='Defensemen',value='defense'),
    app_commands.Choice(name='Goalies',value='goalies'),
    app_commands.Choice(name='All available leaders',value='all'),
])
@bot.tree.command(name='leaders',description='CHL leaders by position, including goalies')
@app_commands.describe(category='Select a position group')
async def leaders_command(interaction:discord.Interaction, category:app_commands.Choice[str]):
    await interaction.response.defer(thinking=True)
    try:
        selected=category.value
        output=[]
        if selected in ('skaters','forwards','defense','all'):
            rows=await skaters()
            if selected in ('forwards','defense'):
                # Do not invent positions if the source lacks a POS field.
                if not any(value(r,'POS','POSITION') != '-' for r in rows):
                    return await interaction.followup.send(
                        'PlayCHL skater statistics do not expose player positions in the current table. '
                        'I cannot reliably separate forwards from defensemen yet. '
                        'Use `/leaders category:All skaters` or select Goalies.',ephemeral=True)
                allowed={'C','LW','RW','F'} if selected=='forwards' else {'D','LD','RD'}
                rows=[r for r in rows if value(r,'POS','POSITION').upper() in allowed]
            rows.sort(key=lambda r:number(r,'P') if value(r,'P')!='-' else number(r,'PTS'),reverse=True)
            output.append('CHL SKATER LEADERS' if selected in ('skaters','all') else 'CHL '+selected.upper()+' LEADERS')
            output.extend(f"{value(r,'PLAYER')[:24]:24} {value(r,'TEAM'):5} G {value(r,'G'):>3} A {value(r,'A'):>3} P {value(r,'P','PTS'):>3}" for r in rows[:20])
        if selected in ('goalies','all'):
            rows=await goalies()
            rows.sort(key=lambda r:number(r,'W'),reverse=True)
            output.append('CHL GOALIE LEADERS - WINS')
            output.extend(f"{value(r,'PLAYER')[:22]:22} {value(r,'TEAM'):5} W {value(r,'W'):>3} SV% {value(r,'SV%'):>6} GAA {value(r,'GAA'):>5} SO {value(r,'SO'):>2}" for r in rows[:20])
        await reply(interaction,output)
    except Exception as e:
        log.exception('Leader command failed')
        await interaction.followup.send(f'Unable to read leaders: {e}',ephemeral=True)

@bot.tree.command(name='player',description='Search visible CHL skater stats by player name')
@app_commands.describe(name='Player name or part of name')
async def player_command(interaction:discord.Interaction,name:str):
    await interaction.response.defer(thinking=True)
    try:
        rows=await skaters()
        matches=[r for r in rows if name.casefold() in value(r,'PLAYER').casefold()]
        if not matches: return await interaction.followup.send('No matching player in the currently displayed skater table.',ephemeral=True)
        await reply(interaction,[f"{value(r,'PLAYER')} ({value(r,'TEAM')}) GP {value(r,'GP')} G {value(r,'G')} A {value(r,'A')} P {value(r,'P','PTS')}" for r in matches[:25]])
    except Exception as e: await interaction.followup.send(f'Unable to read player stats: {e}',ephemeral=True)

# Schedule reader: PlayCHL displays games as two-column cards, not HTML tables.
TEAM_ALIASES = {
    'BUF': ['BUF', 'Buffalo Sabres', 'Buffalo'],
    'ANA': ['ANA', 'Anaheim Ducks', 'Anaheim'],
    'SEA': ['SEA', 'Seattle Kraken', 'Seattle'],
    'DET': ['DET', 'Detroit Red Wings', 'Detroit'],
    'CGY': ['CGY', 'Calgary Flames', 'Calgary'],
    'SJS': ['SJS', 'San Jose Sharks', 'San Jose'],
    'UTAH': ['UTAH', 'Utah Mammoth', 'Utah'],
    'MIN': ['MIN', 'Minnesota Wild', 'Minnesota'],
    'WSH': ['WSH', 'Washington Capitals', 'Washington'],
    'VGK': ['VGK', 'Vegas Golden Knights', 'Vegas'],
}

def resolve_team(team):
    text = team.strip().casefold()
    for abbr, aliases in TEAM_ALIASES.items():
        if text in [a.casefold() for a in aliases]:
            return abbr
    raise ValueError('Unknown team. Use BUF, ANA, SEA, DET, CGY, SJS, UTAH, MIN, WSH, or VGK.')

def match_team(cell, team):
    text = re.sub(r'\s+', ' ', str(cell)).strip().casefold()
    aliases = TEAM_ALIASES[team]
    return any(text == alias.casefold() or text.startswith(alias.casefold() + ' ') or text.endswith(' ' + alias.casefold()) for alias in aliases)

def first_key(row, *candidates):
    for key in candidates:
        if row.get(key, '').strip():
            return row[key].strip()
    return ''

def schedule_table_rows(tables):
    output = []
    for table in tables:
        headers = [re.sub(r'\s+', ' ', h).strip().upper() for h in table['headers']]
        has_date = any(h in ('DATE', 'GAME DATE', 'DAY', 'WHEN') for h in headers)
        has_matchup = (any(h in ('HOME', 'HOME TEAM') for h in headers) and any(h in ('AWAY', 'AWAY TEAM', 'VISITOR', 'VISITING TEAM') for h in headers)) or any(h in ('MATCHUP', 'GAME', 'TEAMS') for h in headers)
        if not (has_date and has_matchup):
            continue
        for cells in table['rows']:
            if len(cells) >= len(headers):
                item = dict(zip(headers, cells))
                if any(str(v).strip() for v in item.values()):
                    output.append(item)
    if not output:
        raise RuntimeError('Could not identify a schedule table on PlayCHL. The page may use cards or a different layout. Please send a screenshot of the schedule page so the reader can be adapted.')
    return output

async def schedule_rows():
    """Extract games from the actual PlayCHL /games/ links in the schedule."""
    async with BROWSER_LOCK:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--disable-dev-shm-usage'])
            try:
                page = await browser.new_page()
                response = await page.goto(BASE + '/schedule', wait_until='domcontentloaded', timeout=45000)
                if not response or response.status >= 400:
                    raise RuntimeError('PlayCHL schedule page could not be loaded')
                selector = 'a[href^="/games/"]'
                try:
                    await page.locator(selector).first.wait_for(state='attached', timeout=25000)
                except Exception as exc:
                    log.error('Schedule links missing: url=%s title=%s', page.url, await page.title())
                    raise RuntimeError('No /games/ links loaded on the schedule page within 25 seconds') from exc
                # Every game is represented by one anchor, so this avoids
                # accidentally merging adjacent games in the two-column layout.
                cards = await page.locator(selector).evaluate_all(
                    "els => els.map(el => ({href: el.getAttribute('href'), text: el.innerText || ''}))"
                )
                log.info('Schedule reader found %d game links', len(cards))
                output = []
                for card in cards:
                    parsed = parse_schedule_card(card['text'])
                    if parsed:
                        parsed['GAME_URL'] = BASE + card['href']
                        output.append(parsed)
                if not output:
                    log.error('Schedule parse diagnostics: count=%s samples=%r', len(cards), cards[:3])
                    raise RuntimeError('Game links loaded but none could be parsed; inspect Railway schedule diagnostics')
                log.info('Schedule reader parsed %d games', len(output))
                return output
            finally:
                await browser.close()


def parse_schedule_card(text):
    """Read a game link's date, time, two team codes and status."""
    lines = [re.sub(r'\s+', ' ', line).strip() for line in text.splitlines() if line.strip()]
    whole = ' '.join(lines)
    date_match = re.search(r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})\b', whole, re.I)
    if not date_match:
        return None
    time_match = re.search(r'\b(\d{1,2}:\d{2}\s*[AP]M)\b', whole, re.I)
    codes = r'BUF|ANA|SEA|DET|CGY|SJS|UTAH|MIN|WSH|VGK'
    # Team names can be separate DOM nodes from their AWAY/HOME labels.
    # Identify both team codes in visual order, rather than requiring adjacency.
    team_hits = list(re.finditer(r'(?<![A-Za-z])(?:' + codes + r')(?![A-Za-z])', whole, re.I))
    if len(team_hits) < 2:
        return None
    away, home = team_hits[0].group().upper(), team_hits[1].group().upper()
    if away == home:
        return None
    status_match = re.search(r'\b(Sched(?:uled)?|Final|Forfeit|Live|In Progress|Postponed|Cancelled|Canceled)\b', whole, re.I)
    return {
        'DATE': date_match.group(0),
        'TIME': time_match.group(1) + ' ET' if time_match else '',
        'AWAY': away,
        'HOME': home,
        'STATUS': status_match.group(1) if status_match else '',
    }

def schedule_for_team(rows, team):
    selected=[]
    for row in rows:
        home=first_key(row,'HOME','HOME TEAM')
        away=first_key(row,'AWAY','AWAY TEAM','VISITOR','VISITING TEAM')
        matchup=first_key(row,'MATCHUP','GAME','TEAMS')
        if match_team(home, team) or match_team(away, team) or (matchup and any(re.search(r'(?<![A-Za-z])'+re.escape(alias)+r'(?![A-Za-z])',matchup,re.I) for alias in TEAM_ALIASES[team])):
            selected.append(row)
    return selected

def game_date(row):
    raw = first_key(row, 'DATE', 'GAME DATE', 'DAY', 'WHEN')
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y', '%b %d, %Y', '%B %d, %Y', '%a, %b %d, %Y'):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    # PlayCHL cards show e.g. "Nov 12" without a year. Choose the closest
    # calendar year to today, handling December/January season rollover.
    try:
        md = datetime.strptime(raw, '%b %d')
    except ValueError:
        return None
    today = datetime.now(EASTERN).date()
    candidates = [date(year, md.month, md.day) for year in (today.year - 1, today.year, today.year + 1)]
    return min(candidates, key=lambda d: abs((d - today).days))


def upcoming(rows):
    today = datetime.now(EASTERN).date()
    return sorted(
        [r for r in rows if game_date(r) is not None and game_date(r) >= today
         and first_key(r, 'STATUS').casefold() in ('sched', 'scheduled', 'postponed', '')],
        key=lambda r: (game_date(r), datetime.strptime(re.search(r'\d{1,2}:\d{2}\s*[AP]M', first_key(r, 'TIME'), re.I).group().replace(' ', '').upper(), '%I:%M%p').time() if re.search(r'\d{1,2}:\d{2}\s*[AP]M', first_key(r, 'TIME'), re.I) else datetime.min.time())
    )

def schedule_lines(rows, team):
    lines=[f'CHL SCHEDULE - {team} (times as shown on PlayCHL)']
    for row in rows[:20]:
        date_text=first_key(row,'DATE','GAME DATE','DAY','WHEN')
        time_text=first_key(row,'TIME','START','START TIME','PUCK DROP')
        home=first_key(row,'HOME','HOME TEAM')
        away=first_key(row,'AWAY','AWAY TEAM','VISITOR','VISITING TEAM')
        matchup=first_key(row,'MATCHUP','GAME','TEAMS')
        game= f'{away} @ {home}' if (home and away) else matchup
        lines.append(f"{date_text} {time_text} | {game} | {first_key(row, 'STATUS')}".strip())
    return lines

@bot.tree.command(name='schedule', description='Show CHL team schedule (Washington by default)')
@app_commands.describe(team='Team abbreviation, e.g. WSH, BUF, SJS')
async def schedule_command(interaction:discord.Interaction, team:str='WSH'):
    await interaction.response.defer(thinking=True)
    try:
        code=resolve_team(team)
        rows=schedule_for_team(await schedule_rows(),code)
        if not rows:
            return await interaction.followup.send('No games found for '+code+' in the readable schedule.',ephemeral=True)
        await reply(interaction,schedule_lines(upcoming(rows) or rows,code))
    except Exception as e:
        log.exception('Schedule command failed')
        await interaction.followup.send('Unable to read CHL schedule: '+str(e),ephemeral=True)

@bot.tree.command(name='nextgame', description='Show the next Washington Capitals CHL game')
async def nextgame_command(interaction:discord.Interaction):
    await interaction.response.defer(thinking=True)
    try:
        games=upcoming(schedule_for_team(await schedule_rows(),'WSH'))
        if not games:
            return await interaction.followup.send('No future Washington games with recognizable dates were found.',ephemeral=True)
        await reply(interaction,schedule_lines(games[:1],'WSH'))
    except Exception as e:
        log.exception('Next game lookup failed')
        await interaction.followup.send('Unable to read next game: '+str(e),ephemeral=True)

last_daily_date=None
@tasks.loop(minutes=30)
async def daily_schedule():
    global last_daily_date
    now=datetime.now(EASTERN)
    if now.hour < SCHEDULE_HOUR_ET or not CHANNEL_ID or last_daily_date==now.date():
        return
    try:
        rows=upcoming(schedule_for_team(await schedule_rows(),'WSH'))
        channel=bot.get_channel(CHANNEL_ID) or await bot.fetch_channel(CHANNEL_ID)
        if rows:
            for part in chunks(schedule_lines(rows[:10],'WSH')):
                await channel.send('**Daily CHL Washington Schedule**\n```\n'+part+'```\nSource: '+BASE+'/schedule',allowed_mentions=discord.AllowedMentions.none())
        else:
            log.info('No upcoming Washington games found; no daily schedule post')
        last_daily_date=now.date()
    except Exception:
        log.exception('Daily schedule read failed; will retry in 30 minutes')

@daily_schedule.before_loop
async def before_daily_schedule():
    await bot.wait_until_ready()

last_snapshot=None
@tasks.loop(minutes=30)
async def auto_update():
    global last_snapshot
    if not CHANNEL_ID: return
    channel=bot.get_channel(CHANNEL_ID)
    if channel is None:
        try: channel=await bot.fetch_channel(CHANNEL_ID)
        except Exception: log.exception('Cannot access configured channel'); return
    try:
        rows=await standings()
        lines=standing_lines(rows)
        snapshot='\n'.join(lines)
        if snapshot == last_snapshot: return  # avoid repeating unchanged standings
        for part in chunks(lines):
            await channel.send('**CHL Conference Standings — 30-minute check**\n```\n'+part+'```\nSource: '+BASE+'/standings',allowed_mentions=discord.AllowedMentions.none())
        last_snapshot=snapshot
    except Exception: log.exception('Automatic standings update failed')

@auto_update.before_loop
async def before_update(): await bot.wait_until_ready()


# Additional public-page readers: Advanced, Stats+, and Awards Race.
# These extract rendered HTML tables; the live PlayCHL markup must be verified.
async def expanded_stats(tab):
    tables = await page_tables('/statistics', tab)
    results = []
    for table in tables:
        headers = [re.sub(r'\s+', ' ', h).strip().upper() for h in table['headers']]
        if not any(h in ('PLAYER', 'NAME', 'GAMERTAG') for h in headers):
            continue
        for cells in table['rows']:
            if len(cells) >= len(headers):
                record = dict(zip(headers, cells))
                if value(record, 'PLAYER', 'NAME', 'GAMERTAG') != '-':
                    results.append(record)
    if not results:
        raise RuntimeError(f'No readable {tab} player table found. The PlayCHL tab may use a different layout.')
    return results

def expanded_lines(rows, heading):
    lines = [heading, 'Fields shown exactly as published by PlayCHL:']
    for r in rows[:15]:
        lines.append(' | '.join(f'{k}: {v}' for k, v in r.items() if v and k)[:400])
    return lines

# Present PlayCHL's published advanced stats in native Discord embeds.
# Unknown source columns are retained under Other / All Stats.
ADVANCED_SECTIONS = {
    'overview': ('Overview', ('GP', 'G', 'A', 'P', 'PTS', 'SH', 'S%', 'SV%', 'GAA', '+/-', 'OFF', 'DEF', 'TP')),
    'offense': ('Offense', ('GP', 'G', 'A', 'P', 'PTS', 'GWG', 'PPG', 'SHG', 'SH', 'SHA', 'S%', 'SON%', 'PASS', 'PA', 'PASS%', 'SAUC', 'FOW', 'FOL', 'FO%', 'GV', 'POSS', 'OFF')),
    'defense': ('Defense', ('TK', 'INT', 'HIT', 'BLK', 'PIM', 'PEND', 'PKZC', '+/-', 'DEF', 'TP')),
    'goalie': ('Goaltending', ('GP', 'SA', 'SV', 'SV%', 'GA', 'GAA', 'DSV', 'PCHK', 'PKZC', 'BRK SA', 'BRK SV', 'BRK SV%', 'PS SA', 'PS SV', 'PS SV%', 'SO PER', 'OFF', 'DEF', 'TP')),
}
ADVANCED_IDENTITY = {'PLAYER', 'NAME', 'GAMERTAG', 'TEAM', 'POS', 'POSITION'}


def advanced_embed(record, section='overview'):
    player = str(value(record, 'PLAYER', 'NAME', 'GAMERTAG'))
    team = str(value(record, 'TEAM'))
    heading = ADVANCED_SECTIONS.get(section, ('All Statistics', ()))[0]
    embed = discord.Embed(
        title=f'🏒 {player} — {heading}',
        description=f'**Team:** {team}  •  **Source:** PlayCHL Advanced',
        color=discord.Color.from_rgb(194, 32, 68),
        url=BASE + '/statistics',
    )
    if section == 'all':
        fields = [(str(k), str(v)) for k, v in record.items()
                  if k and k.upper() not in ADVANCED_IDENTITY and str(v).strip()]
    else:
        wanted = ADVANCED_SECTIONS[section][1]
        fields = [(key, str(record[key])) for key in wanted
                  if key in record and str(record[key]).strip()]
    if not fields:
        embed.description += '\nNo published fields were found for this section.'
    else:
        # Discord permits at most 25 fields per embed. Pack all stats into
        # readable groups of 8 lines so no PlayCHL columns are discarded.
        for offset in range(0, len(fields), 8):
            group = fields[offset:offset + 8]
            text = '\n'.join(f'**{k}**  {v}' for k, v in group)
            embed.add_field(name=f'Statistics {offset // 8 + 1}' if len(fields) > 8 else 'Statistics',
                            value=text[:1024], inline=False)
    embed.set_footer(text='Values are shown as published by PlayCHL')
    return embed



# Original hockey-game-inspired graphic: no official NHL/CHEL artwork or
# fabricated overall ratings. Only fields present in PlayCHL are displayed.
CHEL_FONT_DIR = '/usr/share/fonts/truetype/dejavu'
CHEL_FONT_FILES = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    'C:/Windows/Fonts/arialbd.ttf',
]
CHEL_REGULAR_FILES = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    'C:/Windows/Fonts/arial.ttf',
]


def _chel_font(size, bold=True):
    for path in (CHEL_FONT_FILES if bold else CHEL_REGULAR_FILES):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _chel_text(draw, xy, text, font, fill, max_width=None):
    text = str(text)
    if max_width:
        while len(text) > 2 and draw.textbbox((0, 0), text, font=font)[2] > max_width:
            text = text[:-2]
        if text != str(text):
            text += '…'
    draw.text(xy, text, font=font, fill=fill)


def chel_card_bytes(record, section='overview'):
    """Rink Rivals-inspired compact dark stat card, using only published values."""
    W, H = 1100, 740
    im = Image.new('RGB', (W, H), '#090f1c')
    d = ImageDraw.Draw(im)
    bg, panel, panel2 = '#090f1c', '#121d2d', '#192538'
    white, muted, purple, blue = '#f4f6fc', '#91a0b5', '#bb7bf7', '#8eb6ff'
    def rect(box, color, radius=19):
        d.rounded_rectangle(box, radius=radius, fill=color)
    def label(x, y, text, size=18, color=muted, bold=False):
        d.text((x,y), str(text), font=_chel_font(size, bold), fill=color)
    def field(*keys):
        for k in keys:
            v = record.get(k)
            if v is not None and str(v).strip() not in ('', '-'):
                return str(v).strip()
        return '—'
    name = field('PLAYER', 'NAME', 'GAMERTAG')
    team = field('TEAM')
    pos = field('POS', 'POSITION')
    games = field('GP')
    rect((20,20,1080,720),bg,36)
    d.line((58,71,1040,71), fill='#253248', width=2)
    rect((48,38,190,76), '#25345b', 19)
    label(70,47,'Career stats' if section=='overview' else 'Advanced stats',17,blue,True)
    label(984,46,'PLAYCHL',16,muted,True)
    rect((48,96,352,335),panel2,24)
    d.ellipse((140,120,260,240),fill=purple)
    letter = 'G' if pos.upper() in ('G','GOALIE','GK') else (pos[0].upper() if pos!='—' else 'P')
    label(178,149,letter,52,bg,True)
    label(142,257,'Most played',16,muted)
    label(107,281,'Goaltender' if letter=='G' else ('Position: '+pos),24,white,True)
    rect((365,96,1052,198),panel,21)
    label(390,122,name,31,white,True)
    label(392,162,team,17,purple,True)
    label(939,116,games,41,white,True)
    label(937,163,'Games played',15,muted)
    rect((365,209,1052,335),panel,21)
    label(390,224,'Positions',18,white,True)
    positions=['C','LW','RW','D','G']
    colors=['#ef6472','#34cf91','#49cce8','#ffd447',purple]
    for i,(p,c) in enumerate(zip(positions,colors)):
        x=390+i*130
        d.rounded_rectangle((x,270,x+108,278),radius=4,fill='#2a3548')
        if pos.upper()==p or (p=='G' and pos.upper()=='GOALIE'):
            d.rounded_rectangle((x,270,x+108,278),radius=4,fill=c)
        label(x,287,p,19,c,True)
        label(x+37,287,'1' if pos.upper()==p or (p=='G' and pos.upper()=='GOALIE') else '—',18,muted)
    rect((48,349,1052,489),panel,21)
    label(69,363,'SPLITS / SEASON',17,white,True)
    cols=[('GP',field('GP')),('G',field('G')),('A',field('A')),('P',field('P','PTS')),('SV%',field('SV%')),('SO',field('SO'))]
    for i,(k,v) in enumerate(cols):
        x=70+i*162
        label(x,405,k,17,muted,True)
        label(x,437,v,28,white,True)
    # Category tiles: never invent a value when the source lacks that column.
    if section=='all':
        fields=[(k,v) for k,v in record.items() if k and k.upper() not in ADVANCED_IDENTITY and str(v).strip()]
    else:
        fields=[(k,record[k]) for k in ADVANCED_SECTIONS.get(section,ADVANCED_SECTIONS['overview'])[1] if k in record and str(record[k]).strip()]
    fields=fields[:12]
    if not fields:
        fields=[('DATA','Not published')]
    tile_w=160 if len(fields)>6 else 160
    for i,(k,v) in enumerate(fields):
        col=i%6
        row=i//6
        x=48+col*168
        y=504+row*91
        rect((x,y,x+tile_w,y+82),panel2,14)
        label(x+13,y+10,str(k)[:12],15,muted,True)
        val=str(v)
        f=_chel_font(27 if len(val)<9 else 19)
        while d.textbbox((0,0),val,font=f)[2]>140 and f.size>13:
            f=_chel_font(f.size-2)
        d.text((x+13,y+38),val,font=f,fill=('#f27a8b' if val.startswith('-') else blue))
    label(57,688,'PLAYCHL  •  Published statistics only  •  Missing data shown as —',15,muted)
    out=io.BytesIO()
    im.save(out,format='PNG',optimize=True)
    out.seek(0)
    return out

async def chel_card_payload(record, section='overview'):
    png = await asyncio.to_thread(chel_card_bytes, record, section)
    embed = advanced_embed(record, section)
    embed.set_image(url='attachment://chel_player_card.png')
    return embed, discord.File(png, filename='chel_player_card.png')

class AdvancedStatsView(discord.ui.View):
    def __init__(self, record, owner_id):
        super().__init__(timeout=300)
        self.record = record
        self.owner_id = owner_id

    @discord.ui.select(
        placeholder='Choose a statistics category…',
        options=[
            discord.SelectOption(label='Overview', value='overview', emoji='📊'),
            discord.SelectOption(label='Offense', value='offense', emoji='🏒'),
            discord.SelectOption(label='Defense', value='defense', emoji='🛡️'),
            discord.SelectOption(label='Goaltending', value='goalie', emoji='🥅'),
            discord.SelectOption(label='All Stats', value='all', emoji='📋'),
        ],
    )
    async def category_selected(self, interaction: discord.Interaction, select: discord.ui.Select):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message(
                'Run /advanced yourself to browse statistics.', ephemeral=True)
        await interaction.response.defer()
        embed, graphic = await chel_card_payload(self.record, select.values[0])
        await interaction.message.edit(embed=embed, attachments=[graphic], view=self)


@bot.tree.command(name='advanced', description='Browse CHL advanced stats by player and category')
@app_commands.describe(player='Optional player name', team='Optional team abbreviation, e.g. WSH')
async def advanced_command(interaction: discord.Interaction, player: str = '', team: str = ''):
    await interaction.response.defer(thinking=True)
    try:
        rows = await expanded_stats('Advanced')
        if player:
            rows = [r for r in rows if player.casefold() in value(r, 'PLAYER', 'NAME', 'GAMERTAG').casefold()]
        if team:
            code = resolve_team(team)
            rows = [r for r in rows if match_team(value(r, 'TEAM'), code)]
        if not rows:
            return await interaction.followup.send(
                'No matching advanced statistics in the readable table.', ephemeral=True)
        # Show one interactive card per matching player, capped to avoid spam.
        for record in rows[:5]:
            embed, graphic = await chel_card_payload(record)
            await interaction.followup.send(
                embed=embed,
                file=graphic,
                view=AdvancedStatsView(record, interaction.user.id),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        if len(rows) > 5:
            await interaction.followup.send(
                f'Showing 5 of {len(rows)} players. Use `/advanced player:` to narrow the results.',
                ephemeral=True)
    except Exception as exc:
        log.exception('Advanced statistics failed')
        await interaction.followup.send(f'Unable to read advanced stats: {exc}', ephemeral=True)

@bot.tree.command(name='statsplus', description='Read CHL Stats+ for all teams or a selected player/team')
@app_commands.describe(player='Optional player name', team='Optional team abbreviation, e.g. WSH')
async def statsplus_command(interaction: discord.Interaction, player: str = '', team: str = ''):
    await interaction.response.defer(thinking=True)
    try:
        rows = await expanded_stats('Stats+')
        if player:
            rows = [r for r in rows if player.casefold() in value(r, 'PLAYER', 'NAME', 'GAMERTAG').casefold()]
        if team:
            code = resolve_team(team)
            rows = [r for r in rows if match_team(value(r, 'TEAM'), code)]
        if not rows:
            return await interaction.followup.send('No matching Stats+ rows in the readable table.', ephemeral=True)
        await reply(interaction, expanded_lines(rows, 'CHL STATS+'))
    except Exception as exc:
        log.exception('Stats+ failed')
        await interaction.followup.send(f'Unable to read Stats+: {exc}', ephemeral=True)

async def award_tables():
    """Read the seven award ranking cards shown on the public Awards Race page."""
    async with BROWSER_LOCK:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--disable-dev-shm-usage'])
            try:
                page = await browser.new_page()
                response = await page.goto(BASE + '/awards', wait_until='domcontentloaded', timeout=45000)
                if not response or response.status >= 400:
                    raise RuntimeError('PlayCHL awards page did not load')
                await page.locator('div.divide-y.divide-zinc-800').first.wait_for(timeout=20000)
                data = await page.evaluate(r"""() => {
                  const categories = [
                    ['MOST VALUABLE PLAYER','Hart Trophy'],
                    ['TOP SCORER','Art Ross Trophy'],
                    ['TOP GOAL SCORER','Maurice Richard Trophy'],
                    ['TOP PLAYMAKER','Most Assists'],
                    ['BEST DEFENSEMAN','Norris Trophy'],
                    ['BEST DEFENSIVE FORWARD','Selke Trophy'],
                    ['BEST GOALTENDER','Vezina Trophy']
                  ];
                  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
                  const result = [];
                  const lists = [...document.querySelectorAll('div.divide-y.divide-zinc-800')];
                  for (let i = 0; i < lists.length; i++) {
                    const list = lists[i];
                    const parent = list.parentElement;
                    const heading = clean(parent?.firstElementChild?.textContent).toUpperCase();
                    let award = categories.find(c => heading.includes(c[0]));
                    // PlayCHL shows these seven cards in the same order as the page.
                    // Fall back to card order when header markup changes.
                    if (!award) award = categories[i];
                    if (!award) continue;
                    const rows = [...list.children];
                    for (let j = 0; j < rows.length; j++) {
                      const row = rows[j];
                      const detail = row.querySelector('div.min-w-0.flex-1');
                      let lines = (detail?.innerText || '').split(/\n/).map(clean).filter(Boolean);
                      // Fallback: read text nodes within the player information container.
                      if (lines.length < 2 && detail) {
                        lines = [...detail.querySelectorAll('div,span,p')]
                          .filter(el => !el.querySelector('div,span,p'))
                          .map(el => clean(el.textContent)).filter(Boolean);
                      }
                      // Final fallback: whole row text has rank, name, team, score.
                      if (lines.length < 2) {
                        const rowLines = (row.innerText || '').split(/\n/).map(clean).filter(Boolean);
                        lines = rowLines.filter(t => !/^\d+$/.test(t) && !/^\d+(?:\.\d+)?%?$/.test(t));
                      }
                      const score = clean(row.querySelector('span.tabular-nums')?.textContent) ||
                        clean(row.querySelector('[class*="tabular-nums"]')?.textContent) ||
                        clean((row.innerText || '').split(/\n/).map(clean).filter(Boolean).at(-1));
                      const rank = clean(row.querySelector('span.w-5')?.textContent) || String(j+1);
                      if (lines.length < 2 || !score) continue;
                      result.push({CATEGORY:award[0],TROPHY:award[1],RANK:rank,
                                   PLAYER:lines[0],TEAM:lines[1],SCORE:score});
                    }
                  }
                  return result;
                }""")
                if not data:
                    # Diagnostic counts help distinguish markup changes from an empty page.
                    diagnostic = await page.evaluate("""() => ({
                      lists: document.querySelectorAll('div.divide-y.divide-zinc-800').length,
                      rows: document.querySelectorAll('div.divide-y.divide-zinc-800 > div').length,
                      headings: document.body.innerText.slice(0, 350)
                    })""")
                    log.error('Awards extraction diagnostics: %s', diagnostic)
                    raise RuntimeError('Awards cards found but no ranked players extracted; check Railway logs')
                log.info('Read %s awards entries', len(data))
                return data
            finally:
                await browser.close()

def award_lines(rows, heading):
    lines = [heading]
    current = None
    for r in rows:
        key = (r['CATEGORY'], r['TROPHY'])
        if key != current:
            lines.append('')
            lines.append(f"{r['CATEGORY']} — {r['TROPHY']}")
            current = key
        lines.append(f"#{r['RANK']} {r['PLAYER']} ({r['TEAM']}) — {r['SCORE']}")
    return lines

@bot.tree.command(name='awards', description='Show published CHL awards race data')
@app_commands.describe(trophy='Choose an award from the dropdown', team='Optional team abbreviation, e.g. WSH')
@app_commands.choices(trophy=[
    app_commands.Choice(name='All awards', value='all'),
    app_commands.Choice(name='Hart Trophy - MVP', value='MOST VALUABLE PLAYER'),
    app_commands.Choice(name='Art Ross Trophy - Top Scorer', value='TOP SCORER'),
    app_commands.Choice(name='Maurice Richard - Top Goal Scorer', value='TOP GOAL SCORER'),
    app_commands.Choice(name='Most Assists - Top Playmaker', value='TOP PLAYMAKER'),
    app_commands.Choice(name='Norris Trophy - Best Defenseman', value='BEST DEFENSEMAN'),
    app_commands.Choice(name='Selke Trophy - Best Defensive Forward', value='BEST DEFENSIVE FORWARD'),
    app_commands.Choice(name='Vezina Trophy - Best Goaltender', value='BEST GOALTENDER'),
])
async def awards_command(interaction: discord.Interaction, trophy: app_commands.Choice[str] = None, team: str = ''):
    await interaction.response.defer(thinking=True)
    try:
        rows = await award_tables()
        if trophy and trophy.value != 'all':
            rows = [r for r in rows if r['CATEGORY'].casefold() == trophy.value.casefold()]
        if team:
            code = resolve_team(team)
            rows = [r for r in rows if match_team(r['TEAM'], code)]
        if not rows:
            return await interaction.followup.send('No matching award candidates found in the readable tables.', ephemeral=True)
        await reply(interaction, award_lines(rows, 'CHL AWARDS RACE'))
    except Exception as exc:
        log.exception('Awards race failed')
        await interaction.followup.send(f'Unable to read awards race: {exc}', ephemeral=True)

last_awards_date = None
@tasks.loop(minutes=30)
async def daily_awards():
    global last_awards_date
    now = datetime.now(EASTERN)
    if now.hour < 9 or not CHANNEL_ID or last_awards_date == now.date():
        return
    try:
        rows = await award_tables()
        channel = bot.get_channel(CHANNEL_ID) or await bot.fetch_channel(CHANNEL_ID)
        for part in chunks(award_lines(rows, 'CHL DAILY AWARDS RACE')):
            await channel.send('```\n' + part + '```\nSource: ' + BASE + '/awards', allowed_mentions=discord.AllowedMentions.none())
        last_awards_date = now.date()
    except Exception:
        log.exception('Daily awards unavailable; will retry in 30 minutes')

@daily_awards.before_loop
async def before_daily_awards():
    await bot.wait_until_ready()

if __name__=='__main__':
    if not TOKEN: raise SystemExit('Set DISCORD_TOKEN in .env before running')
    bot.run(TOKEN)
