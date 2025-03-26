import os
import asyncio
import aiohttp
from telegram import Bot

BOT_TOKEN = os.environ['BOT_TOKEN']
CHAT_ID = os.environ['CHAT_ID']
bot = Bot(token=BOT_TOKEN)

# Dicionário para armazenar informações sobre games onde o sacador perdeu os 2 primeiros pontos.
# lost_first_two_points[(event_id, game_number)] = {
#     "server": 1 ou 2,
#     "server_name": str
# }
lost_first_two_points = {}

# Dicionário para **bloquear** novas notificações enquanto um game (para o mesmo event_id) não terminou.
# notification_game_in_progress[event_id] = game_number
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

    # Filtrar apenas torneios ATP/Challenger
    if tournament_category not in ['atp', 'challenger']:
        return

    # Filtrar apenas partidas simples (type=1)
    if event['homeTeam']['type'] != 1 or event['awayTeam']['type'] != 1:
        return

    event_id = event['id']
    home_name = event['homeTeam']['shortName']
    away_name = event['awayTeam']['shortName']
    game_slug = f"{home_name} x {away_name}"

    point_data = await fetch_point_by_point(session, event_id)
    if "pointByPoint" not in point_data or not point_data["pointByPoint"]:
        return

    # Geralmente o set em andamento fica no índice 0
    current_set = point_data["pointByPoint"][0]
    if not current_set.get("games"):
        return

    current_game = current_set["games"][0]
    if not current_game.get("points"):
        return

    current_game_number = current_game["game"]
    serving = current_game["score"]["serving"]  # 1 => home, 2 => away
    server_name = home_name if serving == 1 else away_name

    # Verifica se o game terminou
    game_finished = (
        "scoring" in current_game["score"] and
        current_game["score"]["scoring"] != -1
    )

    #--------------------------------------------------------------
    # 1) Se o game terminou, verificar se o sacador perdeu 2 pontos
    #    no início E se estava "bloqueado" nesse game.
    #--------------------------------------------------------------
    if game_finished:
        # Só precisamos notificar o resultado se este game faz parte
        # do "lost_first_two_points" e do "notification_game_in_progress".
        # Assim temos certeza de que enviamos a notificação final
        # e também liberamos o bloqueio.
        if (event_id, current_game_number) in lost_first_two_points:
            # Ver quem ganhou o game
            winner = current_game["score"]["scoring"]  # 1 => home, 2 => away
            data = lost_first_two_points[(event_id, current_game_number)]

            if winner == data["server"]:
                msg = (
                    f"✅ {data['server_name']} PERDEU os dois primeiros pontos do game, "
                    f"mas se recuperou e venceu ({game_slug}, game {current_game_number})."
                )
            else:
                msg = (
                    f"❌ {data['server_name']} PERDEU os dois primeiros pontos do game "
                    f"e acabou derrotado ({game_slug}, game {current_game_number})."
                )
            await bot.send_message(chat_id=CHAT_ID, text=msg)
            print("[FINAL de Game]:", msg)

            # Remove do dicionário para não notificar de novo
            del lost_first_two_points[(event_id, current_game_number)]

        # Se este game está marcado como "in progress" para esse event_id, liberamos
        if notification_game_in_progress.get(event_id) == current_game_number:
            del notification_game_in_progress[event_id]

        return  # Nada mais a fazer se o game acabou

    # Se o game NÃO terminou ainda, checamos o bloqueio:
    #--------------------------------------------------------------
    # 2) Se há um game em progresso (event_id => game_number),
    #    pulamos qualquer verificação de "perdeu dois pontos"
    #    para não mandar notificação duplicada. Precisamos
    #    esperar aquele game terminar.
    #--------------------------------------------------------------
    in_progress_game = notification_game_in_progress.get(event_id)
    if in_progress_game is not None:
        # Já existe um game bloqueando novas notificações para esse event_id
        # Se for o MESMO game, já tratamos na hora que ele acabar. Se for outro
        # game, significa que estamos esperando finalizar aquele para enviar
        # nova notificação.
        return

    #--------------------------------------------------------------
    # 3) Se não há bloqueio, verificamos se o sacador perdeu
    #    os 2 primeiros pontos, e se não for tie-break.
    #--------------------------------------------------------------
    if current_set.get("tieBreak") is True:
        return  # ignorar tie-break

    points = current_game["points"]
    if len(points) < 2:
        return  # não há pontos suficientes

    home_point_1 = points[0]["homePoint"]
    away_point_1 = points[0]["awayPoint"]
    home_point_2 = points[1]["homePoint"]
    away_point_2 = points[1]["awayPoint"]

    lost_first_point = (
        (serving == 1 and home_point_1 == "0") or
        (serving == 2 and away_point_1 == "0")
    )
    lost_second_point = (
        (serving == 1 and home_point_2 == "0") or
        (serving == 2 and away_point_2 == "0")
    )

    if lost_first_point and lost_second_point:
        # Assim que detectamos, enviamos notificação e BLOQUEAMOS.
        msg = (
            f"⚠️ {server_name} perdeu os DOIS primeiros pontos do game "
            f"({game_slug}, game {current_game_number})."
        )
        await bot.send_message(chat_id=CHAT_ID, text=msg)
        print("[INÍCIO de Game]:", msg)

        # Guarda info de quem saca, etc.
        lost_first_two_points[(event_id, current_game_number)] = {
            "server": serving,
            "server_name": server_name,
        }
        # Bloqueia novas notificações até esse game acabar
        notification_game_in_progress[event_id] = current_game_number


async def monitor_all_games():
    await bot.send_message(chat_id=CHAT_ID, text="✅ Bot iniciado e monitorando partidas!")
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
                print("Erro na execução:", e)
                await asyncio.sleep(5)

if __name__ == '__main__':
    try:
        print("Bot inicializando...")
        asyncio.run(monitor_all_games())
    except Exception as e:
        print("Erro fatal ao iniciar o bot:", e)