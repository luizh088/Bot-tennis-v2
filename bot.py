import os
import asyncio
import aiohttp
from telegram import Bot

BOT_TOKEN = os.environ['BOT_TOKEN']
CHAT_ID = os.environ['CHAT_ID']
bot = Bot(token=BOT_TOKEN)

games_notifications = {}

async def fetch_live_events(session):
    url = 'https://api.sofascore.com/api/v1/sport/tennis/events/live'
    headers = {'User-Agent': 'Mozilla/5.0'}
    async with session.get(url, headers=headers) as response:
        return await response.json()

async def fetch_point_by_point(session, event_id):
    url = f'https://api.sofascore.com/api/v1/event/{event_id}/point-by-point'
    headers = {'User-Agent': 'Mozilla/5.0'}
    async with session.get(url, headers=headers) as response:
        return await response.json()

async def process_game(session, event):
    tournament_category = event['tournament']['category']['slug']

    if tournament_category not in ['atp', 'challenger'] or event['homeTeam']['type'] != 1 or event['awayTeam']['type'] != 1:
        print(f"Ignorando torneio não ATP/Challenger: {tournament_category}")
        return

    event_id = event['id']
    home_name = event['homeTeam']['shortName']
    away_name = event['awayTeam']['shortName']
    game_slug = f"{home_name} x {away_name}"

    point_data = await fetch_point_by_point(session, event_id)

    if "pointByPoint" not in point_data or not point_data["pointByPoint"]:
        print(f"Jogo {game_slug} sem dados ponto a ponto disponíveis.")
        return

    current_set = point_data["pointByPoint"][0]
    current_game = current_set["games"][0]

    if not current_game or not current_game.get("points"):
        print(f"Jogo {game_slug} sem pontos disponíveis no game atual.")
        return

    current_game_number = current_game["game"]
    serving = current_game["score"]["serving"]

    server_name = home_name if serving == 1 else away_name
    receiver_name = away_name if serving == 1 else home_name

    points = current_game["points"]

    home_point = points[0]["homePoint"]
    away_point = points[0]["awayPoint"]

    sacador_perdeu_primeiro_ponto = (
        (serving == 1 and home_point == "0") or
        (serving == 2 and away_point == "0")
    )

    if sacador_perdeu_primeiro_ponto and len(points) == 1 and current_set.get("tieBreak") != True:
        if games_notifications.get(event_id) != current_game_number:
            message = (f"⚠️ {server_name} perdeu o primeiro ponto sacando contra "
                       f"{receiver_name} ({game_slug}, game {current_game_number}).")

            await bot.send_message(chat_id=CHAT_ID, text=message)
            print(f"Notificação enviada: {message}")
            games_notifications[event_id] = current_game_number

    if sacador_perdeu_primeiro_ponto and len(points) >= 2:
        second_point = points[1]
        home_second_point = second_point["homePoint"]
        away_second_point = second_point["awayPoint"]

        sacador_perdeu_segundo_ponto = (
            (serving == 1 and home_second_point == "0") or
            (serving == 2 and away_second_point == "0")
        )

        if sacador_perdeu_segundo_ponto:
            if "scoring" in current_game["score"] and current_game["score"]["scoring"] != -1:
                if games_notifications.get(f"completed_{event_id}") != current_game_number:
                    winner = current_game["score"]["scoring"]
                    emoji = "✅" if winner == serving else "❌"
                    if winner == serving:
                        message = f"{emoji} {server_name} venceu o game de saque ({game_slug}, game {current_game_number})."
                    else:
                        message = f"{emoji} {server_name} perdeu o game de saque ({game_slug}, game {current_game_number})."

                    await bot.send_message(chat_id=CHAT_ID, text=message)
                    print(f"Notificação enviada: {message}")
                    games_notifications[f"completed_{event_id}"] = current_game_number

async def monitor_all_games():
    await bot.send_message(chat_id=CHAT_ID, text="✅ Bot iniciado corretamente e enviando notificações!")
    print("Mensagem teste enviada ao Telegram.")

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                live_events = await fetch_live_events(session)
                events = live_events.get('events', [])
                print(f"Número de jogos sendo monitorados: {len(events)}")

                tasks = [process_game(session, event) for event in events]
                await asyncio.gather(*tasks)

                await asyncio.sleep(3)
            except Exception as e:
                print(f"Erro na execução: {e}")
                await asyncio.sleep(3)

if __name__ == '__main__':
    try:
        print("Bot inicializando corretamente.")
        asyncio.run(monitor_all_games())
    except Exception as e:
        print(f"Erro fatal ao iniciar o bot: {e}")