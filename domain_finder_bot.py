import os
import io
import re
import time
import threading
from flask import Flask, send_from_directory
from telethon import TelegramClient, events
from telebot import TeleBot, types
import requests

# ---------------- CONFIG ----------------
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
SESSION_NAME = os.getenv('SESSION_NAME', 'userbot')
CHANNEL_USERNAME = os.getenv('CHANNEL_USERNAME')

BOT_TOKEN = os.getenv('BOT_TOKEN')
BASE_URL = os.getenv('BASE_URL', 'https://yourapp.com')
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# --------------- FLASK ------------------
app = Flask(__name__)

@app.route('/')
def health():
    return "OK", 200

@app.route('/files/<filename>')
def serve_file(filename):
    file_path = os.path.join(UPLOAD_DIR, filename)
    if os.path.exists(file_path):
        return send_from_directory(UPLOAD_DIR, filename, as_attachment=True)
    return "File not found", 404

def run_flask():
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)

# --------------- TELEGRAM BOT ------------------
bot = TeleBot(BOT_TOKEN)
user_states = {}
user_data = {}

def reset_user(chat_id):
    user_states[chat_id] = None
    user_data[chat_id] = {'links': {}, 'searched_domains': []}

def save_searched_domain(chat_id, domain, max_domains=20):
    domains = user_data[chat_id].setdefault('searched_domains', [])
    if domain not in domains:
        domains.append(domain)
        if len(domains) > max_domains:
            domains.pop(0)

def send_main_menu(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🔍 Search", callback_data="search"),
        types.InlineKeyboardButton("🕘 Recent Domains", callback_data="recent_domains")
    )
    bot.send_message(chat_id, "📌 Choose an action:", reply_markup=markup)

@bot.message_handler(commands=['start'])
def handle_start(message):
    reset_user(message.chat.id)
    send_main_menu(message.chat.id)

# --- CALLBACK HANDLER ---
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id
    links = user_data.get(chat_id, {}).get('links', {})

    if call.data == "search":
        if not links:
            bot.send_message(chat_id, "⚠️ No files to search yet.")
            send_main_menu(chat_id)
            return
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("🔍 Search one file", callback_data="search_one"),
            types.InlineKeyboardButton("🔎 Search all files", callback_data="search_all")
        )
        bot.send_message(chat_id, "Choose search mode:", reply_markup=markup)

    elif call.data == "search_one":
        choose_file_for_search(chat_id)

    elif call.data == "search_all":
        user_states[chat_id] = "awaiting_domain_all"
        bot.send_message(chat_id, "🔎 Send me the domain to search across all files.")

    elif call.data == "recent_domains":
        domains = user_data.get(chat_id, {}).get('searched_domains', [])
        if not domains:
            bot.send_message(chat_id, "⚠️ No recent domains found.")
        else:
            bot.send_message(chat_id, "🕘 Recent domains:\n" + "\n".join(domains))
        send_main_menu(chat_id)

    elif call.data.startswith("search_file:"):
        fname = call.data.split("search_file:")[1]
        if fname in links:
            user_states[chat_id] = f"awaiting_domain:{fname}"
            bot.send_message(chat_id, f"🔍 Send me the domain to search in `{fname}`", parse_mode="Markdown")
        else:
            bot.send_message(chat_id, "⚠️ File not found.")
            send_main_menu(chat_id)

def choose_file_for_search(chat_id):
    links = user_data.get(chat_id, {}).get('links', {})
    markup = types.InlineKeyboardMarkup()
    for fname in links.keys():
        markup.add(types.InlineKeyboardButton(f"🔍 {fname}", callback_data=f"search_file:{fname}"))
    bot.send_message(chat_id, "Select a file to search:", reply_markup=markup)

