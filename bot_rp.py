import os
import re
import html
import json
import asyncio
import random
import unicodedata
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
    "Kamu adalah penulis novel bahasa Indonesia yang rapi, natural, ringkas, dan menggunakan EYD yang benar. "
    "Tulis sudut pandang orang ketiga yang mengalir dan tidak bertele-tele. "
    "Patuhi secara ketat gender setiap tokoh yang diberikan (jangan pernah tertukar antara laki-laki dan perempuan). "
    "Dilarang menulis label seperti 'Paragraf 1:' atau 'Paragraf 2:'. "
    "Dilarang menyebut Game Master, pemain, ronde, HP, sistem, atau menyapa pembaca."
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
            "players": {},          # {actor_id: character_name}
            "genders": {},          # {actor_id: "Cowok" | "Cewek"}
            "hp": {},               # {actor_id: int (0-100)}
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

def get_actor_id(obj):
    if getattr(obj, "from_user", None):
        return obj.from_user.id
    if getattr(obj, "sender_chat", None):
        return obj.sender_chat.id
    return None

def get_actor_name(obj):
    if getattr(obj, "from_user", None):
        return obj.from_user.first_name or "Tokoh"
    if getattr(obj, "sender_chat", None):
        return obj.sender_chat.title or "Tokoh"
    return "Tokoh"

def normalize_plain_text(text):
    """Mengubah huruf bergaya Unicode (misal 𝐊𝐞𝐞𝐧𝐚𝐧) menjadi huruf standar (Keenan) agar konsisten di narasi."""
    return unicodedata.normalize("NFKC", text)

def clean_narrative_labels(text):
    cleaned = re.sub(r'(?i)\bparagraf\s*\d+\s*:\s*', '', text)
    return cleaned.strip()

def highlight_player_names(escaped_text, player_names):
    """Menebalkan semua nama pemain di dalam alur cerita agar menonjol."""
    result = escaped_text
    variants = set()
    for name in player_names:
        if name:
            variants.add(name)
            variants.add(normalize_plain_text(name))

    sorted_names = sorted(variants, key=len, reverse=True)
    for name in sorted_names:
        escaped_name = html.escape(name)
        if not escaped_name:
            continue
        pattern = re.compile(re.escape(escaped_name), re.IGNORECASE)
        result = pattern.sub(lambda m: f"<b>{m.group(0)}</b>", result)
    return result

def format_players_with_gender(room, uid_list=None):
    target_ids = uid_list if uid_list is not None else list(room["players"].keys())
    parts = []
    for uid in target_ids:
        p_name = normalize_plain_text(room["players"][uid])
        p_gender = room["genders"].get(uid, "Cowok")
        gender_desc = "Laki-laki/Cowok" if p_gender == "Cowok" else "Perempuan/Cewek"
        parts.append(f"{p_name} ({gender_desc})")
    return ", ".join(parts)

def build_role_setup_instruction(room):
    """Menentukan peran pemain berdasarkan jumlah pemain, gender, dan tema/judul topik."""
    uids = room["turn_order"]
    count = len(uids)
    topic = room["topic"]
    genre = room["genre"]
    detail_str = format_players_with_gender(room, uids)

    if count == 1:
        uid = uids[0]
        solo_name = normalize_plain_text(room["players"][uid])
        solo_gender = room["genders"].get(uid, "Cowok")
        gender_word = "pria/cowok" if solo_gender == "Cowok" else "wanita/cewek"
        return (
            f"Jumlah pemain: 1 orang -> {detail_str}. Tema/Judul cerita: '{topic}' (Genre: {genre}). "
            f"Aturan peran: Jadikan {solo_name} (seorang {gender_word}) sebagai tokoh utama dalam tema '{topic}'. "
            f"Jika di judul tema '{topic}' terdapat nama tokoh lain (misalnya 'Rose' pada 'aku dan rose'), "
            f"hadirkan tokoh tersebut sebagai karakter pendamping utama yang langsung berinteraksi dengan {solo_name}. "
            f"Jika judul tema tidak menyebut nama lain, hadirkan 1 tokoh pendamping yang relevan dengan tema '{topic}'."
        )
    elif count == 2:
        u1, u2 = uids[0], uids[1]
        n1 = normalize_plain_text(room["players"][u1])
        n2 = normalize_plain_text(room["players"][u2])
        return (
            f"Jumlah pemain: 2 orang -> {detail_str}. Tema/Judul cerita: '{topic}' (Genre: {genre}). "
            f"Aturan peran: Sesuaikan peran {n1} dan {n2} (patuhi gender masing-masing) sebagai dua tokoh sentral dalam tema '{topic}'. "
            f"Fokus pada {n1} dan {n2}, jangan menambah tokoh baru yang tidak perlu."
        )
    else:
        return (
            f"Jumlah pemain: {count} orang -> {detail_str}. Tema/Judul cerita: '{topic}' (Genre: {genre}). "
            f"Aturan peran: Bagikan peran secara alami kepada semua {count} pemain tersebut sesuai gender masing-masing "
            f"di dalam satu latar cerita bertema '{topic}' sehingga setiap pemain memiliki keterkaitan yang jelas."
        )

