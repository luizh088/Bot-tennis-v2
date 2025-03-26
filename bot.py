import os
import asyncio
import aiohttp
import pprint
from telegram import Bot

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
bot = Bot(token=BOT_TOKEN)

# BLOQUEIO GLOBAL
global_current_lock = None
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
    """Processa 1 jogo com logs de debug em cada verificação para entender por que retorna cedo."""
    global global_current_lock
    global lost_first_two_info

    event_id = event["id"]
    home_name = event["homeTeam"]["shortName"]
    away_name = event["awayTeam"]["shortName"]
    game_slug = f"{home_name} x {away_name}"
    tournament_category = event["tournament"]["category"]["slug"]
    home_type = event["homeTeam"]["type"]
    away_type = event["awayTeam"]["type"]

    print(f"\n[DEBUG] Analisando event_id={event_id}, {game_slug}")
    print(f"        tournament_category={tournament_category}, home_type={home_type}, away_type={away_type}")

    # 1) Filtrar torneios que não sejam atp/challenger
    if tournament_category not in ["atp", "challenger"]:
        print(f"[DEBUG] -> SKIP: {tournament_category} não é atp nem challenger.")
        return

    # 2) Filtrar partidas simples (type=1)
    if home_type != 1 or away_type != 1:
        print("[DEBUG] -> SKIP: Jogo não é de simples (type=1).")
        return

    # Se chegou aqui, passou pelos filtros de torneio e simples.
    # Vamos pegar os dados de point-by-point
    point_data = await fetch_point_by_point(session, event_id)
    if "pointByPoint" not in point_data or not point_data["pointByPoint"]:
        print("[DEBUG] -> SKIP: 'pointByPoint' vazio ou ausente para este evento.")
        return

    sets_data = point_data["pointByPoint"]
    print(f"[DEBUG] pointByPoint OK, sets_data length={len(sets_data)}")

    # Exibir todos os sets para entender
    for idx, s in enumerate(sets_data):
        print(f"   [DEBUG] idx={idx}, set_number={s.get('number')}, status={s.get('status')}, #games={len(s.get('games', []))}")

    # ------------------------------------------------
    # Tentar pegar o set "em andamento"
    # (muitas vezes é sets_data[-1], mas pode variar!)
    # Aqui assumiremos que o último é o set atual:
    # ------------------------------------------------
    current_set = sets_data[-1]
    print("[DEBUG] -> Usando o último set do array:")
    pprint.pprint(current_set)

    if not current_set.get("games"):
        print("[DEBUG] -> SKIP: Este set não tem games.")
        return

    # Pegar o último game
    current_game = current_set["games"][-1]
    print("[DEBUG] -> Último game do set escolhido:")
    pprint.pprint(current_game)

    if not current_game.get("points"):
        print("[DEBUG] -> SKIP: Este game não tem 'points'.")
        return

    set_number = current_set["number"] if "number" in current_set else len(sets_data)
    current_game_number = current_game["game"]

    # Quem saca
    serving = current_game["score"]["serving"]
    server_name = home_name if serving == 1 else away_name

    # Checa se o game terminou
    game_finished = (
        "scoring" in current_game["score"] and
        current_game["score"]["scoring"] != -1
    )
    game_id = (event_id, set_number, current_game_number)

    # (A) Se o game terminou
    if game_finished:
        print(f"[DEBUG] -> game {game_id} terminou.")
        if global_current_lock == game_id:
            print("[DEBUG] -> É o game que estava bloqueando, enviando notificação final e liberando lock...")
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
                print("[FINAL de Game]:", msg)
            global_current_lock = None
            lost_first_two_info = {}
            print("[DEBUG] Lock global liberado.")
        else:
            print("[DEBUG] -> O game terminou, mas não é o do lock, então nada a fazer.")
        return

    # (B) Se o game NÃO terminou, mas temos lock ativo, sai
    if global_current_lock is not None:
        print(f"[DEBUG] -> Há um lock ativo em {global_current_lock}, então não notificaremos nada agora.")
        return

    # (C) Checa tie-break
    if current_set.get("tieBreak") is True:
        print("[DEBUG] -> SKIP: É tie-break, ignorado.")
        return

    # (D) Checa se há ao menos 2 pontos
    points = current_game["points"]
    if len(points) < 2:
        print("[DEBUG] -> SKIP: Ainda não há 2 pontos jogados neste game.")
        return

    home_point_1 = points[0]["homePoint"]
    away_point_1 = points[0]["awayPoint"]
    home_point_2 = points[1]["homePoint"]
    away_point_2 = points[1]["awayPoint"]

    lost_first_point = ((serving == 1 and home_point_1 == "0") or
                        (serving == 2 and away_point_1 == "0"))
    lost_second_point = ((serving == 1 and home_point_2 == "0") or
                         (serving == 2 and away_point_2 == "0"))

    if lost_first_point and lost_second_point:
        print("[DEBUG] -> O sacador perdeu os dois primeiros pontos!")
        msg = (
            f"⚠️ {server_name} perdeu os DOIS primeiros pontos do game "
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
    else:
        print("[DEBUG] -> O sacador NÃO perdeu os dois primeiros pontos (outra situação).")

async def monitor_all_games():
    await bot.send_message(chat_id=CHAT_ID, text="✅ Bot iniciado (DEBUG EXTREMO)!")
    print("[DEBUG] Bot iniciado (DEBUG EXTREMO).")

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                live_events = await fetch_live_events(session)
                events = live_events.get("events", [])
                print(f"\nNúmero de jogos ao vivo: {len(events)}")

                tasks = [process_game(session, e) for e in events]
                await asyncio.gather(*tasks)

                await asyncio.sleep(3)
            except Exception as e:
                print("[ERROR]", e)
                await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        asyncio.run(monitor_all_games())
    except Exception as e:
        print("Erro fatal:", e)
