"""Read public schedule cards. No credentials or private API access."""
import asyncio
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from playwright.async_api import async_playwright

_slots = asyncio.Semaphore(1)

async def read_schedule_cards():
    async with _slots:
        return await asyncio.wait_for(_read(), timeout=120)

async def _read():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            response = await page.goto('https://playchl.com/schedule',
                                       wait_until='domcontentloaded', timeout=45000)
            if not response or response.status >= 400:
                raise RuntimeError('PlayCHL schedule request failed')
            cards = page.locator('a[href^="/games/"]')
            await cards.first.wait_for(state='visible', timeout=30000)
            records = await cards.evaluate_all('''els => els.map(el => {
                const c = el.children;
                const teams = Array.from(el.querySelectorAll('img[alt]')).map(x => x.alt);
                return {
                    date: c[0]?.children[0]?.textContent?.trim(),
                    time: c[0]?.children[1]?.textContent?.trim(),
                    away: teams[0], home: teams[1],
                    status: c[c.length - 1]?.textContent?.trim()
                };
            })''')
            # The public cards omit the year. Override for seasons spanning New Year.
            year = int(os.getenv('SCHEDULE_YEAR') or
                       datetime.now(ZoneInfo('America/New_York')).year)
            output = []
            for item in records:
                if not all(item.get(k) for k in ('date', 'time', 'away', 'home', 'status')):
                    raise RuntimeError('PlayCHL schedule card fields changed')
                parsed = datetime.strptime(f"{item['date']} {year}", '%b %d %Y')
                output.append({'DATE': parsed.strftime('%Y-%m-%d'),
                               'TIME': item['time'], 'AWAY': item['away'],
                               'HOME': item['home'], 'STATUS': item['status']})
            if not output:
                raise RuntimeError('No readable PlayCHL schedule cards')
            return output
        finally:
            await browser.close()