def get_dynamic_pacing_cue(round_num, genre, is_solo=False):
    g = genre.lower()
    if is_solo:
        return (
            "Mode Solo: Gerakkan dialog atau reaksi tokoh pendamping di sekitar tokoh utama agar cerita terasa hidup dan interaktif."
        )
    if g == "romance":
        cues = [
            "Hadirkan momen canggung, tatapan tak terduga, atau situasi yang memancing obrolan emosional antar tokoh.",
            "Munculkan salah paham halus, rahasia kecil, atau keadaan yang memaksa tokoh saling bergantung.",
            "Hadirkan kejutan atau dilema perasaan yang menguji kedekatan dan kejujuran antar tokoh.",
            "Tingkatkan intensitas emosi dari setiap keputusan yang mereka ambil."
        ]
    elif g == "comedy":
        cues = [
            "Munculkan kekacauan kecil atau kesalahpahaman konyol yang memancing para tokoh saling bereaksi.",
            "Buat situasi makin absurd di luar rencana sehingga tiap tokoh harus merespons spontan.",
            "Hadirkan kejutan kocak yang bikin suasana makin ramai dan hidup.",
            "Puncak kekacauan komedi yang merespons langsung tingkah laku masing-masing tokoh."
        ]
    else:
        cues = [
            "Hadirkan petunjuk baru atau situasi mendesak yang memancing para tokoh berdiskusi dan mengambil sikap.",
            "Sisipkan dilema atau perbedaan kepentingan yang memicu kerja sama atau gesekan antar tokoh.",
            "Kejutkan alur dengan plot twist yang mengubah situasi di depan mereka.",
            "Tingkatkan taruhan cerita sehingga setiap tindakan tokoh membawa dampak nyata."
        ]
    return cues[(round_num - 1) % len(cues)]

# ---------------------------------------------------------
# 3. UI BUILDERS
# ---------------------------------------------------------
def build_lobby_text(room):
    if room["players"]:
        lines = []
        for idx, (uid, name) in enumerate(room["players"].items(), start=1):
            g = room["genders"].get(uid, "Cowok")
            icon = "♂️" if g == "Cowok" else "♀️"
            lines.append(f"  {idx}. <b>{html.escape(name)}</b> • {icon} {g}")
        players_lines = "\n".join(lines)
    else:
        players_lines = "  <i>Belum ada tokoh bergabung</i>"

    return (
        f"🛋️ <b>LOBBY CERITA</b>\n\n"
        f"🎭 <b>Genre</b>  : {html.escape(room['genre'])}\n"
        f"📌 <b>Tema</b>   : <i>{html.escape(room['topic'])}</i>\n\n"
        f"👥 <b>Daftar Tokoh ({len(room['players'])}):</b>\n"
        f"<blockquote>{players_lines}</blockquote>\n"
        f"⚙️ <b>Pengaturan Cepat:</b>\n"
        f"• <code>/nama NamaBaru</code> — Ubah nama tokoh\n"
        f"• <code>/gender cowok/cewek</code> — Ubah gender\n"
        f"• <code>/topik JudulTema</code> — Ubah tema cerita"
    )

def build_lobby_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("♂️ Join (Cowok)", callback_data="join_cowok"),
            InlineKeyboardButton("♀️ Join (Cewek)", callback_data="join_cewek")
        ],
        [
            InlineKeyboardButton("🚪 Leave", callback_data="leave_rp"),
            InlineKeyboardButton("🎲 Acak Urutan", callback_data="shuffle_players")
        ],
        [
            InlineKeyboardButton("🎭 Pilih Genre", callback_data="open_genre_menu"),
            InlineKeyboardButton("📖 Aturan & Panduan", callback_data="show_rules")
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
            InlineKeyboardButton("← Kembali ke Lobby", callback_data="back_to_dashboard")
        ]
    ])

def build_status_button():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❤️ Cek Status Saya", callback_data="check_my_status")]
    ])

def build_private_home(first_name, bot_username):
    text = (
        f"📖 <b>ROLEPLAY NOVEL BOT</b>\n\n"
        f"Halo, <b>{first_name}</b>.\n"
        f"Bangun kisah novel interaktif bersama teman grupmu dengan alur yang merespons setiap dialog dan tindakan tokoh.\n\n"
        f"<blockquote>"
        f"<b>🚀 Cara Memulai</b>\n\n"
        f"<b>1.</b> Masukkan bot ke grup & jadikan <b>Admin</b>\n"
        f"<b>2.</b> Ketik <code>/start_rp</code> untuk membuka Lobby\n"
        f"<b>3.</b> Pilih <b>♂️ Join (Cowok)</b> atau <b>♀️ Join (Cewek)</b>\n"
        f"<b>4.</b> Atur genre & tema, lalu tekan <b>Mulai Cerita</b>"
        f"</blockquote>"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📖 Aturan Main", callback_data="show_rules_pc"),
            InlineKeyboardButton("⚡ Format & Perintah", callback_data="show_features_pc")
        ],
        [
            InlineKeyboardButton("➕ Tambahkan ke Grup", url=f"https://t.me/{bot_username}?startgroup=true")
        ]
    ])
    return text, keyboard

