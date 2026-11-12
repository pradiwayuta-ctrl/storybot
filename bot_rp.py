import os
import re
import html
import json
import asyncio
import random
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    Message,
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats
)
from openai import OpenAI

# ---------------------------------------------------------
# 1. SETUP ENVIRONMENT VARIABLES & GROQ CLIENT
# ---------------------------------------------------------
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
API_ID = int(os.environ.get("TELEGRAM_API_ID", 0))
API_HASH = os.environ.get("TELEGRAM_API_HASH")
BOT_TOKEN = os.environ.get("TELEGRAM_API_KEY")

if not all([GROQ_API_KEY, API_ID, API_HASH, BOT_TOKEN]):
    raise ValueError("Pastikan GROQ_API_KEY, TELEGRAM_API_ID, TELEGRAM_API_HASH, dan TELEGRAM_API_KEY sudah diisi di Railway!")

ai_client = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=GROQ_API_KEY,
)

app = Client(
    "rp_narrator_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    parse_mode=ParseMode.HTML
)

SYSTEM_INSTRUCTION = (
    "Penulis novel fiksi interaktif bahasa Indonesia yang hidup, ekspresif, dan sinematik. "
    "Tulis narasi sudut pandang orang ketiga yang mengalir, atmosferik, dan sesuai genre. "
    "Dilarang menyebut Game Master, pemain, ronde, HP, sistem, atau menyapa Anda. "
    "Hanya gunakan nama tokoh yang diberikan. "
    "Selalu hadirkan situasi, dilema, atau kejadian yang memicu interaksi antar tokoh secara alami sesuai pilihan tindakan mereka sendiri."
)

GENRE_LIST = {
    "fantasy": "Fantasy",
    "romance": "Romance",
    "comedy": "Comedy",
    "thriller": "Thriller",
    "scifi": "Sci-Fi",
    "mystery": "Mystery",
    "horror": "Horror",
    "historical": "Historical",
    "action": "Action"
}

# ---------------------------------------------------------
# 2. STATE ROOM MEMORY
# ---------------------------------------------------------
rooms = {}

def get_room(chat_id):
    if chat_id not in rooms:
        rooms[chat_id] = {
            "status": "IDLE",       # IDLE | LOBBY | PLAYING
            "genre": "Fantasy",
            "topic": "Petualangan di dunia pedang dan sihir",
            "players": {},          # {user_id: character_name}
            "hp": {},               # {user_id: int (0-100)}
            "turn_order": [],
            "current_turn_index": 0,
            "round_count": 0,
            "round_actions": [],
            "history": [],
            "full_story": [],
            "lobby_msg_id": None,
            "timer_task": None
        }
    return rooms[chat_id]

def get_dynamic_pacing_cue(round_num, genre):
    g = genre.lower()
    if g == "romance":
        cues = [
            "Hadirkan momen canggung, tatapan tak terduga, atau situasi yang memancing obrolan emosional antar tokoh.",
            "Munculkan salah paham halus, rahasia kecil, atau keadaan yang memaksa dua tokoh saling bergantung.",
            "Hadirkan kejutan atau dilema perasaan yang menguji kedekatan dan kejujuran antar tokoh.",
            "Tingkatkan intensitas emosi dan ketegangan hati dari setiap keputusan yang mereka ambil."
        ]
    elif g == "comedy":
        cues = [
            "Munculkan kekacauan kecil atau kesalahpahaman konyol yang memancing para tokoh saling bereaksi.",
            "Buat situasi makin absurd dan di luar rencana sehingga tiap tokoh harus merespons spontan.",
            "Hadirkan kejutan kocak atau kejadian tak terduga yang bikin suasana makin ramai dan hidup.",
            "Puncak kekacauan komedi yang merespons langsung tingkah laku unik masing-masing tokoh."
        ]
    else:
        cues = [
            "Hadirkan petunjuk baru atau ancaman lingkungan yang memancing para tokoh berdiskusi dan mengambil sikap.",
            "Sisipkan dilema atau perbedaan kepentingan yang memicu perdebatan, kerja sama, atau gesekan antar tokoh.",
            "Kejutkan alur dengan plot twist—rahasia terungkap, jalan buntu, atau pihak tak terduga muncul.",
            "Tingkatkan taruhan cerita sehingga setiap pilihan tindakan tokoh membawa dampak nyata."
        ]
    return cues[(round_num - 1) % len(cues)]

def build_lobby_text(room):
    players_str = ", ".join(html.escape(name) for name in room["players"].values()) or "<i>Belum ada yang bergabung</i>"
    return (
        f"🏛️ <b>ARENA PENDAFTARAN CERITA</b>\n\n"
        f"🎭 <b>Genre:</b> {html.escape(room['genre'])}\n"
        f"📌 <b>Topik:</b> {html.escape(room['topic'])}\n"
        f"👥 <b>Tokoh Terdaftar:</b> {players_str}\n\n"
        f"💡 <b>Atur Sebelum Mulai:</b>\n"
        f"• Tekan <b>Join Game</b> untuk mendaftarkan diri\n"
        f"• Ketik <code>/nama NamaTokoh</code> untuk ganti nama panggilan\n"
        f"• Ketik <code>/topik premis cerita</code> untuk ubah alur awal"
    )

