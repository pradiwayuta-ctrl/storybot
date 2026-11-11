import os
import re
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
# 2. STATE ROOM MEMORY (IN-MEMORY)
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
            "lobby_msg_id": None           # ID pesan lobby untuk unpin
        }
    return rooms[chat_id]

# ---------------------------------------------------------
# 3. HELPER FUNCTIONS & TURN MANAGER
# ---------------------------------------------------------
async def prompt_next_turn(client, chat_id):
    room = get_room(chat_id)
    
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
        f"*(Ketik `/skip` jika pemain sedang off/lama)*"
    )

async def process_end_of_round(client, chat_id):
    room = get_room(chat_id)
    await client.send_message(chat_id, "⏳ **Semua pemain selesai bertindak.** Narator sedang merangkai alur...")

    combined_actions = "\n".join(room["round_actions"])
    recent_context = "\n".join(room["history"][-3:]) # Sliding window 3 cerita terakhir
    
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
    
    # Reset buffer ronde & kembalikan antrean giliran ke player pertama
    room["round_actions"].clear()
    room["current_turn_index"] = 0
    
    await client.send_message(chat_id, f"📜 **[NARASI ROUND]**\n\n{narasi_baru}")
    await prompt_next_turn(client, chat_id)

# ---------------------------------------------------------
# 4. BOT HANDLERS & COMMANDS
# ---------------------------------------------------------

@app.on_message(filters.command("start_rp") & filters.group)
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
        [InlineKeyboardButton("Join RP ⚔️", callback_data="join_rp"), InlineKeyboardButton("Leave 🚪", callback_data="leave_rp")],
        [InlineKeyboardButton("Mulai Cerita 🚀", callback_data="start_story")]
    ])
    
    lobby_msg = await message.reply(
        f"🎭 **Sesi RP Dibuka!**\n"
        f"Tema: **{room['theme']}** (Ubah pakai `/tema <nama tema>`)\n\n"
        f"**Format Pesan:**\n"
        f"• Dialog: `\"isi dialog\"` \n"
        f"• Aksi: `;isi aksi;`\n\n"
        f"Klik tombol di bawah untuk bergabung (nama yang dipakai adalah nama depan Telegram kamu).",
        reply_markup=keyboard
    )
    
    # AUTO PIN PESAN LOBBY
    try:
        await lobby_msg.pin(disable_notification=False)
        room["lobby_msg_id"] = lobby_msg.id
    except Exception:
        pass

@app.on_message(filters.command("tema") & filters.group)
async def set_theme(client, message: Message):
    room = get_room(message.chat.id)
    if room["status"] != "LOBBY":
        await message.reply("Tema hanya bisa diubah saat masih di Lobby!")
        return

    if len(message.command) < 2:
        await message.reply("Contoh penggunaan: `/tema Mafia Sicily` atau `/tema Isekai School`")
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
            await callback.answer("Minimal harus ada 1 pemain!", show_alert=True)
            return

        # AUTO UNPIN PESAN LOBBY
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

@app.on_message(filters.group & filters.text & ~filters.command(["start_rp", "selesai_rp", "skip", "tema"]))
async def handle_turn_action(client, message: Message):
    chat_id = message.chat.id
    room = get_room(chat_id)
    
    if room["status"] != "PLAYING":
        return

    active_user_id = room["turn_order"][room["current_turn_index"]]
    if message.from_user.id != active_user_id:
        return # Abaikan jika bukan gilirannya

    text = message.text
    if not ('"' in text or ';' in text):
        return

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

    room["status"] = "IDLE"
    room["players"].clear()
    room["history"].clear()
    room["round_actions"].clear()
    await message.reply("🏁 **Sesi Roleplay telah berakhir.** Terima kasih sudah bermain!")

if __name__ == "__main__":
    app.run()
