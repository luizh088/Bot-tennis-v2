import os
import asyncio
import aiohttp
import pprint
from telegram import Bot

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
bot = Bot(token=BOT_TOKEN)

# -------------- BLOQUEIO GLOBAL --------------
global_current_lock = None  # (event_id, set_number, game_number)
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

    # Filtra torneios
    tournament_category = event["tournament"]["category"]["slug"]
    if tournament_category not in ["atp", "challenger"]:
        return

    # Filtra partidas de simples
    if event["homeTeam"]["type"] != 1 or event["awayTeam"]["type"] != 1:
        return

    event_id = event["id"]
    home_name = event["homeTeam"]["shortName"]
    away_name = event["awayTeam"]["shortName"]
    game_slug = f"{home_name} x {away_name}"

    point_data = await fetch_point_by_point(session, event_id)
    if "pointByPoint" not in point_data or not point_data["pointByPoint"]:
        return

    # --------------------------------------------
    # A) Impressão de debug: todo pointByPoint
    # --------------------------------------------
    sets_data = point_data["pointByPoint"]
    print(f"\n[DEBUG] event_id={event_id}, {game_slug}")
    print(f"[DEBUG] sets_data length = {len(sets_data)}")
    for idx, s in enumerate(sets_data):
        set_num = s.get("number")
        set_status = s.get("status")  # por ex. "finished", "inprogress"
        print(f"   [DEBUG] idx={idx}, set_number={set_num}, status={set_status}, #games={len(s.get('games', []))}")

    # --------------------------------------------
    # B) Aqui está a parte crucial: qual set escolhemos?
    #    (Muitos usam 'sets_data[-1]' achando que é o set em andamento)
    # --------------------------------------------
    # Exemplo: pegando SEMPRE o último do array
    current_set = sets_data[-1]

    # Log de debug do set escolhido
    print("[DEBUG] Chosen set (last in array):")
    pprint.pprint(current_set)

    # Se não tiver games, sair
    if not current_set.get("games"):
        print("[DEBUG] O set escolhido não possui 'games'.")
        return

    # --------------------------------------------
    # C) Pegando o último game do set escolhido.
    # --------------------------------------------
    current_game = current_set["games"][-1]

    # Impressão de debug do game escolhido
    print("[DEBUG] Chosen game (last in chosen set):")
    pprint.pprint(current_game)

    if not current_game.get("points"):
        print("[DEBUG] current_game não tem 'points'.")
        return

    # Identificando set_number
    set_number = current_set["number"] if "number" in current_set else len(sets_data)
    current_game_number = current_game["game"]

    serving = current_game["score"]["serving"]
    server_name = home_name if serving == 1 else away_name

    # Checando se game terminou
    game_finished = (
        "scoring" in current_game["score"]
        and current_game["score"]["scoring"] != -1
    )
    game_id = (event_id, set_number, current_game_number)

    # 1) Se o game terminou, checa se é o do lock
    if game_finished:
        if global_current_lock == game_id:
            if lost_first_two_info:
                winner = current_game["score"]["scoring"]
                data = lost_first_two_info
                if winner == data["server"]:
                    msg = (
                        f"✅ {data['server_name']} PERDEU 2 pontos e se recuperou, "
                        f"vencendo o game ({game_slug}, set={set_number}, game={current_game_number})."
                    )
                else:
                    msg = (
                        f"❌ {data['server_name']} PERDEU 2 pontos e acabou derrotado "
                        f"({game_slug}, set={set_number}, game={current_game_number})."
                    )
                await bot.send_message(chat_id=CHAT_ID, text=msg)
                print("[FINAL de game]:", msg)

            # Libera
            global_current_lock = None
            lost_first_two_info = {}
            print("[DEBUG] Liberando lock global (game terminou).")
        return

    # 2) Se o game não terminou, mas há um lock, sai
    if global_current_lock is not None:
        print("[DEBUG] Lock global ativo, não notificaremos esse game.")
        return

    # 3) Se não há lock, checa tie-break
    if current_set.get("tieBreak") is True:
        print("[DEBUG] É tie-break, ignorando.")
        return

    points = current_game["points"]
    if len(points) < 2:
        print("[DEBUG] Ainda não há 2 pontos disputados.")
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
        msg = (
            f"⚠️ {server_name} perdeu os dois primeiros pontos do game "
            f"({game_slug}, set={set_number}, game={current_game_number})."
        )
        await bot.send_message(chat_id=CHAT_ID, text=msg)
        print("[INÍCIO de Game]:", msg)

        global_current_lock = game_id
        lost_first_two_info = {
            "server": serving,
            "server_name": server_name
        }
        print(f"[DEBUG] Lock global ATIVADO: {global_current_lock}")

async def monitor_all_games():
    await bot.send_message(chat_id=CHAT_ID, text="✅ Bot iniciado (com DEBUG)!")
    print("Bot iniciado, mensagem de debug enviada ao Telegram.")

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                live_events = await fetch_live_events(session)
                events = live_events.get("events", [])
                print(f"\n[DEBUG] -> Número de jogos ao vivo: {len(events)}")

                tasks = [process_game(session, e) for e in events]
                await asyncio.gather(*tasks)

                await asyncio.sleep(3)
            except Exception as e:
                print("[ERROR] Erro na execução:", e)
                await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        print("Iniciando Bot com debug...")
        asyncio.run(monitor_all_games())
    except Exception as e:
        print("Erro fatal:", e)