def build_lobby_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚔️ Join Game", callback_data="join_rp"),
            InlineKeyboardButton("🚪 Leave", callback_data="leave_rp")
        ],
        [
            InlineKeyboardButton("🎭 Pilih Genre", callback_data="open_genre_menu"),
            InlineKeyboardButton("🎲 Acak Giliran", callback_data="shuffle_players")
        ],
        [
            InlineKeyboardButton("📖 Rules", callback_data="show_rules"),
            InlineKeyboardButton("⚡ Cara Main", callback_data="show_features")
        ],
        [
            InlineKeyboardButton("🚀 Mulai Cerita", callback_data="start_story")
        ]
    ])

def build_genre_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🐉 Fantasy", callback_data="setgenre_fantasy"),
            InlineKeyboardButton("💕 Romance", callback_data="setgenre_romance"),
            InlineKeyboardButton("😂 Comedy", callback_data="setgenre_comedy")
        ],
        [
            InlineKeyboardButton("🔪 Thriller", callback_data="setgenre_thriller"),
            InlineKeyboardButton("🚀 Sci-Fi", callback_data="setgenre_scifi"),
            InlineKeyboardButton("🕵️ Mystery", callback_data="setgenre_mystery")
        ],
        [
            InlineKeyboardButton("👻 Horror", callback_data="setgenre_horror"),
            InlineKeyboardButton("👑 Historical", callback_data="setgenre_historical"),
            InlineKeyboardButton("⚔️ Action", callback_data="setgenre_action")
        ],
        [
            InlineKeyboardButton("🔙 Kembali ke Lobby", callback_data="back_to_dashboard")
        ]
    ])

# ---------------------------------------------------------
# 3. HELPER GROQ AUTO-MODEL DISCOVERY (HEMAT TOKEN)
# ---------------------------------------------------------
def get_active_groq_models():
    try:
        models_page = ai_client.models.list()
        excluded_keywords = ["whisper", "tts", "guard", "vision", "embedding", "compound"]
        active_models = [
            m.id for m in models_page.data
            if not any(kw in m.id.lower() for kw in excluded_keywords)
        ]
        active_models.sort(key=lambda x: (0 if "70b" in x or "versatile" in x else 1))
        return active_models
    except Exception as e:
        print(f"Gagal mengambil daftar model Groq: {e}")
        return ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]