# ---------------------------------------------------------
# 4. HELPER GROQ MODEL SELECTION (HANYA MODEL BESAR / ANTI-TYPO)
# ---------------------------------------------------------
def get_active_groq_models():
    priority = [
        "llama-3.3-70b-versatile",
        "meta-llama/llama-4-maverick-17b-128e-instruct",
        "meta-llama/llama-4-scout-17b-16e-instruct"
    ]
    try:
        models_page = ai_client.models.list()
        excluded_keywords = [
            "whisper", "tts", "guard", "vision", "embedding",
            "compound", "r1", "qwen", "8b", "instant", "gemma"
        ]
        available = [
            m.id for m in models_page.data
            if not any(kw in m.id.lower() for kw in excluded_keywords)
        ]
        ordered = [m for m in priority if m in available]
        for m in available:
            if m not in ordered:
                ordered.append(m)
        return ordered or priority
    except Exception as e:
        print(f"Gagal mengambil daftar model Groq: {e}")
        return priority

async def generate_groq_json(prompt, max_tokens=550):
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
                    temperature=0.65
                )
            )
            content = completion.choices[0].message.content
            if content and content.strip():
                return json.loads(content.strip())
        except Exception as e:
            last_error = e
            print(f"Model {model_name} gagal ({e}), beralih ke model berikutnya...")
            await asyncio.sleep(0.4)
            continue

    raise last_error or Exception("Tidak ada model Groq aktif yang merespons.")

# ---------------------------------------------------------
# 5. HELPER FUNCTIONS & TURN MANAGER
# ---------------------------------------------------------
def cancel_afk_timer(room):
    if room["timer_task"] and not room["timer_task"].done():
        room["timer_task"].cancel()
        room["timer_task"] = None

def reset_room_state(room):
    cancel_afk_timer(room)
    room["status"] = "IDLE"
    room["players"].clear()
    room["genders"].clear()
    room["hp"].clear()
    room["turn_order"].clear()
    room["history"].clear()
    room["full_story"].clear()
    room["round_actions"].clear()
    room["round_count"] = 0

