import os
import re
import asyncio
from pyrogram import Client, filters
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
app = Client("rp_narrator_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

SYSTEM_INSTRUCTION = """
Kamu adalah Narator (Game Master) RP teks interaktif.
Tugasmu:
1. Terima gabungan aksi dari semua pemain dalam 1 ronde.
2. Buatkan narasi balasan MAKSIMAL 2 kalimat padat yang merangkum hasil/dampak dari gabungan aksi para pemain tersebut dan memperbarui situasi konflik.
3. Hanya sebut nama pemain yang terdaftar.
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
            await client.send_message(chat_id, f"⏰ **Waktu habis!** Giliran **{skipped_player_name}** otomatis dilewati (auto-skip).")
            
            room["current_turn_index"] += 1
            await prompt_next_turn(client, chat_id)

async def prompt_next_turn(client, chat_id):
    room = get_room(chat_id)
    cancel_afk_timer(room)
    
    # Jika semua player di ronde ini sudah aksi -> Generate Gemini!
    if room["current_turn_index"] >= len(room["turn_order"]):
        await process_end_of_round(client, chat_id)
        return

    current_user_id = room["turn_order"][room["current_turn_index"]]
    player_name = room["players"][current_user_id]
    
    await client.send_message(
        chat_id,
        f"🎯 **Giliran:** [{player_name}](tg://user?id={current_user_id})\n"
        f"Ketik dialog/aksi kamu di grup ini.\n"
        f"*(Waktu: 2 Menit | Ketik `/skip` jika ingin melewati)*"
    )
    
    # Jalankan background timer 2 menit
    room["timer_task"] = asyncio.create_task(start_afk_timer(client, chat_id, current_user_id, timeout=120))

async def process_end_of_round(client, chat_id):
    room = get_room(chat_id)
    cancel_afk_timer(room)
    
    await client.send_message(chat_id, "⏳ **Semua pemain selesai bertindak.** Narator sedang merangkai alur...")

    combined_actions = "\n".join(room["round_actions"])
    recent_context = "\n".join(room["history"][-3:])
    
    prompt_round = (
        f"Riwayat Cerita Sebelumnya:\n{recent_context}\n\n"
        f"Aksi Pemain Ronde Ini:\n{combined_actions}\n\n"
        f"Sebagai Narator, berikan narasi balasan (max 2 kalimat) atas seluruh aksi pemain di atas."
    )

    response = ai_client.models.generate_content(
        model="gemini-2.5-flash",
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
    
    await client.send_message(chat_id, f"📜 **[NARASI ROUND]**\n\n{narasi_baru}")
    await prompt_next_turn(client, chat_id)

# ---------------------------------------------------------
# 4. HANDLER START (PRIVATE CHAT & GROUP)
# ---------------------------------------------------------

@app.on_message(filters.command(["start", "help"]) & filters.private)
async def start_private(client, message: Message):
    bot_user = await client.get_me()
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📖 Rules", callback_data="show_rules_pc"),
            InlineKeyboardButton("⚡ Fitur & Format", callback_data="show_features_pc")
        ],
        [
            InlineKeyboardButton("➕ Tambahkan Bot ke Grup", url=f"https://t.me/{bot_user.username}?startgroup=true")
        ]
    ])
    
    text = (
        f"👋 **Halo {message.from_user.first_name}!**\n\n"
        f"Saya adalah **Bot GM Narator Roleplay** berbasis Gemini AI.\n\n"
        f"📌 **Cara Menggunakan Bot:**\n"
        f"1. Tambahkan bot ini ke dalam **Grup Telegram** kamu.\n"
        f"2. Berikan hak akses **Admin** (terutama izin *Pin Messages*).\n"
        f"3. Ketik perintah `/start_rp` di grup untuk membuka Dashboard Game.\n\n"
        f"Klik tombol di bawah untuk melihat **Rules** atau **Fitur & Format**!"
    )
    await message.reply(text, reply_markup=keyboard)


@app.on_message(filters.command(["start_rp", "start"]) & filters.group)
async def start_rp_lobby(client, message: Message):
    room = get_room(message.chat.id)
    if room["status"] != "IDLE":
        await message.reply("Sesi RP sedang berjalan atau dalam lobby.")
        return

    room["status"] = "LOBBY"
    room["players"].clear()
    room["history"].clear()
    room["round_actions"].clear()

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Join RP ⚔️", callback_data="join_rp"), 
            InlineKeyboardButton("Leave 🚪", callback_data="leave_rp")
        ],
        [
            InlineKeyboardButton("📖 Rules", callback_data="show_rules"), 
            InlineKeyboardButton("⚡ Fitur & Format", callback_data="show_features")
        ],
        [
            InlineKeyboardButton("Mulai Cerita 🚀", callback_data="start_story")
        ]
    ])
    
    welcome_text = (
        f"✨ **DASHBOARD ROLEPLAY** ✨\n\n"
        f"Sesi Roleplay (RP) baru telah dibuka!\n"
        f"• Tema saat ini: **{room['theme']}** *(Ubah via `/tema <nama tema>`)*\n\n"
        f"Silakan klik **Join RP** untuk bergabung (menggunakan nama depan Telegram kamu)."
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
            "📖 **ATURAN PERMAINAN (RULES)**\n\n"
            "1. **Antrean Giliran (Turn-Based):** Bertindaklah HANYA saat giliran kamu dipanggil bot.\n"
            "2. **AFK Timer (2 Menit):** Giliran otomatis di-skip jika diam selama 2 menit.\n"
            "3. **Nama Karakter:** Menggunakan Nama Depan (first_name) akun Telegram.\n"
            "4. **Format RP:** Gunakan `\"dialog\"` dan `;aksi;`."
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="back_to_pc")]])
        await callback.message.edit_text(rules_text, reply_markup=keyboard)

    elif data == "show_features_pc":
        features_text = (
            "⚡ **FITUR & FORMAT PESAN**\n\n"
            "📌 **Format Aksi Pemain:**\n"
            "• Dialog: `\"Halo, siapa di sana?\"`\n"
            "• Aksi: `;menarik pedang dari sarungnya;`\n"
            "• Kombo: `\"Tunggu aku!\" ;berlari menyusul Ken;`\n\n"
            "🛠️ **Daftar Perintah Grup:**\n"
            "• `/start_rp` - Buka Lobby Baru\n"
            "• `/tema <nama>` - Ubah tema cerita\n"
            "• `/skip` - Lewati giliran manual\n"
            "• `/selesai_rp` - Akhiri permainan"
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data="back_to_pc")]])
        await callback.message.edit_text(features_text, reply_markup=keyboard)

    elif data == "back_to_pc":
        bot_user = await client.get_me()
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📖 Rules", callback_data="show_rules_pc"),
                InlineKeyboardButton("⚡ Fitur & Format", callback_data="show_features_pc")
            ],
            [
                InlineKeyboardButton("➕ Tambahkan Bot ke Grup", url=f"https://t.me/{bot_user.username}?startgroup=true")
            ]
        ])
        text = (
            f"👋 **Halo {callback.from_user.first_name}!**\n\n"
            f"Saya adalah **Bot GM Narator Roleplay** berbasis Gemini AI.\n\n"
            f"Klik tombol di bawah untuk informasi selengkapnya atau langsung tambahkan saya ke grup!"
        )
        await callback.message.edit_text(text, reply_markup=keyboard)

    elif data == "show_rules":
        rules_text = (
            "📖 **ATURAN PERMAINAN (RULES)**\n\n"
            "1. Bertindaklah HANYA saat giliran nama kamu dipanggil bot.\n"
            "2. Batas waktu per giliran adalah 2 menit (auto-skip jika AFK).\n"
            "3. Menggunakan Nama Depan Telegram."
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali ke Dashboard", callback_data="back_to_dashboard")]])
        await callback.message.edit_text(rules_text, reply_markup=keyboard)

    elif data == "show_features":
        features_text = (
            "⚡ **FORMAT PESAN:**\n"
            "• Dialog: `\"isi dialog\"` \n"
            "• Aksi: `;isi aksi;`\n"
            "• Kombo: `\"dialog\" ;aksi;`"
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali ke Dashboard", callback_data="back_to_dashboard")]])
        await callback.message.edit_text(features_text, reply_markup=keyboard)

    elif data == "back_to_dashboard":
        room = get_room(callback.message.chat.id)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Join RP ⚔️", callback_data="join_rp"), InlineKeyboardButton("Leave 🚪", callback_data="leave_rp")],
            [InlineKeyboardButton("📖 Rules", callback_data="show_rules"), InlineKeyboardButton("⚡ Fitur & Format", callback_data="show_features")],
            [InlineKeyboardButton("Mulai Cerita 🚀", callback_data="start_story")]
        ])
        welcome_text = (
            f"✨ **DASHBOARD ROLEPLAY** ✨\n\n"
            f"Sesi Roleplay (RP) baru telah dibuka!\n"
            f"• Tema saat ini: **{room['theme']}** *(Ubah via `/tema <nama tema>`)*\n\n"
            f"Silakan klik **Join RP** untuk bergabung."
        )
        await callback.message.edit_text(welcome_text, reply_markup=keyboard)

# ---------------------------------------------------------
# 6. COMMAND & GAME HANDLERS
# ---------------------------------------------------------

@app.on_message(filters.command("tema") & filters.group)
async def set_theme(client, message: Message):
    room = get_room(message.chat.id)
    if room["status"] != "LOBBY":
        await message.reply("Tema hanya bisa diubah saat masih di Lobby!")
        return

    if len(message.command) < 2:
        await message.reply("Contoh: `/tema Mafia Sicily`")
        return

    new_theme = " ".join(message.command[1:])
    room["theme"] = new_theme
    await message.reply(f"🎨 Tema cerita diubah menjadi: **{new_theme}**")

@app.on_callback_query(filters.regex("^(join_rp|leave_rp|start_story)$"))
async def handle_lobby_buttons(client, callback: CallbackQuery):
    chat_id = callback.message.chat.id
    user = callback.from_user
    room = get_room(chat_id)

    if callback.data == "join_rp":
        room["players"][user.id] = user.first_name
        await callback.answer(f"Kamu bergabung sebagai {user.first_name}!", show_alert=False)
        
    elif callback.data == "leave_rp":
        if user.id in room["players"]:
            del room["players"][user.id]
            await callback.answer("Kamu keluar dari lobby.", show_alert=False)

    elif callback.data == "start_story":
        if len(room["players"]) < 1:
            await callback.answer("Minimal 1 pemain!", show_alert=True)
            return

        if room["lobby_msg_id"]:
            try:
                await client.unpin_chat_message(chat_id, room["lobby_msg_id"])
            except Exception:
                pass

        room["status"] = "PLAYING"
        room["turn_order"] = list(room["players"].keys())
        room["current_turn_index"] = 0

        await callback.message.edit_text("⏳ **Narator sedang menyusun takdir awal...**")
        
        player_names = ", ".join(room["players"].values())
        prompt_pembuka = (
            f"Buatkan narasi pembuka tepat 1 PARAGRAF untuk tema '{room['theme']}'. "
            f"Pemain yang ikut: {player_names}. Berikan peran yang sesuai tema untuk setiap pemain "
            f"menggunakan nama depan mereka, lalu tutup dengan situasi konflik awal."
        )

        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt_pembuka,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                max_output_tokens=150,
                temperature=0.7
            )
        )
        
        narasi_awal = response.text
        room["history"].append(f"[NARATOR]: {narasi_awal}")
        
        await callback.message.reply(f"📜 **[AWAL CERITA]**\n\n{narasi_awal}")
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

    # Batalkan timer AFK karena pemain sudah bertindak!
    cancel_afk_timer(room)

    player_name = room["players"][message.from_user.id]
    
    dialogs = re.findall(r'"([^"]*)"', text)
    actions = re.findall(r';([^;]*);', text)
    
    parsed_parts = []
    if dialogs: parsed_parts.append(f'Bicara: "{ " ".join(dialogs) }"')
    if actions: parsed_parts.append(f'Aksi: ({ " ".join(actions) })')
    
    formatted_action = f"- {player_name}: " + " ".join(parsed_parts)
    
    room["round_actions"].append(formatted_action)
    await message.reply(f"✅ Aksi **{player_name}** dicatat!")
    
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
    await message.reply(f"⏭️ Giliran **{skipped_player_name}** dilewati!")
    
    room["current_turn_index"] += 1
    await prompt_next_turn(client, chat_id)

@app.on_message(filters.command("selesai_rp") & filters.group)
async def stop_rp(client, message: Message):
    room = get_room(message.chat.id)
    if room["status"] == "IDLE":
        await message.reply("Tidak ada sesi RP yang aktif.")
        return

    cancel_afk_timer(room)

    room["status"] = "IDLE"
    room["players"].clear()
    room["history"].clear()
    room["round_actions"].clear()
    await message.reply("🏁 **Sesi Roleplay telah berakhir.** Terima kasih sudah bermain!")

if __name__ == "__main__":
    app.run()
