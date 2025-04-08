import os
import asyncio
import aiohttp
from telegram import Bot
import json

BOT_TOKEN = os.environ['BOT_TOKEN']
CHAT_ID = os.environ['CHAT_ID']
bot = Bot(token=BOT_TOKEN)

PROXY_BASE = "https://web-production-ea045.up.railway.app/"

games_notifications = {}

async def fetch_via_proxy(session, url):
    proxied_url = f"{PROXY_BASE}{url}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Origin": "https://meusite.com",
        "Accept": "application/json"
    }
    try:
        async with session.get(proxied_url, headers=headers) as response:
            print(f"📡 {response.status} → {proxied_url}")
            text = await response.text()
            try:
                return json.loads(text)
            except Exception as e:
                print(f"⚠️ JSON inválido recebido:\n{text}")
                return {}
    except Exception as e:
        print(f"❌ Erro em fetch_via_proxy: {e} ({proxied_url})")
        return {}

async def fetch_live_events(session):
    url = 'https://api.sofascore.com/api/v1/sport/tennis/events/live'
    return await fetch_via_proxy(session, url)

async def fetch_point_by_point(session, event_id):
    url = f'https://api.sofascore.com/api/v1/event/{event_id}/point-by-point'
    return await fetch_via_proxy(session, url)

async def process_game(session, event):
    tournament_category = event['tournament']['category']['slug']

    if tournament_category not in ['atp', 'challenger'] or \
       event['homeTeam']['type'] != 1 or event['awayTeam']['type'] != 1:
        return

    event_id = event['id']
    home_name = event['homeTeam']['shortName']
    away_name = event['awayTeam']['shortName']
    game_slug = f"{home_name} x {away_name}"

    point_data = await fetch_point_by_point(session, event_id)

    if "pointByPoint" not in point_data or not point_data["pointByPoint"]:
        return

    current_set = point_data["pointByPoint"][0]
    current_game = current_set["games"][0] if current_set["games"] else None

    if not current_game or not current_game.get("points"):
        return

    current_game_number = current_game["game"]
    serving = current_game["score"]["serving"]
    server_name = home_name if serving == 1 else away_name
    receiver_name = away_name if serving == 1 else home_name

    points = current_game["points"]
    home_point_first = points[0]["homePoint"]
    away_point_first = points[0]["awayPoint"]

    sacador_perdeu_primeiro_ponto = (
        (serving == 1 and home_point_first == "0") or
        (serving == 2 and away_point_first == "0")
    )

    if sacador_perdeu_primeiro_ponto and len(points) >= 2:
        home_point_second = points[1]["homePoint"]
        away_point_second = points[1]["awayPoint"]

        sacador_perdeu_segundo_ponto = (
            (serving == 1 and home_point_second == "0") or
            (serving == 2 and away_point_second == "0")
        )

        if sacador_perdeu_segundo_ponto:
            if games_notifications.get(f"two_lost_{event_id}") != current_game_number:
                message = (
                    f"⚠️ {server_name} perdeu os DOIS primeiros pontos sacando contra "
                    f"{receiver_name} ({game_slug}, game {current_game_number})."
                )
                await bot.send_message(chat_id=CHAT_ID, text=message)
                games_notifications[f"two_lost_{event_id}"] = current_game_number

            if "scoring" in current_game["score"] and current_game["score"]["scoring"] != -1:
                if games_notifications.get(f"completed_{event_id}") != current_game_number:
                    winner = current_game["score"]["scoring"]
                    emoji = "✅" if winner == serving else "❌"
                    if winner == serving:
                        message = (
                            f"{emoji} {server_name} venceu o game de saque "
                            f"({game_slug}, game {current_game_number})."
                        )
                    else:
                        message = (
                            f"{emoji} {server_name} perdeu o game de saque "
                            f"({game_slug}, game {current_game_number})."
                        )

                    await bot.send_message(chat_id=CHAT_ID, text=message)
                    games_notifications[f"completed_{event_id}"] = current_game_number

async def monitor_all_games():
    try:
        await bot.send_message(chat_id=CHAT_ID, text="✅ Bot iniciado corretamente e enviando notificações!")
        print("✅ Bot Telegram notificado com sucesso.")
    except Exception as e:
        print(f"❌ Erro ao notificar início do bot: {e}")

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                print("🔄 Buscando eventos ao vivo...")
                live_events = await fetch_live_events(session)
                events = live_events.get('events', [])
                print(f"🎾 Total de jogos encontrados: {len(events)}")
                tasks = [process_game(session, event) for event in events]
                await asyncio.gather(*tasks)
                await asyncio.sleep(5)
            except Exception as e:
                print(f"❌ Erro na execução do loop principal: {e}")
                await asyncio.sleep(5)

if __name__ == '__main__':
    try:
        print("🚀 Iniciando o bot...")
        print(f"🔐 BOT_TOKEN definido? {'Sim' if BOT_TOKEN else 'Não'}")
        print(f"📬 CHAT_ID definido? {'Sim' if CHAT_ID else 'Não'}")
        asyncio.run(monitor_all_games())
    except Exception as e:
        print(f"💥 Erro fatal ao iniciar o bot: {e}")
