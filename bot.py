import os
import asyncio
import aiohttp
from telegram import Bot

BOT_TOKEN = os.environ['BOT_TOKEN']
CHAT_ID = os.environ['CHAT_ID']
bot = Bot(token=BOT_TOKEN)

# Bloqueio global. Se for None, não há bloqueio ativo.
# Se estiver definido, armazena (event_id, set_number, game_number).
global_current_lock = None

# Armazena dados do game bloqueante (ex.: quem saca, para msg final).
lost_first_two_info = {}

async def fetch_live_events(session):
    url = "https://api.sofascore.com/api/v1/sport/tennis/events/live"
    headers = {"User-Agent": "Mozilla/5.0"}
    async with session.get(url, headers=headers) as response:
        return await response.json()

async def fetch_point_by_point(session, event_id):
    url = f"https://api.sofascore.com/api/v1/event/{event_id}/point-by-point"
    headers = {"User-Agent": "Mozilla/5.0"}
    async with session.get(url, headers=headers) as response:
        return await response.json()

async def process_game(session, event):
    global global_current_lock
    global lost_first_two_info

    # Filtra torneios fora de atp/challenger
    tournament_category = event["tournament"]["category"]["slug"]
    if tournament_category not in ["atp", "challenger"]:
        return

    # Filtra partidas de simples (type=1)
    if event["homeTeam"]["type"] != 1 or event["awayTeam"]["type"] != 1:
        return

    event_id = event["id"]
    home_name = event["homeTeam"]["shortName"]
    away_name = event["awayTeam"]["shortName"]
    game_slug = f"{home_name} x {away_name}"

    # Dados "point-by-point"
    point_data = await fetch_point_by_point(session, event_id)
    if "pointByPoint" not in point_data or not point_data["pointByPoint"]:
        return

    # Geralmente o set em andamento é o último no array:
    sets_data = point_data["pointByPoint"]
    current_set = sets_data[-1]  # Se no SofaScore o último for o set em andamento
    if not current_set.get("games"):
        return

    # Pega o último game do set
    current_game = current_set["games"][-1]
    if not current_game.get("points"):
        return

    # Identificação do set: use "number" ou o índice do array
    set_number = current_set["number"] if "number" in current_set else len(sets_data)
    current_game_number = current_game["game"]

    serving = current_game["score"]["serving"]  # 1 => home, 2 => away
    server_name = home_name if serving == 1 else away_name

    # Verifica se o game terminou
    game_finished = (
        "scoring" in current_game["score"] 
        and current_game["score"]["scoring"] != -1
    )

    game_id = (event_id, set_number, current_game_number)

    # (A) Se o game terminou, checar se é o game que está bloqueando
    if game_finished:
        if global_current_lock == game_id:
            # Notificação final
            if lost_first_two_info:
                winner = current_game["score"]["scoring"]  # 1 => home, 2 => away
                data = lost_first_two_info
                if winner == data["server"]:
                    msg = (
                        f"✅ {data['server_name']} PERDEU os dois primeiros pontos do game, "
                        f"mas venceu ({game_slug}, set {set_number}, game {current_game_number})."
                    )
                else:
                    msg = (
                        f"❌ {data['server_name']} PERDEU os dois primeiros pontos do game "
                        f"e acabou derrotado ({game_slug}, set {set_number}, game {current_game_number})."
                    )
                await bot.send_message(chat_id=CHAT_ID, text=msg)
                print("[FINAL de Game]:", msg)

            # Libera o bloqueio
            global_current_lock = None
            lost_first_two_info = {}
        return

    # (B) Se o game não terminou e já existe um bloqueio global,
    #     não faz nada (nem para a mesma partida nem para outra).
    if global_current_lock is not None:
        return

    # (C) Se não há bloqueio, checa se o sacador perdeu 2 pontos iniciais.
    if current_set.get("tieBreak") is True:
        return  # ignorar tie-break

    points = current_game["points"]
    if len(points) < 2:
        return

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
        # Envia notificação e bloqueia globalmente
        msg = (
            f"⚠️ {server_name} perdeu os DOIS primeiros pontos do game "
            f"({game_slug}, set {set_number}, game {current_game_number})."
        )
        await bot.send_message(chat_id=CHAT_ID, text=msg)
        print("[INÍCIO de Game]:", msg)

        global_current_lock = game_id
        lost_first_two_info = {
            "server": serving,
            "server_name": server_name
        }

async def monitor_all_games():
    await bot.send_message(chat_id=CHAT_ID, text="✅ Bot iniciado (bloqueio global)!")
    print("Mensagem inicial enviada ao Telegram.")

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                live_events = await fetch_live_events(session)
                events = live_events.get("events", [])
                print(f"Número de jogos ao vivo: {len(events)}")

                tasks = [process_game(session, event) for event in events]
                await asyncio.gather(*tasks)

                await asyncio.sleep(3)
            except Exception as e:
                print("Erro na execução:", e)
                await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        print("Bot inicializando...")
        asyncio.run(monitor_all_games())
    except Exception as e:
        print("Erro fatal ao iniciar o bot:", e)