async def start_afk_timer(client, chat_id, user_id, timeout=180):
    await asyncio.sleep(timeout)
    room = get_room(chat_id)

    if room["status"] == "PLAYING":
        active_user_id = room["turn_order"][room["current_turn_index"]]
        if active_user_id == user_id:
            skipped_player_name = room["players"][user_id]
            room["round_actions"].append(f"{normalize_plain_text(skipped_player_name)}: diam mengamati")
            await client.send_message(
                chat_id,
                f"⌛ <b>Waktu Habis</b> — Giliran <b>{html.escape(skipped_player_name)}</b> dilewati otomatis."
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
            room["round_actions"].append(f"{normalize_plain_text(dead_name)}: terkapar tak sadarkan diri")
            room["current_turn_index"] += 1
        else:
            break

    if room["current_turn_index"] >= len(room["turn_order"]):
        await process_end_of_round(client, chat_id)
        return

    current_user_id = room["turn_order"][room["current_turn_index"]]
    player_name = room["players"][current_user_id]

    mention_str = (
        f"<a href=\"tg://user?id={current_user_id}\"><b>{html.escape(player_name)}</b></a>"
        if current_user_id > 0 else f"<b>{html.escape(player_name)}</b>"
    )

    await client.send_message(
        chat_id,
        f"🎯 <b>GILIRAN BERIKUTNYA</b>\n\n"
        f"👤 <b>Tokoh</b> : {mention_str}\n\n"
        f"<blockquote>"
        f"<b>✍️ Cara Menulis (pilih salah satu):</b>\n"
        f"• Pakai perintah : <code>/aksi \"Ucapanmu\" ;tindakan;</code>\n"
        f"• Langsung ketik : <code>\"Ucapanmu\" ;tindakan;</code>"
        f"</blockquote>\n"
        f"⏱️ <i>Batas 3 menit • /skip lewati</i>"
    )

    room["timer_task"] = asyncio.create_task(start_afk_timer(client, chat_id, current_user_id, timeout=180))

async def process_end_of_round(client, chat_id):
    room = get_room(chat_id)
    cancel_afk_timer(room)

    room["round_count"] += 1
    is_solo = len(room["turn_order"]) == 1
    pacing_cue = get_dynamic_pacing_cue(room["round_count"], room["genre"], is_solo=is_solo)

    status_msg = await client.send_message(chat_id, "✒️ <i>Merangkai kelanjutan bab cerita...</i>")

    combined_actions = "; ".join(room["round_actions"])
    recent_context = "\n".join(room["history"][-2:])
    tokoh_detail = ", ".join([
        f"{normalize_plain_text(room['players'][uid])}({room['genders'].get(uid, 'Cowok')}|{room['hp'][uid]}HP)"
        for uid in room["turn_order"]
    ])

    prompt_round = (
        f"Genre:{room['genre']}|Tema:{room['topic']}|Bab:{room['round_count']}\n"
        f"Cerita Sebelumnya:\n{recent_context}\n"
        f"Tokoh Pemain:{tokoh_detail}\n"
        f"Aksi Bab Ini:{combined_actions}\n"
        f"Arahan Alur: {pacing_cue}\n\n"
        f"Tugas:\n"
        f"1. Tulis lanjutan novel 1 paragraf singkat (3-4 kalimat padat, bahasa Indonesia baku & rapi, tanpa typo) yang merespons langsung ucapan/tindakan tokoh dan memicu interaksi baru. Patuhi gender setiap tokoh.\n"
        f"2. Hitung perubahan HP tiap tokoh pemain jika ada benturan/bahaya/pemulihan (0 jika aman, negatif jika terluka, positif jika pulih).\n"
        f"Output JSON valid tanpa markdown:\n"
        f'{{"n":"paragraf novel singkat dan rapi","h":{{"NamaTokoh":0}}}}'
    )

    try:
        data = await generate_groq_json(prompt_round, max_tokens=550)
        raw_narasi = clean_narrative_labels(data.get("n", "Suasana semakin intens saat langkah mereka memicu kejadian tak terduga."))
        narasi_baru = highlight_player_names(html.escape(raw_narasi), list(room["players"].values()))

        room["history"].append(html.escape(raw_narasi))
        room["full_story"].append(html.escape(raw_narasi))

        if len(room["history"]) > 4:
            room["history"] = room["history"][-4:]

        hp_updates = data.get("h", {})
        name_to_id = {}
        for uid, pname in room["players"].items():
            name_to_id[pname.lower()] = uid
            name_to_id[normalize_plain_text(pname).lower()] = uid

        fallen_names = []

        for p_name, diff_val in hp_updates.items():
            uid = name_to_id.get(normalize_plain_text(p_name).lower())
            if uid and uid in room["hp"]:
                try:
                    hp_diff = int(diff_val)
                except (ValueError, TypeError):
                    hp_diff = 0

                if hp_diff != 0:
                    room["hp"][uid] = max(0, min(100, room["hp"][uid] + hp_diff))
                    if room["hp"][uid] <= 0:
                        fallen_names.append(f"<b>{html.escape(room['players'][uid])}</b>")

        room["round_actions"].clear()
        room["current_turn_index"] = 0

        await status_msg.delete()

        final_msg = (
            f"📖 <b>BAB {room['round_count']} • {html.escape(room['genre'].upper())}</b>\n\n"
            f"{narasi_baru}"
        )
        if fallen_names:
            final_msg += f"\n\n💀 <i>Tokoh tumbang: {', '.join(fallen_names)}</i>"

        await client.send_message(chat_id, final_msg, reply_markup=build_status_button())

        alive_players = [
            uid for uid in room["turn_order"]
            if room["hp"].get(uid, 0) > 0
        ]

        if not alive_players:
            epilog_prompt = (
                f"Genre:{room['genre']}|Akhir:{raw_narasi}\n"
                f"Semua tokoh tumbang. Tulis epilog penutup novel 2 kalimat singkat dalam bahasa Indonesia yang rapi.\n"
                f'Output JSON valid: {{"e":"epilog 2 kalimat"}}'
            )
            try:
                epilog_data = await generate_groq_json(epilog_prompt, max_tokens=220)
                epilog_raw = epilog_data.get("e", "Tirai akhirnya tertutup atas perjalanan mereka hari ini.")
                epilog_text = highlight_player_names(html.escape(epilog_raw), list(room["players"].values()))
            except Exception:
                epilog_text = "Tirai akhirnya tertutup atas perjalanan mereka hari ini, menyisakan kisah yang tak terlupakan."

            await client.send_message(
                chat_id,
                f"💀 <b>EPILOG • AKHIR PERJALANAN</b>\n\n"
                f"{epilog_text}\n\n"
                f"<i>Seluruh tokoh telah tumbang. Ketik <code>/start_rp</code> untuk membuka kisah baru.</i>"
            )
            reset_room_state(room)
            return

        await prompt_next_turn(client, chat_id)

    except Exception as e:
        print(f"Error Generate Round: {e}")
        await status_msg.edit_text(f"⚠️ <b>Gagal merangkai bab cerita:</b> <code>{html.escape(str(e))}</code>\n\nKetik /skip untuk melanjutkan.")

# ---------------------------------------------------------
# 6. PARSER & EKSEKUTOR AKSI GILIRAN
# ---------------------------------------------------------
async def process_submitted_turn(client, message: Message, raw_input: str):
    chat_id = message.chat.id
    room = get_room(chat_id)

    if room["status"] != "PLAYING":
        return

    actor_id = get_actor_id(message)
    if not actor_id:
        return

    active_user_id = room["turn_order"][room["current_turn_index"]]
    if actor_id != active_user_id:
        return

    text = raw_input.strip()
    normalized_text = text.replace("“", '"').replace("”", '"')

    dialogs = [d.strip() for d in re.findall(r'"([^"]+)"', normalized_text) if d.strip()]
    actions = [a.strip() for a in re.findall(r';([^;]+);', normalized_text) if a.strip()]

    unclosed_quote = normalized_text.count('"') % 2 != 0
    unclosed_semicolon = normalized_text.count(';') % 2 != 0

    if unclosed_quote or unclosed_semicolon or (not dialogs and not actions):
        await message.reply(
            f"❌ <b>Format Belum Tepat</b>\n"
            f"Pastikan tanda kutip atau titik koma dibuka serta ditutup dengan benar.\n\n"
            f"<blockquote>"
            f"• <code>/aksi \"Halo semuanya!\"</code>\n"
            f"• <code>/aksi ;berjalan mendekat;</code>\n"
            f"• <code>/aksi \"Ayo!\" ;menghunus pedang;</code>"
            f"</blockquote>",
            quote=True
        )
        return

    cancel_afk_timer(room)
    player_name = room["players"][actor_id]
    plain_player_name = normalize_plain_text(player_name)

    parsed_parts = []
    if dialogs:
        parsed_parts.append(f'"{ " ".join(dialogs) }"')
    if actions:
        parsed_parts.append(f'({ " ".join(actions) })')

    formatted_action = f"{plain_player_name}: " + " ".join(parsed_parts)
    room["round_actions"].append(formatted_action)

    await message.reply(f"✅ <b>Aksi {html.escape(player_name)} tercatat.</b>", quote=True)

    room["current_turn_index"] += 1
    await prompt_next_turn(client, chat_id)

# ---------------------------------------------------------
# 7. HANDLER CHAT PRIBADI (PC)
# ---------------------------------------------------------

@app.on_message(filters.private)
async def start_private(client, message: Message):
    bot_user = await client.get_me()
    first_name = html.escape(get_actor_name(message))
    text, keyboard = build_private_home(first_name, bot_user.username)
    await message.reply(text, reply_markup=keyboard)

# ---------------------------------------------------------
# 8. HANDLER GROUP & LOBBY
# ---------------------------------------------------------

@app.on_message(filters.command(["start_rp", "start"]) & filters.group)
async def start_rp_lobby(client, message: Message):
    room = get_room(message.chat.id)
    if room["status"] != "IDLE":
        await message.reply("⚠️ Sesi cerita masih berjalan atau Lobby sedang terbuka.\nKetik <code>/selesai_rp</code> jika ingin menutup sesi yang lama.")
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
# 9. CALLBACK BUTTON HANDLERS
# ---------------------------------------------------------

@app.on_callback_query(filters.regex("^check_my_status$"))
async def check_secret_status_popup(client, callback: CallbackQuery):
    room = get_room(callback.message.chat.id)
    user_id = get_actor_id(callback)

    if room["status"] != "PLAYING":
        await callback.answer("Belum ada sesi cerita yang berjalan.", show_alert=True)
        return

    if user_id not in room["players"]:
        await callback.answer("Kamu tidak terdaftar sebagai tokoh di cerita ini.", show_alert=True)
        return

    p_name = room["players"].get(user_id, "Tokoh")
    p_gender = room["genders"].get(user_id, "Cowok")
    sisa_hp = room["hp"].get(user_id, 100)

    if sisa_hp >= 75:
        kondisi = "Prima & bugar"
    elif sisa_hp >= 40:
        kondisi = "Terluka ringan"
    elif sisa_hp > 0:
        kondisi = "Kritis! Hati-hati bertindak"
    else:
        kondisi = "Tumbang (0 HP)"

    popup_text = (
        f"❤️ STATUS RAHASIA TOKOH\n\n"
        f"• Tokoh   : {p_name} ({p_gender})\n"
        f"• Nyawa   : {sisa_hp} / 100 HP\n"
        f"• Kondisi : {kondisi}"
    )
    await callback.answer(popup_text[:200], show_alert=True)

@app.on_callback_query(filters.regex("^open_genre_menu$"))
async def open_genre_selection(client, callback: CallbackQuery):
    room = get_room(callback.message.chat.id)
    if room["status"] != "LOBBY":
        await callback.answer("Genre hanya bisa diubah saat di Lobby!", show_alert=True)
        return

    await callback.message.edit_text(
        f"🎭 <b>PILIH GENRE CERITA</b>\n\n"
        f"Genre aktif : <b>{html.escape(room['genre'])}</b>\n"
        f"Pilih suasana cerita yang diinginkan:",
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

    await callback.answer(f"Genre: {chosen_genre}", show_alert=False)
    await callback.message.edit_text(build_lobby_text(room), reply_markup=build_lobby_keyboard())

@app.on_callback_query(filters.regex("^shuffle_players$"))
async def shuffle_lobby_players(client, callback: CallbackQuery):
    room = get_room(callback.message.chat.id)
    if room["status"] != "LOBBY":
        await callback.answer("Urutan hanya bisa diacak saat di Lobby!", show_alert=True)
        return

    if len(room["players"]) < 2:
        await callback.answer("Minimal harus ada 2 tokoh untuk mengacak urutan!", show_alert=True)
        return

    items = list(room["players"].items())
    random.shuffle(items)
    room["players"] = dict(items)

    await callback.answer("🎲 Urutan giliran diacak!", show_alert=False)
    await callback.message.edit_text(build_lobby_text(room), reply_markup=build_lobby_keyboard())

@app.on_callback_query(filters.regex("^(show_rules|show_features|back_to_dashboard|show_rules_pc|show_features_pc|back_to_pc)$"))
async def handle_info_buttons(client, callback: CallbackQuery):
    data = callback.data

    if data == "show_rules_pc":
        rules_text = (
            f"📖 <b>ATURAN BERMAIN</b>\n\n"
            f"<blockquote>"
            f"<b>1. Identitas & Peran Tema</b>\n"
            f"Pilih gender saat bergabung di Lobby. AI menyesuaikan peran tiap pemain berdasarkan gender, jumlah pemain, dan judul tema.\n\n"
            f"<b>2. Kebebasan Karakter</b>\n"
            f"Tentukan sendiri sifat dan tindakan tokohmu—baik, netral, licik, maupun kocak—lewat dialog dan aksi.\n\n"
            f"<b>3. Status Rahasia & Timer</b>\n"
            f"Tiap tokoh mulai dengan <b>100 HP</b> (cek lewat tombol <b>❤️ Cek Status Saya</b>) dan memiliki waktu <b>3 menit</b> per giliran."
            f"</blockquote>"
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("← Kembali", callback_data="back_to_pc")]])
        await callback.message.edit_text(rules_text, reply_markup=keyboard)

    elif data == "show_features_pc":
        features_text = (
            f"⚡ <b>FORMAT & DAFTAR PERINTAH</b>\n\n"
            f"<blockquote>"
            f"<b>✍️ Format Menulis Giliran</b>\n\n"
            f"• <b>Dialog</b>\n"
            f"  <code>/aksi \"Jangan buka pintu itu!\"</code>\n\n"
            f"• <b>Aksi</b>\n"
            f"  <code>/aksi ;menarik lengan Haru;</code>\n\n"
            f"• <b>Gabungan</b>\n"
            f"  <code>/aksi \"Awas!\" ;mendorong Javen;</code>"
            f"</blockquote>\n"
            f"<blockquote>"
            f"<b>🛠️ Perintah di Grup</b>\n\n"
            f"• <code>/start_rp</code> — Buka Lobby cerita\n"
            f"• <code>/aksi</code> — Kirim dialog / aksi giliranmu\n"
            f"• <code>/nama</code> — Ganti nama tokoh\n"
            f"• <code>/gender</code> — Ubah gender (cowok/cewek)\n"
            f"• <code>/topik</code> — Ubah judul/tema cerita\n"
            f"• <code>/skip</code> — Lewati giliran aktif\n"
            f"• <code>/selesai_rp</code> — Tutup & rangkum kisah"
            f"</blockquote>"
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("← Kembali", callback_data="back_to_pc")]])
        await callback.message.edit_text(features_text, reply_markup=keyboard)

    elif data == "back_to_pc":
        bot_user = await client.get_me()
        first_name = html.escape(get_actor_name(callback))
        text, keyboard = build_private_home(first_name, bot_user.username)
        await callback.message.edit_text(text, reply_markup=keyboard)

    elif data == "show_rules":
        rules_text = (
            f"📖 <b>ATURAN & PANDUAN</b>\n\n"
            f"<blockquote>"
            f"<b>✍️ Format Menulis Giliran</b>\n"
            f"• Dialog : <code>/aksi \"Ucapan tokoh\"</code>\n"
            f"• Aksi   : <code>/aksi ;gerakan tokoh;</code>\n"
            f"• Gabung : <code>/aksi \"Awas!\" ;menghindar;</code>"
            f"</blockquote>\n"
            f"<blockquote>"
            f"<b>📌 Info Permainan</b>\n"
            f"• Pilih <b>♂️ Join (Cowok)</b> / <b>♀️ Join (Cewek)</b> di Lobby.\n"
            f"• Tekan <b>❤️ Cek Status Saya</b> untuk cek HP pribadi.\n"
            f"• Batas 3 menit per giliran • <code>/selesai_rp</code> untuk tamat."
            f"</blockquote>"
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("← Kembali ke Lobby", callback_data="back_to_dashboard")]])
        await callback.message.edit_text(rules_text, reply_markup=keyboard)

    elif data == "back_to_dashboard":
        room = get_room(callback.message.chat.id)
        await callback.message.edit_text(build_lobby_text(room), reply_markup=build_lobby_keyboard())