async def generate_groq_json(prompt, max_tokens=280):
    loop = asyncio.get_running_loop()
    models_to_use = await loop.run_in_executor(None, get_active_groq_models)
    last_error = None

    for model_name in models_to_use:
        try:
            completion = await loop.run_in_executor(
                None,
                lambda m=model_name: ai_client.chat.completions.create(
                    model=m,
                    messages=[
                        {"role": "system", "content": SYSTEM_INSTRUCTION + " Output JSON valid."},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    max_tokens=max_tokens,
                    temperature=0.8
                )
            )
            content = completion.choices[0].message.content
            if content and content.strip():
                return json.loads(content.strip())
        except Exception as e:
            last_error = e
            print(f"Model {model_name} gagal ({e}), beralih ke model aktif berikutnya...")
            await asyncio.sleep(0.5)
            continue

    raise last_error or Exception("Tidak ada model Groq aktif yang merespons.")

async def generate_groq_narrative(prompt, max_tokens=280):
    loop = asyncio.get_running_loop()
    models_to_use = await loop.run_in_executor(None, get_active_groq_models)
    last_error = None

    for model_name in models_to_use:
        try:
            completion = await loop.run_in_executor(
                None,
                lambda m=model_name: ai_client.chat.completions.create(
                    model=m,
                    messages=[
                        {"role": "system", "content": SYSTEM_INSTRUCTION},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=max_tokens,
                    temperature=0.8
                )
            )
            content = completion.choices[0].message.content
            if content and content.strip():
                return content.strip()
        except Exception as e:
            last_error = e
            await asyncio.sleep(0.5)
            continue

    raise last_error or Exception("Tidak ada model Groq aktif yang merespons.")

# ---------------------------------------------------------
# 4. HELPER FUNCTIONS & TURN MANAGER
# ---------------------------------------------------------
def cancel_afk_timer(room):
    if room["timer_task"] and not room["timer_task"].done():
        room["timer_task"].cancel()
        room["timer_task"] = None

def reset_room_state(room):
    cancel_afk_timer(room)
    room["status"] = "IDLE"
    room["players"].clear()
    room["hp"].clear()
    room["turn_order"].clear()
    room["history"].clear()
    room["full_story"].clear()
    room["round_actions"].clear()
    room["round_count"] = 0

async def start_afk_timer(client, chat_id, user_id, timeout=120):
    await asyncio.sleep(timeout)
    room = get_room(chat_id)

    if room["status"] == "PLAYING":
        active_user_id = room["turn_order"][room["current_turn_index"]]
        if active_user_id == user_id:
            skipped_player_name = room["players"][user_id]
            room["round_actions"].append(f"{skipped_player_name}: diam mengamati")
            await client.send_message(
                chat_id,
                f"⌛ <b>Waktu Habis!</b> Giliran <b>{html.escape(skipped_player_name)}</b> dilewati otomatis."
            )

            room["current_turn_index"] += 1
            await prompt_next_turn(client, chat_id)

async def prompt_next_turn(client, chat_id):
    room = get_room(chat_id)
    cancel_afk_timer(room)

    while room["current_turn_index"] < len(room["turn_order"]):
        candidate_id = room["turn_order"][room["current_turn_index"]]
        if room["hp"].get(candidate_id, 100) <= 0:
            dead_name = room["players"][candidate_id]
            room["round_actions"].append(f"{dead_name}: terkapar tak sadarkan diri")
            room["current_turn_index"] += 1
        else:
            break

    if room["current_turn_index"] >= len(room["turn_order"]):
        await process_end_of_round(client, chat_id)
        return

    current_user_id = room["turn_order"][room["current_turn_index"]]
    player_name = room["players"][current_user_id]
    current_hp = room["hp"].get(current_user_id, 100)

    await client.send_message(
        chat_id,
        f"🎯 <b>GILIRAN:</b> <a href=\"tg://user?id={current_user_id}\">{html.escape(player_name)}</a>\n"
        f"❤️ <b>Sisa Nyawa:</b> {current_hp}/100 HP\n\n"
        f"✍️ <b>Format Menulis:</b>\n"
        f"• Dialog: <code>\"Halo semuanya!\"</code>\n"
        f"• Aksi: <code>;berjalan mendekat;</code>\n"
        f"• Gabungan: <code>\"Ayo maju!\" ;menghunus pedang;</code>\n\n"
        f"⏱️ <i>Batas 2 menit • /skip lewati • /status cek HP</i>"
    )

    room["timer_task"] = asyncio.create_task(start_afk_timer(client, chat_id, current_user_id, timeout=120))

async def process_end_of_round(client, chat_id):
    room = get_room(chat_id)
    cancel_afk_timer(room)

    room["round_count"] += 1
    pacing_cue = get_dynamic_pacing_cue(room["round_count"], room["genre"])

    status_msg = await client.send_message(chat_id, "✒️ <i>Menulis kelanjutan bab cerita...</i>")

    combined_actions = "; ".join(room["round_actions"])
    recent_context = "\n".join(room["history"][-2:])
    tokoh_hp = ", ".join([
        f"{room['players'][uid]}({room['hp'][uid]}HP)"
        for uid in room["turn_order"]
    ])

    prompt_round = (
        f"Genre:{room['genre']}|Topik:{room['topic']}|Bab:{room['round_count']}\n"
        f"Lalu:{recent_context}\n"
        f"Tokoh:{tokoh_hp}\n"
        f"Aksi Bab Ini:{combined_actions}\n"
        f"Arahan Alur: {pacing_cue}\n\n"
        f"Tugas:\n"
        f"1. Tulis lanjutan novel 1 paragraf (3-4 kalimat hidup sesuai genre {room['genre']}) yang merespons aksi tiap tokoh sekaligus memicu interaksi baru di antara mereka.\n"
        f"2. Hitung perubahan HP tiap tokoh jika ada benturan/bahaya/pemulihan (0 jika aman, negatif jika terluka, positif jika pulih).\n"
        f"Output JSON valid tanpa markdown:\n"
        f'{{"n":"paragraf novel","h":{{"NamaTokoh":-15}}}}'
    )

    try:
        data = await generate_groq_json(prompt_round, max_tokens=280)
        narasi_baru = html.escape(data.get("n", "Suasana semakin intens saat langkah mereka memicu kejadian tak terduga."))
        room["history"].append(narasi_baru)
        room["full_story"].append(narasi_baru)

        if len(room["history"]) > 4:
            room["history"] = room["history"][-4:]

        hp_log = []
        hp_updates = data.get("h", {})
        name_to_id = {v.lower(): k for k, v in room["players"].items()}

        for p_name, diff_val in hp_updates.items():
            uid = name_to_id.get(p_name.lower())
            if uid and uid in room["hp"]:
                try:
                    hp_diff = int(diff_val)
                except (ValueError, TypeError):
                    hp_diff = 0

                if hp_diff != 0:
                    room["hp"][uid] = max(0, min(100, room["hp"][uid] + hp_diff))
                    tanda = "+" if hp_diff > 0 else ""
                    hp_log.append(f"• <b>{html.escape(room['players'][uid])}:</b> {tanda}{hp_diff} HP → Sisa {room['hp'][uid]} HP")

        room["round_actions"].clear()
        room["current_turn_index"] = 0

        await status_msg.delete()

        final_msg = f"📖 <b>BAB {room['round_count']} • LANJUTAN KISAH</b>\n\n{narasi_baru}"
        if hp_log:
            final_msg += "\n\n🩸 <b>PERUBAHAN NYAWA</b>\n" + "\n".join(hp_log)

        await client.send_message(chat_id, final_msg)

        alive_players = [
            uid for uid in room["turn_order"]
            if room["hp"].get(uid, 0) > 0
        ]

        if not alive_players:
            epilog_prompt = (
                f"Genre:{room['genre']}|Akhir:{narasi_baru}\n"
                f"Semua tokoh tumbang. Tulis epilog penutup novel 2 kalimat sesuai genre {room['genre']}."
            )
            try:
                epilog_raw = await generate_groq_narrative(epilog_prompt, max_tokens=110)
                epilog_text = html.escape(epilog_raw)
            except Exception:
                epilog_text = "Tirai akhirnya tertutup atas perjalanan mereka hari ini, menyisakan kisah yang tak terlupakan."

            await client.send_message(
                chat_id,
                f"💀 <b>EPILOG: AKHIR PERJALANAN</b>\n\n"
                f"{epilog_text}\n\n"
                f"<i>Seluruh tokoh telah tumbang. Ketik <code>/start_rp</code> untuk memulai kisah baru.</i>"
            )
            reset_room_state(room)
            return

        await prompt_next_turn(client, chat_id)

    except Exception as e:
        print(f"Error Generate Round: {e}")
        await status_msg.edit_text(f"⚠️ <b>Gagal merangkai bab cerita:</b> <code>{html.escape(str(e))}</code>\n\nKetik /skip untuk melanjutkan.")

# ---------------------------------------------------------
# 5. HANDLER CHAT PRIBADI (PC)
# ---------------------------------------------------------

@app.on_message(filters.private)
async def start_private(client, message: Message):
    bot_user = await client.get_me()
    first_name = html.escape(message.from_user.first_name or "Petualang")

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📖 Rules Bermain", callback_data="show_rules_pc"),
            InlineKeyboardButton("⚡ Cara Main & Fitur", callback_data="show_features_pc")
        ],
        [
            InlineKeyboardButton("⚔️ Masukkan Bot ke Grup", url=f"https://t.me/{bot_user.username}?startgroup=true")
        ]
    ])

    text = (
        f"🛡️ <b>ROLEPLAY NOVEL BOT</b>\n\n"
        f"Halo, <b>{first_name}</b>! Ubah obrolan roleplay di grupmu menjadi cerita novel interaktif dengan alur yang merespons setiap aksi dan dialog kalian.\n\n"
        f"🚀 <b>Langkah Memulai:</b>\n"
        f"1. Tambahkan bot ini ke <b>Grup Telegram</b> lalu jadikan <b>Admin</b>\n"
        f"2. Ketik <code>/start_rp</code> di grup untuk membuka Lobby\n"
        f"3. Tekan <b>Join Game</b>, pilih <b>Genre</b>, atur <code>/topik</code> & <code>/nama</code>\n"
        f"4. Tekan <b>Mulai Cerita</b> dan bangun alur cerita bebas sesuai karaktermu!"
    )
    await message.reply(text, reply_markup=keyboard)

