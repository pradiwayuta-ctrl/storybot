import os
import re
import asyncio
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from google import genai
from google.genai import types

# ---------------------------------------------------------
# 1. SETUP ENVIRONMENT VARIABLES & CLIENTS
# ---------------------------------------------------------
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
API_ID = int(os.environ.get("TELEGRAM_API_ID", 0))
API_HASH = os.environ.get("TELEGRAM_API_HASH")
BOT_TOKEN = os.environ.get("TELEGRAM_API_KEY")

if not all([GEMINI_API_KEY, API_ID, API_HASH, BOT_TOKEN]):
    raise ValueError("Pastikan semua Environment Variables (GEMINI_API_KEY, TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_API_KEY) sudah diisi!")

ai_client = genai.Client(api_key=GEMINI_API_KEY)
app = Client(
    "rp_narrator_bot", 
    api_id=API_ID, 
    api_hash=API_HASH, 
    bot_token=BOT_TOKEN,
    parse_mode=ParseMode.HTML
)

SYSTEM_INSTRUCTION = """
Kamu adalah Game Master (GM) dan Narator utama dalam game Roleplay (RP) teks interaktif.
Tugasmu:
1. Terima gabungan aksi dari semua pemain dalam 1 ronde.
2. Buatkan narasi balasan MAKSIMAL 2 kalimat padat yang merangkum hasil/dampak dari gabungan aksi para pemain tersebut dan memperbarui situasi konflik.
3. Gunakan gaya bahasa naratif RPG yang immersif dan dramatis.
4. Hanya sebut nama pemain yang terdaftar.
"""

# ---------------------------------------------------------
# 2. STATE ROOM MEMORY
# ---------------------------------------------------------
rooms = {}

def get_room(chat_id):
    if chat_id not in rooms:
        rooms[chat_id] = {
            "status": "IDLE",            # IDLE, LOBBY, PLAYING
            "theme": "Petualangan Fantasy",
            "players": {},               # {user_id: first_name}
            "turn_order": [],              # [user_id1, user_id2, ...]
            "current_turn_index": 0,       # Indeks player aktif
            "round_actions": [],           # Buffer aksi 1 ronde
            "history": [],                 # Log narasi cerita
            "lobby_msg_id": None,          # ID pesan lobby untuk unpin
            "timer_task": None             # Task background untuk AFK timer
        }
    return rooms[chat_id]

# ---------------------------------------------------------
# 3. HELPER FUNCTIONS, TIMEOUT & TURN MANAGER
# ---------------------------------------------------------
def cancel_afk_timer(room):
    """Membatalkan timer AFK jika player merespons tepat waktu."""
    if room["timer_task"] and not room["timer_task"].done():
        room["timer_task"].cancel()
        room["timer_task"] = None

async def start_afk_timer(client, chat_id, user_id, timeout=120):
    """Timer otomatis 2 menit (120 detik) untuk auto-skip jika AFK."""
    await asyncio.sleep(timeout)
    room = get_room(chat_id)
    
    if room["status"] == "PLAYING":
        active_user_id = room["turn_order"][room["current_turn_index"]]
        if active_user_id == user_id:
            skipped_player_name = room["players"][user_id]
            room["round_actions"].append(f"- {skipped_player_name}: (Waktu habis / auto-skip AFK)")
            await client.send_message(
                chat_id, 
                f"⌛ <b>Waktu Habis!</b> Giliran <b>{skipped_player_name}</b> di-skip otomatis oleh Game Master."
            )
            
            room["current_turn_index"] += 1
            await prompt_next_turn(client, chat_id)

async def prompt_next_turn(client, chat_id):
    room = get_room(chat_id)
    cancel_afk_timer(room)
    
    if room["current_turn_index"] >= len(room["turn_order"]):
        await process_end_of_round(client, chat_id)
        return

    current_user_id = room["turn_order"][room["current_turn_index"]]
    player_name = room["players"][current_user_id]
    
    await client.send_message(
        chat_id,
        f"🎯 <b>GILIRAN PLAYER:</b> <a href=\"tg://user?id={current_user_id}\">{player_name}</a>\n\n"
        f"Ketik dialog/aksi kamu di grup ini.\n"
        f"<i>(Batas Waktu: 2 Menit | Ketik /skip untuk melewati)</i>"
    )
    
    room["timer_task"] = asyncio.create_task(start_afk_timer(client, chat_id, current_user_id, timeout=120))

