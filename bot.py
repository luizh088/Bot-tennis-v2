import os
import asyncio
import aiohttp
from telegram import Bot

BOT_TOKEN = os.environ['BOT_TOKEN']
CHAT_ID = os.environ['CHAT_ID']
bot = Bot(token=BOT_TOKEN)

games_notifications = {}
blocked_game = None

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
    global blocked_game

    # Filtra torneios
    tournament_category = event['tournament']['category']['slug']
    if tournament_category not in ['atp', 'challenger']:
        return

    # Filtra se não é simples
    if event['homeTeam']['type'] != 1 or event['awayTeam']['type'] != 1:
        return

    event_id = event['id']
    home_name = event['homeTeam']['shortName']
    away_name = event['awayTeam']['shortName']
    game_slug = f"{home_name} x {away_name}"

    point_data = await fetch_point_by_point(session, event_id)
    if "pointByPoint" not in point_data or not point_data["pointByPoint"]:
        return

    # Pegar o último set
    last_set = point_data["pointByPoint"][-1]
    if "games" not in last_set or not last_set["games"]:
        return

    # Pegar o último game do último set
    current_game = last_set["games"][-1]
    if not current_game or not current_game.get("points"):
        return

    current_game_number = current_game["game"]
    serving = current_game["score"]["serving"]
    scoring = current_game["score"].get("scoring", -1)

    server_name = home_name if serving == 1 else away_name
    receiver_name = away_name if serving == 1 else home_name

    # 1) Se o game já está concluído, só notifica se for o MESMO game do bloqueio
    if scoring != -1:
        if blocked_game == (event_id, current_game_number):
            # Avisa quem ganhou o game, se ainda não avisamos
            if games_notifications.get(f"completed_{event_id}") != current_game_number:
                winner = scoring
                emoji = "✅" if winner == serving else "❌"
                if winner == serving:
                    message = f"{emoji} {server_name} venceu o game de saque ({game_slug}, game {current_game_number})."
                else:
                    message = f"{emoji} {server_name} perdeu o game de saque ({game_slug}, game {current_game_number})."

                await bot.send_message(chat_id=CHAT_ID, text=message)
                print(f"[NOTIFICAÇÃO] {message}")
                games_notifications[f"completed_{event_id}"] = current_game_number

            # Libera bloqueio
            blocked_game = None
            print(f"[INFO] Game {current_game_number} finalizado e bloqueio liberado.")
        return

    # 2) Se o game NÃO está concluído, checar se perdeu 2 pontos
    points = current_game["points"]
    if len(points) < 2:
        return

    home_first_point = points[0]["homePoint"]
    away_first_point = points[0]["awayPoint"]
    home_second_point = points[1]["homePoint"]
    away_second_point = points[1]["awayPoint"]

    sacador_perdeu_primeiro_ponto = (
        (serving == 1 and home_first_point == "0") or
        (serving == 2 and away_first_point == "0")
    )
    sacador_perdeu_segundo_ponto = (
        (serving == 1 and home_second_point == "0") or
        (serving == 2 and away_second_point == "0")
    )

    is_tie_break = last_set.get("tieBreak", False)

    # Cria chave para não notificar duas vezes no mesmo game
    lost2points_key = f"lost2points_{event_id}_{current_game_number}"
    if sacador_perdeu_primeiro_ponto and sacador_perdeu_segundo_ponto and not is_tie_break:
        # Só notifica se:
        #  - não existe bloqueio de outro game
        #  - ainda não notificamos esse game
        if blocked_game is None and lost2points_key not in games_notifications:
            message = (
                f"⚠️ {server_name} perdeu os DOIS primeiros pontos sacando contra "
                f"{receiver_name} ({game_slug}, game {current_game_number})."
            )
            await bot.send_message(chat_id=CHAT_ID, text=message)
            print(f"[NOTIFICAÇÃO] {message}")
            # Marca que este game recebeu a notificação
            games_notifications[lost2points_key] = True
            # Ativa bloqueio
            blocked_game = (event_id, current_game_number)

async def monitor_all_games():
    await bot.send_message(chat_id=CHAT_ID, text="✅ Bot iniciado corretamente e enviando notificações!")
    print("Mensagem de teste enviada ao Telegram.")

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                live_events = await fetch_live_events(session)
                events = live_events.get('events', [])

                tasks = [process_game(session, event) for event in events]
                await asyncio.gather(*tasks)

                await asyncio.sleep(3)
            except Exception as e:
                print(f"Erro na execução: {e}")
                await asyncio.sleep(3)

if __name__ == '__main__':
    try:
        print("Bot inicializando corretamente (apenas GAMES que perderam 2 pontos).")
        asyncio.run(monitor_all_games())
    except Exception as e:
        print(f"Erro fatal ao iniciar o bot: {e}")