# ---------------------------------------------------------
# 6. HANDLER GROUP & LOBBY
# ---------------------------------------------------------

@app.on_message(filters.command(["start_rp", "start"]) & filters.group)
async def start_rp_lobby(client, message: Message):
    room = get_room(message.chat.id)
    if room["status"] != "IDLE":
        await message.reply("⚠️ Sesi cerita masih berjalan atau Lobby sedang terbuka.")
        return

    reset_room_state(room)
    room["status"] = "LOBBY"

    lobby_msg = await message.reply(build_lobby_text(room), reply_markup=build_lobby_keyboard())

    try:
        await lobby_msg.pin(disable_notification=False)
        room["lobby_msg_id"] = lobby_msg.id
    except Exception:
        pass

# ---------------------------------------------------------
# 7. CALLBACK BUTTON HANDLERS
# ---------------------------------------------------------

@app.on_callback_query(filters.regex("^open_genre_menu$"))
async def open_genre_selection(client, callback: CallbackQuery):
    room = get_room(callback.message.chat.id)
    if room["status"] != "LOBBY":
        await callback.answer("Genre hanya bisa diubah saat di Lobby!", show_alert=True)
        return

    await callback.message.edit_text(
        f"🎭 <b>PILIH GENRE NOVEL</b>\n\n"
        f"Genre aktif sekarang: <b>{html.escape(room['genre'])}</b>\n"
        f"Silakan pilih genre baru di bawah ini:",
        reply_markup=build_genre_keyboard()
    )