# ---------------------------------------------------------
# 10. COMMAND & GAME HANDLERS
# ---------------------------------------------------------

@app.on_message(filters.command("nama") & filters.group)
async def set_character_name(client, message: Message):
    actor_id = get_actor_id(message)
    if not actor_id:
        return

    room = get_room(message.chat.id)
    if room["status"] != "LOBBY":
        await message.reply("⚠️ Nama tokoh hanya bisa diganti saat masih di Lobby!")
        return

    if actor_id not in room["players"]:
        await message.reply("⚠️ Tekan tombol <b>♂️ Join (Cowok)</b> atau <b>♀️ Join (Cewek)</b> dulu!")
        return

    if len(message.command) < 2:
        await message.reply("Contoh: <code>/nama Ken</code> atau <code>/nama Javen</code>")
        return

    custom_name = " ".join(message.command[1:]).strip()[:20]

    if any(normalize_plain_text(existing).lower() == normalize_plain_text(custom_name).lower() and uid != actor_id for uid, existing in room["players"].items()):
        await message.reply(f"⚠️ Nama <b>{html.escape(custom_name)}</b> sudah dipakai pemain lain. Pilih nama lain!")
        return

    room["players"][actor_id] = custom_name
    await message.reply(f"🪪 <b>Nama Tokoh:</b> <code>{html.escape(custom_name)}</code>", quote=True)

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