# --- SEARCH HANDLERS ---
@bot.message_handler(func=lambda m: user_states.get(m.chat.id, "").startswith('awaiting_domain:'))
def search_one_file(message):
    chat_id = message.chat.id
    state = user_states[chat_id]
    fname = state.split("awaiting_domain:")[1]
    url = user_data[chat_id]['links'].get(fname)
    if not url:
        bot.send_message(chat_id, "⚠️ File not found.")
        send_main_menu(chat_id)
        return

    domain = message.text.strip()
    save_searched_domain(chat_id, domain)
    search_file(chat_id, fname, url, domain)

@bot.message_handler(func=lambda m: user_states.get(m.chat.id) == "awaiting_domain_all")
def search_all_files(message):
    chat_id = message.chat.id
    domain = message.text.strip()
    save_searched_domain(chat_id, domain)
    links = user_data[chat_id]['links']
    if not links:
        bot.send_message(chat_id, "⚠️ No files to search.")
        send_main_menu(chat_id)
        return

    total_matches = 0
    result_stream = io.StringIO()
    pattern = re.compile(re.escape(domain), re.IGNORECASE)

    for fname, url in links.items():
        try:
            resp = requests.get(url, stream=True, timeout=(10, 60))
            for line in resp.iter_lines(decode_unicode=True):
                if line and pattern.search(line):
                    result_stream.write(f"[{fname}] {line}\n")
                    total_matches += 1
        except:
            continue

    if total_matches > 0:
        result_stream.seek(0)
        bot.send_document(chat_id, io.BytesIO(result_stream.getvalue().encode()),
                          visible_file_name=f"search_results_{domain}.txt",
                          caption=f"✅ Found {total_matches} matches")
    else:
        bot.send_message(chat_id, f"❌ No matches found for `{domain}`.", parse_mode="Markdown")
    send_main_menu(chat_id)

def search_file(chat_id, fname, url, domain):
    total_matches = 0
    result_stream = io.StringIO()
    pattern = re.compile(re.escape(domain), re.IGNORECASE)
    try:
        resp = requests.get(url, stream=True, timeout=(10, 60))
        for line in resp.iter_lines(decode_unicode=True):
            if line and pattern.search(line):
                result_stream.write(line + "\n")
                total_matches += 1
    except:
        bot.send_message(chat_id, f"⚠️ Error reading `{fname}`")
        send_main_menu(chat_id)
        return

    if total_matches > 0:
        result_stream.seek(0)
        bot.send_document(chat_id, io.BytesIO(result_stream.getvalue().encode()),
                          visible_file_name=f"{fname}_search_{domain}.txt",
                          caption=f"✅ Found {total_matches} matches in `{fname}`")
    else:
        bot.send_message(chat_id, f"❌ No matches found in `{fname}` for `{domain}`.", parse_mode="Markdown")
    send_main_menu(chat_id)

# --------------- TELETHON USERBOT -----------------
client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

@client.on(events.NewMessage(chats=CHANNEL_USERNAME))
async def download_file(event):
    if event.message.file:
        file_name = event.message.file.name or f"{int(time.time())}"
        save_path = os.path.join(UPLOAD_DIR, file_name)
        await event.message.download_media(save_path)
        print(f"📥 Downloaded: {file_name}")
        # Add file to all users
        for chat_id in user_data.keys():
            user_data[chat_id]['links'][file_name] = f"{BASE_URL}/files/{file_name}"

# --------------- AUTO CLEANUP -----------------
def cleanup_files():
    while True:
        now = int(time.time())
        for fname in os.listdir(UPLOAD_DIR):
            path = os.path.join(UPLOAD_DIR, fname)
            if os.path.isfile(path) and now - os.path.getmtime(path) > 86400:
                os.remove(path)
        time.sleep(600)

# --------------- MAIN -----------------
if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    threading.Thread(target=cleanup_files, daemon=True).start()
    threading.Thread(target=bot.polling, kwargs={'none_stop': True}).start()
    print("🤖 Bot and Userbot running...")
    client.start()
    client.run_until_disconnected()