@app.on_callback_query(filters.regex("^setgenre_"))
async def select_genre_button(client, callback: CallbackQuery):
    room = get_room(callback.message.chat.id)
    if room["status"] != "LOBBY":
        await callback.answer("Genre hanya bisa diubah saat di Lobby!", show_alert=True)
        return

    genre_key = callback.data.replace("setgenre_", "")
    chosen_genre = GENRE_LIST.get(genre_key, "Fantasy")
    room["genre"] = chosen_genre

    await callback.answer(f"Genre dipilih: {chosen_genre}", show_alert=False)
    await callback.message.edit_text(build_lobby_text(room), reply_markup=build_lobby_keyboard())

@app.on_callback_query(filters.regex("^shuffle_players$"))
async def shuffle_lobby_players(client, callback: CallbackQuery):
    room = get_room(callback.message.chat.id)
    if room["status"] != "LOBBY":
        await callback.answer("Urutan hanya bisa diacak saat di Lobby!", show_alert=True)
        return

    if len(room["players"]) < 2:
        await callback.answer("Minimal harus ada 2 tokoh untuk mengacak giliran!", show_alert=True)
        return

    items = list(room["players"].items())
    random.shuffle(items)
    room["players"] = dict(items)

    await callback.answer("🎲 Urutan giliran berhasil diacak!", show_alert=False)
    await callback.message.edit_text(build_lobby_text(room), reply_markup=build_lobby_keyboard())