@app.on_message(filters.command("gender") & filters.group)
async def set_character_gender(client, message: Message):
    actor_id = get_actor_id(message)
    if not actor_id:
        return

    room = get_room(message.chat.id)
    if room["status"] != "LOBBY":
        await message.reply("⚠️ Gender hanya bisa diubah saat masih di Lobby!")
        return

    if actor_id not in room["players"]:
        await message.reply("⚠️ Gabung ke Lobby dulu sebelum mengatur gender!")
        return

    if len(message.command) < 2:
        await message.reply("Contoh: <code>/gender cowok</code> atau <code>/gender cewek</code>")
        return

    arg = message.command[1].lower()
    if arg in ("cowok", "cwo", "laki", "pria", "male", "m"):
        chosen = "Cowok"
    elif arg in ("cewek", "cwe", "perempuan", "wanita", "female", "f"):
        chosen = "Cewek"
    else:
        await message.reply("⚠️ Pilihan gender: <code>cowok</code> atau <code>cewek</code>")
        return

    room["genders"][actor_id] = chosen
    icon = "♂️" if chosen == "Cowok" else "♀️"
    await message.reply(f"{icon} <b>Gender Diubah:</b> {chosen}", quote=True)

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
        await message.reply("⚠️ Tema cerita hanya bisa diubah saat masih di Lobby!")
        return

    if len(message.command) < 2:
        await message.reply("Contoh: <code>/topik aku dan rose</code> atau <code>/topik transmigrasi antagonis</code>")
        return

    new_topic = " ".join(message.command[1:])
    room["topic"] = new_topic
    await message.reply(f"📌 <b>Tema Cerita:</b> <i>{html.escape(new_topic)}</i>")

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

