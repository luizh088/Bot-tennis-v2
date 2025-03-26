import os
import asyncio
import aiohttp
from telegram import Bot

BOT_TOKEN = os.environ['BOT_TOKEN']
CHAT_ID = os.environ['CHAT_ID']
bot = Bot(token=BOT_TOKEN)

# Marca (event_id, game_number) que já receberam notificação de "perdeu primeiros 2 pontos"
lost_first_two_points = {}
# Indica se há um game em andamento que bloqueia novas notificações
notification_game_in_progress = {}

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

    # 1) Filtrar torneios que não sejam atp/challenger
    if tournament_category not in ['atp', 'challenger']:
        return

    # 2) Filtrar apenas partidas simples (type=1)
    if event['homeTeam']['type'] != 1 or event['awayTeam']['type'] != 1:
        return

    event_id = event['id']
    home_name = event['homeTeam']['shortName']
    away_name = event['awayTeam']['shortName']
    game_slug = f"{home_name} x {away_name}"

    point_data = await fetch_point_by_point(session, event_id)
    if "pointByPoint" not in point_data or not point_data["pointByPoint"]:
        return

    # O set em andamento costuma ser o índice 0
    current_set = point_data["pointByPoint"][0]
    if not current_set.get("games"):
        return

    current_game = current_set["games"][0]
    if not current_game or not current_game.get("points"):
        return

    current_game_number = current_game["game"]
    serving = current_game["score"]["serving"]  # 1 => home está sacando, 2 => away está sacando
    server_name = home_name if serving == 1 else away_name
    receiver_name = away_name if serving == 1 else home_name

    # -----------------------------------------------------------
    # 1) Antes de qualquer coisa, checamos se já existe um game
    #    "bloqueado" (notification_game_in_progress[event_id]).
    #    Se sim, só continuamos se ESTRITAMENTE for o mesmo game
    #    e pudermos enviar o resultado final (ou se já liberamos).
    # -----------------------------------------------------------
    in_progress_game = notification_game_in_progress.get(event_id, None)
    if in_progress_game is not None:
        # Há um game que já notificamos e ainda não terminou
        if in_progress_game == current_game_number:
            # É o mesmo game que disparou notificação. Vamos verificar se o game acabou agora
            if "scoring" in current_game["score"] and current_game["score"]["scoring"] != -1:
                # O game terminou. Precisamos avisar se o sacador ganhou ou perdeu.
                # Mas só se esse game começou com o sacador perdendo os dois primeiros pontos:
                if (event_id, current_game_number) in lost_first_two_points:
                    server_info = lost_first_two_points[(event_id, current_game_number)]
                    # Verificar quem ganhou (score["scoring"] == 1 => home, == 2 => away)
                    winner = current_game["score"]["scoring"]
                    if winner == server_info['server']:
                        message = (
                            f"✅ {server_info['server_name']} se recuperou e VENCEU o game "
                            f"mesmo após perder os dois primeiros pontos ({game_slug}, game {current_game_number})."
                        )
                    else:
                        message = (
                            f"❌ {server_info['server_name']} PERDEU o game "
                            f"após ter perdido os dois primeiros pontos ({game_slug}, game {current_game_number})."
                        )
                    await bot.send_message(chat_id=CHAT_ID, text=message)
                    print(f"Notificação de resultado enviada: {message}")

                    # Limpa dados
                    lost_first_two_points.pop((event_id, current_game_number), None)

                # Libera este evento para receber novas notificações em futuros games
                notification_game_in_progress.pop(event_id, None)
            # Se o game ainda não acabou, não fazemos nada
            return
        else:
            # Se in_progress_game != current_game_number
            # Quer dizer que o game anterior acabou e já liberamos a notificação,
            # mas não removemos. Vamos remover apenas por segurança:
            notification_game_in_progress.pop(event_id, None)
            # Agora seguimos abaixo para ver se notificamos um novo game
            # (se o server perdeu 2 pontos no game atual).
    
    # -----------------------------------------------------------
    # 2) Verificar se o sacador perdeu os DOIS primeiros pontos 
    #    do game, ignorando tie-break.
    # -----------------------------------------------------------
    if current_set.get("tieBreak") == True:
        return  # ignoramos tie-break

    points = current_game["points"]
    if len(points) < 2:
        return  # não há pontos suficientes para verificar

    # Primeiro e segundo ponto
    home_point_1 = points[0]["homePoint"]
    away_point_1 = points[0]["awayPoint"]
    home_point_2 = points[1]["homePoint"]
    away_point_2 = points[1]["awayPoint"]

    sacador_perdeu_primeiro_ponto = (
        (serving == 1 and home_point_1 == "0") or
        (serving == 2 and away_point_1 == "0")
    )
    sacador_perdeu_segundo_ponto = (
        (serving == 1 and home_point_2 == "0") or
        (serving == 2 and away_point_2 == "0")
    )

    if sacador_perdeu_primeiro_ponto and sacador_perdeu_segundo_ponto:
        # Se ainda não enviamos notificação para ESTE exato game
        if (event_id, current_game_number) not in lost_first_two_points:
            lost_first_two_points[(event_id, current_game_number)] = {
                'server': serving,
                'server_name': server_name
            }
            # Enviar aviso
            message = (
                f"⚠️ {server_name} perdeu os DOIS primeiros pontos sacando contra "
                f"{receiver_name} ({game_slug}, game {current_game_number})."
            )
            await bot.send_message(chat_id=CHAT_ID, text=message)
            print(f"Notificação enviada: {message}")

            # Bloqueia novas notificações até que este game termine
            notification_game_in_progress[event_id] = current_game_number

async def monitor_all_games():
    # Mensagem inicial de teste
    await bot.send_message(chat_id=CHAT_ID, text="✅ Bot iniciado corretamente e enviando notificações!")
    print("Mensagem inicial enviada ao Telegram.")

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                live_events = await fetch_live_events(session)
                events = live_events.get('events', [])
                print(f"Número de jogos ao vivo: {len(events)}")

                tasks = [process_game(session, event) for event in events]
                await asyncio.gather(*tasks)

                await asyncio.sleep(3)
            except Exception as e:
                print(f"Erro na execução: {e}")
                await asyncio.sleep(3)

if __name__ == '__main__':
    try:
        print("Bot inicializando...")
        asyncio.run(monitor_all_games())
    except Exception as e:
        print(f"Erro fatal ao iniciar o bot: {e}")