async def process_end_of_round(client, chat_id):
    room = get_room(chat_id)
    cancel_afk_timer(room)
    
    await client.send_message(chat_id, "🎲 <b>[RONDE SELESAI]</b> <i>Game Master sedang memproses aksi seluruh pemain...</i>")

    combined_actions = "\n".join(room["round_actions"])
    recent_context = "\n".join(room["history"][-3:])
    
    prompt_round = (
        f"Riwayat Cerita Sebelumnya:\n{recent_context}\n\n"
        f"Aksi Pemain Ronde Ini:\n{combined_actions}\n\n"
        f"Sebagai Game Master, berikan narasi balasan (max 2 kalimat) atas seluruh aksi pemain di atas."
    )

    response = ai_client.models.generate_content(
        model="gemini-3.8-flash",
        contents=prompt_round,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            max_output_tokens=100,
            temperature=0.7
        )
    )

    narasi_baru = response.text
    room["history"].append(f"[NARATOR]: {narasi_baru}")
    
    room["round_actions"].clear()
    room["current_turn_index"] = 0
    
    await client.send_message(chat_id, f"📜 <b>[NARASI GAME MASTER]</b>\n\n{narasi_baru}")
    await prompt_next_turn(client, chat_id)

# ---------------------------------------------------------
# 4. HANDLER START (PRIVATE CHAT & GROUP)
# ---------------------------------------------------------

@app.on_message(filters.command(["start", "help"]) & filters.private)
async def start_private(client, message: Message):
    bot_user = await client.get_me()
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📖 Rules Permainan", callback_data="show_rules_pc"),
            InlineKeyboardButton("⚡ Panduan Aksi", callback_data="show_features_pc")
        ],
        [
            InlineKeyboardButton("⚔️ Masukkan GM ke Grup", url=f"https://t.me/{bot_user.username}?startgroup=true")
        ]
    ])
    
    text = (
        f"🛡️ <b>GAME MASTER ROLEPLAY BOT</b> 🛡️\n\n"
        f"Selamat datang, <b>{message.from_user.first_name}</b>!\n"
        f"Sistem ini bertindak sebagai Game Master otomatis untuk memimpin jalannya alur cerita Roleplay di grup kamu.\n\n"
        f"<b>Cara Memulai Game:</b>\n"
        f"1. Tambahkan bot ini ke <b>Grup Telegram</b> kamu.\n"
        f"2. Berikan akses <b>Admin</b> (terutama izin <i>Pin Messages</i>).\n"
        f"3. Ketik perintah <code>/start_rp</code> di grup untuk membuka Arena Pendaftaran.\n\n"
        f"Pilih menu di bawah untuk melihat aturan dan cara input aksi!"
    )
    await message.reply(text, reply_markup=keyboard)


@app.on_message(filters.command(["start_rp", "start"]) & filters.group)
async def start_rp_lobby(client, message: Message):
    room = get_room(message.chat.id)
    if room["status"] != "IDLE":
        await message.reply("⚠️ Sesi petualangan sedang berlangsung atau dalam ruang pendaftaran.")
        return

    room["status"] = "LOBBY"
    room["players"].clear()
    room["history"].clear()
    room["round_actions"].clear()

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚔️ Join Game", callback_data="join_rp"), 
            InlineKeyboardButton("🚪 Leave", callback_data="leave_rp")
        ],
        [
            InlineKeyboardButton("📖 Rules", callback_data="show_rules"), 
            InlineKeyboardButton("⚡ Panduan Aksi", callback_data="show_features")
        ],
        [
            InlineKeyboardButton("🚀 Mulai Petualangan", callback_data="start_story")
        ]
    ])
    
    welcome_text = (
        f"🏛️ <b>ARENA PENDAFTARAN PETUALANGAN</b> 🏛️\n\n"
        f"Pintu petualangan baru telah dibuka!\n"
        f"• Tema Saat Ini: <b>{room['theme']}</b> <i>(Ganti via <code>/tema nama_tema</code>)</i>\n\n"
        f"Tekan tombol <b>Join Game</b> untuk mendaftarkan karakter kamu!"
    )
    
    lobby_msg = await message.reply(welcome_text, reply_markup=keyboard)
    
    try:
        await lobby_msg.pin(disable_notification=False)
        room["lobby_msg_id"] = lobby_msg.id
    except Exception:
        pass

# ---------------------------------------------------------
# 5. CALLBACK BUTTON HANDLERS
# ---------------------------------------------------------

