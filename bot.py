import os
import asyncio
import aiohttp
from telegram import Bot

BOT_TOKEN = os.environ['BOT_TOKEN']
CHAT_ID = os.environ['CHAT_ID']
bot = Bot(token=BOT_TOKEN)

# Armazena informações de games em que o sacador perdeu os dois primeiros pontos
# Forma: lost_first_two_points[(event_id, game_number)] = {
#   "server": 1 ou 2,
#   "server_name": str,
# }
lost_first_two_points = {}

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

    # Filtrar apenas ATP/Challenger
    if tournament_category not in ['atp', 'challenger']:
        return

    # Filtrar apenas partidas simples (type=1)
    if event['homeTeam']['type'] != 1 or event['awayTeam']['type'] != 1:
        return

    event_id = event['id']
    home_name = event['homeTeam']['shortName']
    away_name = event['awayTeam']['shortName']
    game_slug = f"{home_name} x {away_name}"

    # Dados ponto a ponto
    point_data = await fetch_point_by_point(session, event_id)
    if "pointByPoint" not in point_data or not point_data["pointByPoint"]:
        return

    # O set em andamento normalmente é o índice 0
    current_set = point_data["pointByPoint"][0]
    # Se não tiver games, sai
    if not current_set.get("games"):
        return

    current_game = current_set["games"][0]
    if not current_game.get("points"):
        return

    current_game_number = current_game["game"]
    serving = current_game["score"]["serving"]  # 1 => home saca, 2 => away saca
    server_name = home_name if serving == 1 else away_name

    # Verifica se o game acabou
    # Sofascore usa "scoring" != -1 pra indicar que o game tem vencedor
    game_is_finished = (
        "scoring" in current_game["score"] 
        and current_game["score"]["scoring"] != -1
    )

    # ------------------------------
    # 1) Se o game acabou,
    #    verifique se o sacador
    #    perdeu os dois primeiros
    #    pontos lá no começo.
    # ------------------------------
    if game_is_finished:
        # Se constar em lost_first_two_points, notificamos agora
        if (event_id, current_game_number) in lost_first_two_points:
            info = lost_first_two_points[(event_id, current_game_number)]
            del lost_first_two_points[(event_id, current_game_number)]  # remove pra não duplicar

            # Vê quem ganhou o game (1 => home, 2 => away)
            winner = current_game["score"]["scoring"]
            if winner == info["server"]:
                # sacador ganhou
                message = (
                    f"⚠️ {info['server_name']} PERDEU os dois primeiros pontos do game "
                    f"e mesmo assim conseguiu vencer ({game_slug}, game {current_game_number})."
                )
            else:
                message = (
                    f"⚠️ {info['server_name']} PERDEU os dois primeiros pontos do game "
                    f"e acabou derrotado ({game_slug}, game {current_game_number})."
                )
            await bot.send_message(chat_id=CHAT_ID, text=message)
            print(f"[Notificação - Fim de Game]: {message}")

        # Já que o game acabou, nada mais a fazer
        return

    # Se chegou aqui, o game ainda está em andamento

    # Ignora tie-break
    is_tiebreak = current_set.get("tieBreak") == True
    if is_tiebreak:
        return

    points = current_game["points"]
    if len(points) < 2:
        return  # ainda não há 2 pontos pra analisar

    # Primeiro ponto
    home_point_1 = points[0]["homePoint"]
    away_point_1 = points[0]["awayPoint"]
    # Segundo ponto
    home_point_2 = points[1]["homePoint"]
    away_point_2 = points[1]["awayPoint"]

    sacador_perdeu_1 = (serving == 1 and home_point_1 == "0") or (serving == 2 and away_point_1 == "0")
    sacador_perdeu_2 = (serving == 1 and home_point_2 == "0") or (serving == 2 and away_point_2 == "0")

    if sacador_perdeu_1 and sacador_perdeu_2:
        # Anota que este game teve o sacador perdendo os 2 primeiros pontos
        # mas NÃO notificamos agora. Só no fim do game.
        if (event_id, current_game_number) not in lost_first_two_points:
            lost_first_two_points[(event_id, current_game_number)] = {
                "server": serving,
                "server_name": server_name
            }
            print(f"[INFO] {server_name} perdeu os 2 primeiros pontos. Aguardando fim do game para notificar...")

async def monitor_all_games():
    # Mensagem de inicialização
    await bot.send_message(chat_id=CHAT_ID, text="✅ Bot iniciado e enviando apenas 1 notificação por game!")
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