@app.on_callback_query(filters.regex("^(join_cowok|join_cewek|leave_rp|start_story)$"))
async def handle_lobby_buttons(client, callback: CallbackQuery):
    chat_id = callback.message.chat.id
    actor_id = get_actor_id(callback)
    actor_name = get_actor_name(callback)
    room = get_room(chat_id)

    if callback.data in ("join_cowok", "join_cewek"):
        chosen_gender = "Cowok" if callback.data == "join_cowok" else "Cewek"
        icon = "♂️" if chosen_gender == "Cowok" else "♀️"

        if actor_id not in room["players"]:
            room["players"][actor_id] = actor_name
            room["genders"][actor_id] = chosen_gender
            room["hp"][actor_id] = 100
            await callback.answer(f"{actor_name} bergabung sebagai {chosen_gender}!", show_alert=False)
            await callback.message.edit_text(build_lobby_text(room), reply_markup=build_lobby_keyboard())
        else:
            if room["genders"].get(actor_id) != chosen_gender:
                room["genders"][actor_id] = chosen_gender
                await callback.answer(f"{icon} Gender diubah menjadi {chosen_gender}!", show_alert=False)
                await callback.message.edit_text(build_lobby_text(room), reply_markup=build_lobby_keyboard())
            else:
                await callback.answer(f"Kamu sudah terdaftar ({chosen_gender})!", show_alert=False)

    elif callback.data == "leave_rp":
        if actor_id in room["players"]:
            del room["players"][actor_id]
            room["genders"].pop(actor_id, None)
            room["hp"].pop(actor_id, None)
            await callback.answer("Keluar dari Lobby.", show_alert=False)
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

        status_msg = await callback.message.edit_text("✒️ <i>Menentukan peran tokoh & menulis prolog cerita...</i>")

        role_setup_rule = build_role_setup_instruction(room)

        prompt_pembuka = (
            f"Genre: {room['genre']}\n"
            f"Judul/Tema Cerita: {room['topic']}\n"
            f"{role_setup_rule}\n\n"
            f"Tugas:\n"
            f"Tulis prolog singkat dan mengalir (2 paragraf pendek, masing-masing 2-3 kalimat saja) dalam bahasa Indonesia baku yang rapi, tanpa typo, dan patuh pada gender masing-masing tokoh.\n"
            f"• Paragraf pertama memperkenalkan latar tempat serta peran masing-masing tokoh sesuai aturan peran dan gendernya.\n"
            f"• Paragraf kedua langsung memunculkan kejadian atau dialog awal yang memicu interaksi.\n"
            f'Output JSON valid tanpa markdown (pisahkan dua paragraf dengan \\n\\n):\n'
            f'{{"p":"paragraf pertama\\n\\nparagraf kedua"}}'
        )

        try:
            data = await generate_groq_json(prompt_pembuka, max_tokens=500)
            raw_text = clean_narrative_labels(data.get("p", f"Kisah {room['topic']} pun dimulai."))
            narasi_awal = highlight_player_names(html.escape(raw_text), list(room["players"].values()))
            room["history"].append(html.escape(raw_text))
            room["full_story"].append(html.escape(raw_text))
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
            f"📌 <i>{html.escape(room['topic'])}</i>\n\n"
            f"{narasi_awal}",
            reply_markup=build_status_button()
        )
        await prompt_next_turn(client, chat_id)