@app.on_callback_query(filters.regex("^(show_rules|show_features|back_to_dashboard|show_rules_pc|show_features_pc|back_to_pc)$"))
async def handle_info_buttons(client, callback: CallbackQuery):
    data = callback.data

    if data == "show_rules_pc":
        rules_text = (
            "📜 <b>ATURAN PERMAINAN (GAME RULES)</b>\n\n"
            "1. <b>Sistem Giliran (Turn-Based):</b> Pemain HANYA boleh bertindak saat nama karakternya dipanggil oleh GM.\n"
            "2. <b>Batas Waktu (AFK Timer):</b> Pemain memiliki waktu 2 menit per giliran. Jika melebihi batas, giliran akan di-skip otomatis.\n"
            "3. <b>Identitas Karakter:</b> Game Master menggunakan Nama Depan Telegram kamu sebagai nama resmi karakter dalam narasi.\n"
            "4. <b>Format Bertindak:</b> Wajib menggunakan simbol dialog <code>\"...\"</code> atau aksi <code>;...;</code>."
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="back_to_pc")]])
        await callback.message.edit_text(rules_text, reply_markup=keyboard)

    elif data == "show_features_pc":
        features_text = (
            "⚡ <b>PANDUAN FORMAT AKSI & PERINTAH</b>\n\n"
            "🎯 <b>Cara Mengirim Aksi di Grup:</b>\n"
            "• <b>Bicara / Dialog:</b> <code>\"Siapa yang berani melangkah ke depan?\"</code>\n"
            "• <b>Gerakan / Aksi:</b> <code>;mengangkat pedang dan bersiap bertarung;</code>\n"
            "• <b>Kombo:</b> <code>\"Maju bersama!\" ;mendorong pintu gerbang;</code>\n\n"
            "🛠️ <b>Perintah Utama (Commands):</b>\n"
            "• <code>/start_rp</code> - Buka Arena Pendaftaran di grup\n"
            "• <code>/tema [nama]</code> - Ganti tema cerita saat di Lobby\n"
            "• <code>/skip</code> - Lewati giliran pemain yang terhambat\n"
            "• <code>/selesai_rp</code> - Akhiri sesi petualangan"
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="back_to_pc")]])
        await callback.message.edit_text(features_text, reply_markup=keyboard)

    elif data == "back_to_pc":
        bot_user = await client.get_me()
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📖 Rules Permainan", callback_data="show_rules_pc"),
                InlineKeyboardButton("⚡ Panduan Aksi", callback_data="show_features_pc")
            ],
            [
                InlineKeyboardButton("⚔️ Masukkan GM ke Grup", url=f"https://t.me/{bot_user.username}?startgroup=true")
            ]
        ])
        text = (
            f"🛡️ <b>GAME MASTER ROLEPLAY BOT</b> 🛡️\n\n"
            f"Selamat datang, <b>{callback.from_user.first_name}</b>!\n"
            f"Pilih menu di bawah untuk melihat penjelasan sistem permainan."
        )
        await callback.message.edit_text(text, reply_markup=keyboard)

    elif data == "show_rules":
        rules_text = (
            "📜 <b>ATURAN PERMAINAN (GAME RULES)</b>\n\n"
            "1. Bertindak HANYA saat giliran nama kamu dipanggil oleh Game Master.\n"
            "2. Waktu merespons adalah 2 menit per pemain (auto-skip jika AFK).\n"
            "3. Karakter menggunakan nama depan akun Telegram kamu."
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali ke Arena", callback_data="back_to_dashboard")]])
        await callback.message.edit_text(rules_text, reply_markup=keyboard)

    elif data == "show_features":
        features_text = (
            "⚡ <b>FORMAT INPUT AKSI:</b>\n\n"
            "• <b>Dialog:</b> <code>\"isi ucapan\"</code>\n"
            "• <b>Aksi:</b> <code>;isi tindakan;</code>\n"
            "• <b>Kombo:</b> <code>\"isi ucapan\" ;isi tindakan;</code>"
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali ke Arena", callback_data="back_to_dashboard")]])
        await callback.message.edit_text(features_text, reply_markup=keyboard)

    elif data == "back_to_dashboard":
        room = get_room(callback.message.chat.id)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚔️ Join Game", callback_data="join_rp"), InlineKeyboardButton("🚪 Leave", callback_data="leave_rp")],
            [InlineKeyboardButton("📖 Rules", callback_data="show_rules"), InlineKeyboardButton("⚡ Panduan Aksi", callback_data="show_features")],
            [InlineKeyboardButton("🚀 Mulai Petualangan", callback_data="start_story")]
        ])
        welcome_text = (
            f"🏛️ <b>ARENA PENDAFTARAN PETUALANGAN</b> 🏛️\n\n"
            f"Pintu petualangan baru telah dibuka!\n"
            f"• Tema Saat Ini: <b>{room['theme']}</b>\n\n"
            f"Tekan tombol <b>Join Game</b> untuk mendaftarkan karakter kamu!"
        )
        await callback.message.edit_text(welcome_text, reply_markup=keyboard)

# ---------------------------------------------------------
# 6. COMMAND & GAME HANDLERS
# ---------------------------------------------------------