@app.on_callback_query(filters.regex("^(show_rules|show_features|back_to_dashboard|show_rules_pc|show_features_pc|back_to_pc)$"))
async def handle_info_buttons(client, callback: CallbackQuery):
    data = callback.data

    if data == "show_rules_pc":
        rules_text = (
            "📜 <b>ATURAN BERMAIN</b>\n\n"
            "1️⃣ <b>Kebebasan Karakter</b>\n"
            "Tidak ada peran atau misi yang membatasi gaya bermainmu. Kamu bebas menentukan sendiri watak tokohmu—baik, netral, licik, maupun kocak—lewat dialog dan tindakan di tiap giliran.\n\n"
            "2️⃣ <b>Alur Dinamis & Pemicu Interaksi</b>\n"
            "Di setiap akhir ronde, AI merangkai lanjutan cerita yang merespons tindakan semua tokoh sekaligus memunculkan konflik atau situasi baru agar interaksi tetap hidup.\n\n"
            "3️⃣ <b>Nyawa Tokoh (100 HP)</b>\n"
            "Setiap tokoh memulai dengan 100 HP. Keputusan berbahaya atau benturan fisik bisa mengurangi HP.\n\n"
            "4️⃣ <b>Batas Waktu & Penutupan</b>\n"
            "Batas waktu tiap giliran 2 menit. Ketik <code>/selesai_rp</code> kapan saja untuk menutup cerita dan mendapatkan rangkuman novel utuh."
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="back_to_pc")]])
        await callback.message.edit_text(rules_text, reply_markup=keyboard)

    elif data == "show_features_pc":
        features_text = (
            "⚡ <b>CARA MAIN & DAFTAR PERINTAH</b>\n\n"
            "✍️ <b>Format Menulis Giliran:</b>\n"
            "• <b>Dialog:</b> <code>\"Jangan buka pintu itu!\"</code>\n"
            "• <b>Aksi:</b> <code>;menarik lengan Haru menjauh;</code>\n"
            "• <b>Gabungan:</b> <code>\"Awas jebakan!\" ;mendorong Javen ke dinding;</code>\n\n"
            "🛠️ <b>Perintah di Grup:</b>\n"
            "• <code>/start_rp</code> — Buka Lobby pendaftaran\n"
            "• <code>/nama NamaTokoh</code> — Ganti nama panggilanmu di Lobby\n"
            "• <code>/topik premis cerita</code> — Atur latar awal cerita di Lobby\n"
            "• <code>/status</code> — Cek sisa HP semua tokoh\n"
            "• <code>/skip</code> — Lewati giliran aktif\n"
            "• <code>/selesai_rp</code> — Akhiri cerita & buat rangkuman utuh"
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="back_to_pc")]])
        await callback.message.edit_text(features_text, reply_markup=keyboard)

    elif data == "back_to_pc":
        bot_user = await client.get_me()
        first_name = html.escape(callback.from_user.first_name or "Petualang")
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📖 Rules Bermain", callback_data="show_rules_pc"),
                InlineKeyboardButton("⚡ Cara Main & Fitur", callback_data="show_features_pc")
            ],
            [
                InlineKeyboardButton("⚔️ Masukkan Bot ke Grup", url=f"https://t.me/{bot_user.username}?startgroup=true")
            ]
        ])
        text = (
            f"🛡️ <b>ROLEPLAY NOVEL BOT</b>\n\n"
            f"Halo, <b>{first_name}</b>! Ubah obrolan roleplay di grupmu menjadi cerita novel interaktif dengan alur yang merespons setiap aksi dan dialog kalian.\n\n"
            f"🚀 <b>Langkah Memulai:</b>\n"
            f"1. Tambahkan bot ini ke <b>Grup Telegram</b> lalu jadikan <b>Admin</b>\n"
            f"2. Ketik <code>/start_rp</code> di grup untuk membuka Lobby\n"
            f"3. Tekan <b>Join Game</b>, pilih <b>Genre</b>, atur <code>/topik</code> & <code>/nama</code>\n"
            f"4. Tekan <b>Mulai Cerita</b> dan bangun alur cerita bebas sesuai karaktermu!"
        )
        await callback.message.edit_text(text, reply_markup=keyboard)

    elif data == "show_rules":
        rules_text = (
            "📜 <b>ATURAN BERMAIN</b>\n\n"
            "1️⃣ <b>RP Bebas:</b> Kamu bebas menentukan sifat dan pilihan tokohmu sendiri (baik, netral, jahat, atau kocak) di sepanjang cerita.\n"
            "2️⃣ <b>Nyawa Tokoh:</b> Setiap tokoh memulai dengan 100 HP.\n"
            "3️⃣ <b>Batas Waktu:</b> 2 menit per giliran (otomatis terlewati jika AFK).\n"
            "4️⃣ <b>Arsip Cerita:</b> Ketik <code>/selesai_rp</code> untuk menutup cerita dan mendapatkan rangkuman novel utuh."
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali ke Lobby", callback_data="back_to_dashboard")]])
        await callback.message.edit_text(rules_text, reply_markup=keyboard)

    elif data == "show_features":
        features_text = (
            "⚡ <b>CARA MENULIS GILIRAN</b>\n\n"
            "• <b>Dialog:</b> <code>\"ucapan tokoh\"</code>\n"
            "• <b>Aksi:</b> <code>;gerakan atau tindakan;</code>\n"
            "• <b>Gabungan:</b> <code>\"Awas!\" ;mendorong Haru menjauh;</code>\n\n"
            "🛠️ <b>Perintah Cepat:</b>\n"
            "• <code>/nama NamaTokoh</code> — Ganti nama di Lobby\n"
            "• <code>/topik premis cerita</code> — Ubah topik di Lobby\n"
            "• <code>/status</code> — Cek sisa HP tokoh\n"
            "• <code>/skip</code> — Lewati giliran\n"
            "• <code>/selesai_rp</code> — Tutup & rangkum kisah"
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali ke Lobby", callback_data="back_to_dashboard")]])
        await callback.message.edit_text(features_text, reply_markup=keyboard)

    elif data == "back_to_dashboard":
        room = get_room(callback.message.chat.id)
        await callback.message.edit_text(build_lobby_text(room), reply_markup=build_lobby_keyboard())

# ---------------------------------------------------------
# 8. COMMAND & GAME HANDLERS
# ---------------------------------------------------------

@app.on_message(filters.command("nama") & filters.group)
async def set_character_name(client, message: Message):
    room = get_room(message.chat.id)
    if room["status"] != "LOBBY":
        await message.reply("⚠️ Nama tokoh hanya bisa diganti saat masih di Lobby!")
        return

    user_id = message.from_user.id
    if user_id not in room["players"]:
        await message.reply("⚠️ Tekan tombol <b>Join Game</b> dulu sebelum mengganti nama tokoh!")
        return

    if len(message.command) < 2:
        await message.reply("Contoh: <code>/nama Ken</code> atau <code>/nama Javen</code>")
        return

    custom_name = " ".join(message.command[1:]).strip()[:20]

    if any(existing.lower() == custom_name.lower() and uid != user_id for uid, existing in room["players"].items()):
        await message.reply(f"⚠️ Nama <b>{html.escape(custom_name)}</b> sudah dipakai pemain lain. Pilih nama lain!")
        return

    room["players"][user_id] = custom_name
    await message.reply(f"🪪 <b>Nama Tokoh Diubah:</b> <code>{html.escape(custom_name)}</code>", quote=True)

    if room["lobby_msg_id"]:
        try:
            await client.edit_message_text(
                message.chat.id,
                room["lobby_msg_id"],
                build_lobby_text(room),
                reply_markup=build_lobby_keyboard()
            )
        except Exception:
            pass

@app.on_message(filters.command("topik") & filters.group)
async def set_topic(client, message: Message):
    room = get_room(message.chat.id)
    if room["status"] != "LOBBY":
        await message.reply("⚠️ Topik cerita hanya bisa diubah saat masih di Lobby!")
        return

    if len(message.command) < 2:
        await message.reply("Contoh: <code>/topik transmigrasi ke dalam tubuh antagonis akademi sihir</code>")
        return

    new_topic = " ".join(message.command[1:])
    room["topic"] = new_topic
    await message.reply(f"📌 <b>Topik Cerita Diubah:</b> <code>{html.escape(new_topic)}</code>")

    if room["lobby_msg_id"]:
        try:
            await client.edit_message_text(
                message.chat.id,
                room["lobby_msg_id"],
                build_lobby_text(room),
                reply_markup=build_lobby_keyboard()
            )
        except Exception:
            pass

@app.on_message(filters.command("status") & filters.group)
async def check_player_status(client, message: Message):
    room = get_room(message.chat.id)
    if room["status"] != "PLAYING":
        await message.reply("⚠️ Belum ada cerita yang aktif saat ini.")
        return

    lines = ["❤️ <b>KONDISI NYAWA TOKOH</b>\n"]
    for uid in room["turn_order"]:
        name = html.escape(room["players"][uid])
        hp = room["hp"].get(uid, 100)
        kondisi = "💀 Tumbang" if hp <= 0 else f"❤️ {hp}/100 HP"
        lines.append(f"• <b>{name}:</b> {kondisi}")

    await message.reply("\n".join(lines))

@app.on_callback_query(filters.regex("^(join_rp|leave_rp|start_story)$"))
async def handle_lobby_buttons(client, callback: CallbackQuery):
    chat_id = callback.message.chat.id
    user = callback.from_user
    room = get_room(chat_id)

    if callback.data == "join_rp":
        if user.id not in room["players"]:
            room["players"][user.id] = user.first_name
            room["hp"][user.id] = 100
            await callback.answer(f"{user.first_name} berhasil bergabung!", show_alert=False)
            await callback.message.edit_text(build_lobby_text(room), reply_markup=build_lobby_keyboard())
        else:
            await callback.answer("Kamu sudah terdaftar di Lobby!", show_alert=False)

    elif callback.data == "leave_rp":
        if user.id in room["players"]:
            del room["players"][user.id]
            room["hp"].pop(user.id, None)
            await callback.answer("Kamu keluar dari pendaftaran.", show_alert=False)
            await callback.message.edit_text(build_lobby_text(room), reply_markup=build_lobby_keyboard())
        else:
            await callback.answer("Kamu belum terdaftar.", show_alert=False)

    elif callback.data == "start_story":
        if len(room["players"]) < 1:
            await callback.answer("Minimal harus ada 1 tokoh yang bergabung!", show_alert=True)
            return

        if room["lobby_msg_id"]:
            try:
                await client.unpin_chat_message(chat_id, room["lobby_msg_id"])
            except Exception:
                pass

        room["status"] = "PLAYING"
        room["turn_order"] = list(room["players"].keys())
        room["current_turn_index"] = 0
        room["round_count"] = 0

        status_msg = await callback.message.edit_text("✒️ <i>Menulis bab pembuka kisah...</i>")

        player_names = ", ".join(room["players"].values())
        prompt_pembuka = (
            f"Tulis prolog novel 2 paragraf sesuai genre {room['genre']}. Topik:{room['topic']}|Tokoh:{player_names}.\n"
            f"P1: Awali 'Selamat datang di dunia...', jelaskan latar tempat, premis topik, serta posisi tiap tokoh di dalam cerita.\n"
            f"P2: Munculkan kejadian atau situasi mendesak di depan mata yang langsung memicu interaksi antar tokoh."
        )

        try:
            raw_text = await generate_groq_narrative(prompt_pembuka, max_tokens=260)
            narasi_awal = html.escape(raw_text)
            room["history"].append(narasi_awal)
            room["full_story"].append(narasi_awal)
        except Exception as e:
            print(f"Error Start Story: {e}")
            room["status"] = "LOBBY"
            error_text = (
                f"{build_lobby_text(room)}\n\n"
                f"⚠️ <b>Gagal memuat alur cerita:</b> <code>{html.escape(str(e))}</code>\n"
                f"Silakan tekan tombol <b>Mulai Cerita</b> lagi."
            )
            await status_msg.edit_text(error_text, reply_markup=build_lobby_keyboard())
            return

        await status_msg.delete()

        await callback.message.reply(
            f"📖 <b>PROLOG • {html.escape(room['genre'].upper())}</b>\n"
            f"<i>Topik: {html.escape(room['topic'])}</i>\n\n"
            f"{narasi_awal}"
        )
        await prompt_next_turn(client, chat_id)

@app.on_message(filters.group & filters.text & ~filters.command(["start_rp", "start", "selesai_rp", "skip", "topik", "nama", "status"]))
async def handle_turn_action(client, message: Message):
    chat_id = message.chat.id
    room = get_room(chat_id)

    if room["status"] != "PLAYING":
        return

    active_user_id = room["turn_order"][room["current_turn_index"]]
    if message.from_user.id != active_user_id:
        return

    text = message.text.strip()
    normalized_text = text.replace("“", '"').replace("”", '"')

    dialogs = [d.strip() for d in re.findall(r'"([^"]+)"', normalized_text) if d.strip()]
    actions = [a.strip() for a in re.findall(r';([^;]+);', normalized_text) if a.strip()]

    unclosed_quote = normalized_text.count('"') % 2 != 0
    unclosed_semicolon = normalized_text.count(';') % 2 != 0

    if unclosed_quote or unclosed_semicolon or (not dialogs and not actions):
        await message.reply(
            "❌ <b>Format Belum Tepat</b>\n"
            "Pastikan tanda kutip dan titik koma dibuka serta ditutup dengan benar.\n\n"
            "✍️ <b>Contoh Penulisan:</b>\n"
            "• Dialog: <code>\"Aku harus segera kembali ke akademi\"</code>\n"
            "• Aksi: <code>;bergegas membawa tas;</code>\n"
            "• Gabungan: <code>\"Aku harus segera kembali ke akademi\" ;bergegas membawa tas;</code>\n\n"
            "<i>Silakan kirim ulang giliranmu.</i>",
            quote=True
        )
        return

    cancel_afk_timer(room)
    player_name = room["players"][message.from_user.id]

    parsed_parts = []
    if dialogs:
        parsed_parts.append(f'"{ " ".join(dialogs) }"')
    if actions:
        parsed_parts.append(f'({ " ".join(actions) })')

    formatted_action = f"{player_name}: " + " ".join(parsed_parts)
    room["round_actions"].append(formatted_action)

    await message.reply(f"✅ <b>Aksi {html.escape(player_name)} diterima!</b>", quote=True)

    room["current_turn_index"] += 1
    await prompt_next_turn(client, chat_id)

@app.on_message(filters.command("skip") & filters.group)
async def skip_turn(client, message: Message):
    chat_id = message.chat.id
    room = get_room(chat_id)

    if room["status"] != "PLAYING":
        return

    cancel_afk_timer(room)

    active_user_id = room["turn_order"][room["current_turn_index"]]
    skipped_player_name = room["players"][active_user_id]

    room["round_actions"].append(f"{skipped_player_name}: menahan diri dan mengamati")
    await message.reply(f"⏭️ Giliran <b>{html.escape(skipped_player_name)}</b> dilewati!")

    room["current_turn_index"] += 1
    await prompt_next_turn(client, chat_id)

@app.on_message(filters.command("selesai_rp") & filters.group)
async def stop_rp(client, message: Message):
    room = get_room(message.chat.id)
    if room["status"] == "IDLE":
        await message.reply("⚠️ Tidak ada cerita yang sedang aktif saat ini.")
        return

    cancel_afk_timer(room)

    if room["status"] == "PLAYING" and room["full_story"]:
        status_msg = await message.reply("📜 <i>Merangkai seluruh bab menjadi kisah novel yang utuh...</i>")

        gabungan_bab = "\n\n".join(room["full_story"])[-3000:]
        tokoh_str = ", ".join(room["players"].values())

        prompt_rekap = (
            f"Genre:{room['genre']}|Topik:{room['topic']}|Tokoh:{tokoh_str}\n"
            f"Rangkaian Kejadian:\n{gabungan_bab}\n\n"
            f"Tugas: Rangkum seluruh perjalanan cerita di atas dari awal hingga akhir menjadi 2 paragraf novel yang utuh, "
            f"mengalir, dan ditutup dengan kalimat epilog yang berkesan."
        )

        try:
            rekap_raw = await generate_groq_narrative(prompt_rekap, max_tokens=320)
            rekap_text = html.escape(rekap_raw)
        except Exception:
            rekap_text = f"{room['full_story'][0]}\n\n...\n\n{room['full_story'][-1]}"

        await status_msg.delete()
        await message.reply(
            f"📚 <b>ARSIP KISAH UTUH • {html.escape(room['genre'].upper())}</b>\n"
            f"<i>Topik: {html.escape(room['topic'])}</i>\n\n"
            f"{rekap_text}\n\n"
            f"🏆 <i>Sesi cerita selesai. Ketik <code>/start_rp</code> untuk membuka petualangan baru.</i>"
        )
    else:
        await message.reply("🏆 <b>KISAH DITUTUP</b>\nPendaftaran dibatalkan. Ketik <code>/start_rp</code> untuk membuka sesi baru.")

    reset_room_state(room)

# ---------------------------------------------------------
# 9. STARTUP & AUTO-REGISTER BOT COMMANDS
# ---------------------------------------------------------
async def main():
    await app.start()

    await app.set_bot_commands(
        [
            BotCommand("start_rp", "Buka arena pendaftaran cerita RP"),
            BotCommand("nama", "Ganti nama tokohmu di Lobby"),
            BotCommand("topik", "Tentukan topik cerita di Lobby"),
            BotCommand("status", "Cek sisa HP semua tokoh"),
            BotCommand("skip", "Lewati giliran pemain aktif"),
            BotCommand("selesai_rp", "Akhiri sesi & rangkum kisah utuh")
        ],
        scope=BotCommandScopeAllGroupChats()
    )

    await app.set_bot_commands(
        [
            BotCommand("start", "Buka menu utama & panduan bermain")
        ],
        scope=BotCommandScopeAllPrivateChats()
    )

    print("Bot RP Novel aktif (Mode RP Bebas + Pemicu Interaksi AI + Rangkuman Utuh)!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    app.run(main())
