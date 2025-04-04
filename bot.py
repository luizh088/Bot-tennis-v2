import os
import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
from telegram import Bot

BOT_TOKEN = os.environ['BOT_TOKEN']
CHAT_ID = os.environ['CHAT_ID']
bot = Bot(token=BOT_TOKEN)

games_notifications = {}

async def fetch_live_events(page):
    await page.goto('https://www.sofascore.com/tennis/livescore')
    await page.wait_for_selector('.event-list-item')
    html = await page.content()
    soup = BeautifulSoup(html, 'html.parser')

    events = []
    for item in soup.select('.event-list-item'):
        event_id = item['id'].split('-')[-1]
        home_name = item.select_one('.home-team').text.strip()
        away_name = item.select_one('.away-team').text.strip()
        tournament_slug = item.select_one('.cell__section--category').text.strip().lower()

        events.append({
            'id': event_id,
            'homeTeam': {'shortName': home_name, 'type': 1},
            'awayTeam': {'shortName': away_name, 'type': 1},
            'tournament': {'category': {'slug': tournament_slug}}
        })

    return events

async def fetch_point_by_point(page, event_id):
    url = f'https://www.sofascore.com/event/{event_id}/point-by-point'
    await page.goto(url)
    await page.wait_for_selector('.point-by-point')
    html = await page.content()
    soup = BeautifulSoup(html, 'html.parser')

    points_elements = soup.select('.point-by-point .point')
    points = [{'text': p.text.strip()} for p in points_elements]

    return points

async def process_game(page, event):
    tournament_category = event['tournament']['category']['slug']

    if tournament_category not in ['atp', 'challenger']:
        return

    event_id = event['id']
    home_name = event['homeTeam']['shortName']
    away_name = event['awayTeam']['shortName']
    game_slug = f"{home_name} x {away_name}"

    points = await fetch_point_by_point(page, event_id)

    if len(points) < 2:
        return

    first_point = points[0]['text']
    second_point = points[1]['text']

    if "0-15" in first_point and "0-30" in second_point:
        if games_notifications.get(f"two_lost_{event_id}") != first_point:
            message = f"⚠️ Sacador perdeu os DOIS primeiros pontos no jogo {game_slug}."
            await bot.send_message(chat_id=CHAT_ID, text=message)
            games_notifications[f"two_lost_{event_id}"] = first_point

    last_point = points[-1]['text']
    if "Game" in last_point:
        if games_notifications.get(f"completed_{event_id}") != last_point:
            emoji = "✅" if "Game won by server" in last_point else "❌"
            message = f"{emoji} Resultado do game: {last_point} ({game_slug})."
            await bot.send_message(chat_id=CHAT_ID, text=message)
            games_notifications[f"completed_{event_id}"] = last_point

async def monitor_all_games():
    await bot.send_message(chat_id=CHAT_ID, text="✅ Bot iniciado corretamente e enviando notificações!")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()

        while True:
            try:
                events = await fetch_live_events(page)
                tasks = [process_game(page, event) for event in events]
                await asyncio.gather(*tasks)
                await asyncio.sleep(5)
            except Exception as e:
                print(f"Erro na execução: {e}")
                await asyncio.sleep(5)

if __name__ == '__main__':
    asyncio.run(monitor_all_games())