@app.on_message(filters.command("tema") & filters.group)
async def set_theme(client, message: Message):
    room = get_room(message.chat.id)
    if room["status"] != "LOBBY":
        await message.reply("⚠️ Tema cerita hanya dapat diubah saat berada di Arena Pendaftaran (Lobby)!")
        return

    if len(message.command) < 2:
        await message.reply("Contoh: <code>/tema Mafia Sicily</code> atau <code>/tema Kerajaan Cyberpunk</code>")
        return

    new_theme = " ".join(message.command[1:])
    room["theme"] = new_theme
    await message.reply(f"🎨 <b>Tema Petualangan Diubah:</b> <code>{new_theme}</code>")

@app.on_callback_query(filters.regex("^(join_rp|leave_rp|start_story)$"))
async def handle_lobby_buttons(client, callback: CallbackQuery):
    chat_id = callback.message.chat.id
    user = callback.from_user
    room = get_room(chat_id)

    if callback.data == "join_rp":
        room["players"][user.id] = user.first_name
        await callback.answer(f"Karakter {user.first_name} terdaftar!", show_alert=False)
        
    elif callback.data == "leave_rp":
        if user.id in room["players"]:
            del room["players"][user.id]
            await callback.answer("Kamu keluar dari pendaftaran.", show_alert=False)

    elif callback.data == "start_story":
        if len(room["players"]) < 1:
            await callback.answer("Minimal harus ada 1 pemain yang terdaftar!", show_alert=True)
            return

        if room["lobby_msg_id"]:
            try:
                await client.unpin_chat_message(chat_id, room["lobby_msg_id"])
            except Exception:
                pass

        room["status"] = "PLAYING"
        room["turn_order"] = list(room["players"].keys())
        room["current_turn_index"] = 0

        await callback.message.edit_text("🎲 <b>[GAME MASTER]</b> <i>Sedang merangkai takdir dan pembukaan dunia...</i>")
        
        player_names = ", ".join(room["players"].values())
        prompt_pembuka = (
            f"Buatkan narasi pembuka tepat 1 PARAGRAF untuk tema '{room['theme']}'. "
            f"Pemain yang ikut: {player_names}. Berikan peran yang sesuai tema untuk setiap pemain "
            f"menggunakan nama depan mereka, lalu tutup dengan situasi konflik awal."
        )

        response = ai_client.models.generate_content(
            model="gemini-3.8-flash",
            contents=prompt_pembuka,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                max_output_tokens=150,
                temperature=0.7
            )
        )
        
        narasi_awal = response.text
        room["history"].append(f"[NARATOR]: {narasi_awal}")
        
        await callback.message.reply(f"📜 <b>[AWAL PETUALANGAN]</b>\n\n{narasi_awal}")
        await prompt_next_turn(client, chat_id)

@app.on_message(filters.group & filters.text & ~filters.command(["start_rp", "start", "selesai_rp", "skip", "tema"]))
async def handle_turn_action(client, message: Message):
    chat_id = message.chat.id
    room = get_room(chat_id)
    
    if room["status"] != "PLAYING":
        return

    active_user_id = room["turn_order"][room["current_turn_index"]]
    if message.from_user.id != active_user_id:
        return

    text = message.text
    if not ('"' in text or ';' in text):
        return

    cancel_afk_timer(room)

    player_name = room["players"][message.from_user.id]
    
    dialogs = re.findall(r'"([^"]*)"', text)
    actions = re.findall(r';([^;]*);', text)
    
    parsed_parts = []
    if dialogs: parsed_parts.append(f'Bicara: "{ " ".join(dialogs) }"')
    if actions: parsed_parts.append(f'Aksi: ({ " ".join(actions) })')
    
    formatted_action = f"- {player_name}: " + " ".join(parsed_parts)
    
    room["round_actions"].append(formatted_action)
    await message.reply(f"⚔️ Aksi <b>{player_name}</b> terrekam oleh Game Master!")
    
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
    
    room["round_actions"].append(f"- {skipped_player_name}: (Tidak bertindak / di-skip)")
    await message.reply(f"⏭️ Giliran <b>{skipped_player_name}</b> dilewati!")
    
    room["current_turn_index"] += 1
    await prompt_next_turn(client, chat_id)

@app.on_message(filters.command("selesai_rp") & filters.group)
async def stop_rp(client, message: Message):
    room = get_room(message.chat.id)
    if room["status"] == "IDLE":
        await message.reply("⚠️ Tidak ada petualangan yang sedang aktif saat ini.")
        return

    cancel_afk_timer(room)

    room["status"] = "IDLE"
    room["players"].clear()
    room["history"].clear()
    room["round_actions"].clear()
    await message.reply("🏆 <b>[PETUALANGAN SELESAI]</b> Sesi permaianan telah diakhiri. Sampai jumpa di petualangan berikutnya!")

if __name__ == "__main__":
    app.run()