@app.on_message(filters.command("aksi") & filters.group)
async def handle_turn_command(client, message: Message):
    raw_arg = message.text.partition(" ")[2] if " " in message.text else ""
    if not raw_arg.strip():
        await message.reply(
            "Contoh penggunaan:\n"
            "<code>/aksi \"Oh apa itu cuy?\" ;melihat ke sekitar;</code>",
            quote=True
        )
        return
    await process_submitted_turn(client, message, raw_arg)

@app.on_message(filters.group & filters.text & ~filters.regex(r"^/"))
async def handle_turn_plain_text(client, message: Message):
    await process_submitted_turn(client, message, message.text)

@app.on_message(filters.command("skip") & filters.group)
async def skip_turn(client, message: Message):
    chat_id = message.chat.id
    room = get_room(chat_id)

    if room["status"] != "PLAYING":
        return

    cancel_afk_timer(room)

    active_user_id = room["turn_order"][room["current_turn_index"]]
    skipped_player_name = room["players"][active_user_id]

    room["round_actions"].append(f"{normalize_plain_text(skipped_player_name)}: menahan diri dan mengamati")
    await message.reply(f"⏭️ Giliran <b>{html.escape(skipped_player_name)}</b> dilewati.")

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
        tokoh_detail = format_players_with_gender(room, room["turn_order"])

        prompt_rekap = (
            f"Genre:{room['genre']}|Tema:{room['topic']}|Tokoh:{tokoh_detail}\n"
            f"Rangkaian Kejadian:\n{gabungan_bab}\n\n"
            f"Tugas: Rangkum seluruh perjalanan cerita di atas dari awal hingga akhir menjadi 2 paragraf singkat yang utuh, "
            f"rapi dalam bahasa Indonesia baku (patuhi gender tiap tokoh), dan ditutup dengan kalimat epilog yang berkesan.\n"
            f'Output JSON valid tanpa markdown (pisahkan paragraf dengan \\n\\n):\n'
            f'{{"r":"paragraf satu\\n\\nparagraf dua"}}'
        )

        try:
            rekap_data = await generate_groq_json(prompt_rekap, max_tokens=550)
            rekap_raw = clean_narrative_labels(rekap_data.get("r", ""))
            rekap_text = highlight_player_names(html.escape(rekap_raw), list(room["players"].values()))
        except Exception:
            rekap_text = f"{room['full_story'][0]}\n\n...\n\n{room['full_story'][-1]}"

        await status_msg.delete()
        await message.reply(
            f"📚 <b>ARSIP KISAH UTUH • {html.escape(room['genre'].upper())}</b>\n"
            f"📌 <i>{html.escape(room['topic'])}</i>\n\n"
            f"{rekap_text}\n\n"
            f"🏆 <i>Sesi cerita selesai. Ketik <code>/start_rp</code> untuk membuka petualangan baru.</i>"
        )
    else:
        await message.reply("🏆 <b>KISAH DITUTUP</b>\nPendaftaran dibatalkan. Ketik <code>/start_rp</code> untuk membuka sesi baru.")

    reset_room_state(room)

# ---------------------------------------------------------
# 11. STARTUP & AUTO-REGISTER BOT COMMANDS
# ---------------------------------------------------------
async def main():
    await app.start()

    await app.set_bot_commands(
        [
            BotCommand("start_rp", "Buka lobby pendaftaran cerita RP"),
            BotCommand("aksi", "Kirim dialog / aksi giliranmu"),
            BotCommand("nama", "Ganti nama tokohmu di Lobby"),
            BotCommand("gender", "Ubah gender tokoh (cowok/cewek) di Lobby"),
            BotCommand("topik", "Tentukan judul/tema cerita di Lobby"),
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

    print("Bot RP Novel aktif (Fitur Gender per Player di Lobby + Prolog JSON Rapi)!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    app.run(main())
