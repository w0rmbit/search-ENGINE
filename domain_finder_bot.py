import os
import io
import time
import threading
import zipfile
import requests
import telebot
from flask import Flask, send_from_directory
from telebot import types
import re

# --- Directories ---
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# --- Telegram Bot Setup ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    print("Error: BOT_TOKEN environment variable is not set.")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

# --- Flask App for health check & serving files ---
app = Flask(__name__)

@app.route('/')
def health():
    return "OK", 200

@app.route('/files/<filename>')
def serve_file(filename):
    for chat_id, data in user_data.items():
        links = data.get("links", {})
        if filename in links:
            info = links[filename]
            if int(time.time()) - info["timestamp"] <= 86400:  # 24h validity
                return send_from_directory(UPLOAD_DIR, filename, as_attachment=True)
            else:
                return "⛔ Link expired", 404
    return "⛔ File not found", 404

def run_flask():
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)

# --- Bot State ---
user_states = {}
user_data = {}

def reset_user(chat_id):
    user_states[chat_id] = None
    user_data[chat_id] = {'links': {}, 'temp_url': None, 'searched_domains': []}

def save_searched_domain(chat_id, domain, max_domains=20):
    domains = user_data[chat_id].setdefault('searched_domains', [])
    if domain not in domains:
        domains.append(domain)
        if len(domains) > max_domains:
            domains.pop(0)

# --- Main Menu ---
def send_main_menu(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📤 Add Link/File", callback_data="upload_file"),
        types.InlineKeyboardButton("🔍 Search", callback_data="search"),
        types.InlineKeyboardButton("🗑 Delete", callback_data="delete")
    )
    bot.send_message(chat_id, "📌 Choose an action:", reply_markup=markup)

# --- Start Command ---
@bot.message_handler(commands=['start'])
def handle_start(message):
    reset_user(message.chat.id)
    send_main_menu(message.chat.id)

# --- File Upload Handler (TXT or ZIP) ---
MAX_TG_FILE = 48 * 1024 * 1024  # 48 MB

@bot.message_handler(content_types=['document'])
def handle_file_upload(message):
    chat_id = message.chat.id
    file_name = message.document.file_name
    file_size = message.document.file_size

    # Only accept .txt or .zip
    if not (file_name.lower().endswith(".txt") or file_name.lower().endswith(".zip")):
        bot.send_message(chat_id, "⚠️ Please send `.txt` or `.zip` files only.", parse_mode="Markdown")
        return

    if file_size > MAX_TG_FILE:
        bot.send_message(chat_id, "⚠️ File too big (>48MB). Use /upload page instead.")
        return

    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        save_path = os.path.join(UPLOAD_DIR, file_name)
        with open(save_path, "wb") as f:
            f.write(downloaded_file)

        base_url = os.getenv("BASE_URL", "https://your-app.koyeb.app")
        file_url = f"{base_url}/files/{file_name}"

        user_data.setdefault(chat_id, {"links": {}, "searched_domains": []})
        user_data[chat_id]['links'][file_name] = {"url": file_url, "timestamp": int(time.time())}

        bot.send_message(chat_id,
            f"✅ File saved as `{file_name}`\n\n🔗 Link: {file_url}\n⚠️ Link expires in 24h.",
            parse_mode="Markdown")
        send_main_menu(chat_id)

    except Exception as e:
        bot.send_message(chat_id, f"⚠️ Failed to save file: {e}")

# --- Callback Handler ---
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id

    if call.data == "upload_file":
        bot.send_message(chat_id, "📤 Send me a file (`.txt` or `.zip`) as document.")

    elif call.data == "search":
        links = user_data.get(chat_id, {}).get('links', {})
        if not links:
            bot.send_message(chat_id, "⚠️ No links added yet.")
            send_main_menu(chat_id)
            return
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("🔍 Search one file", callback_data="search_one"),
            types.InlineKeyboardButton("🔎 Search all files", callback_data="search_all"),
            types.InlineKeyboardButton("🕘 Recent Domains", callback_data="recent_domains")
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
            send_main_menu(chat_id)
            return
        markup = types.InlineKeyboardMarkup()
        for d in domains[-10:]:
            markup.add(types.InlineKeyboardButton(f"🔁 {d}", callback_data=f"use_domain:{d}"))
        bot.send_message(chat_id, "Choose a recent domain:", reply_markup=markup)

    elif call.data.startswith("use_domain:"):
        domain = call.data.split("use_domain:")[1]
        markup = types.InlineKeyboardMarkup()
        markup.add(
            types.InlineKeyboardButton("🔍 Search one file", callback_data=f"use_domain_one:{domain}"),
            types.InlineKeyboardButton("🔎 Search all files", callback_data=f"use_domain_all:{domain}")
        )
        bot.send_message(chat_id, f"Use `{domain}` for search:", reply_markup=markup, parse_mode="Markdown")

    elif call.data.startswith("use_domain_one:"):
        domain = call.data.split("use_domain_one:")[1]
        user_states[chat_id] = f"use_existing_domain_one:{domain}"
        choose_file_for_search(chat_id)

    elif call.data.startswith("use_domain_all:"):
        domain = call.data.split("use_domain_all:")[1]
        handle_search_all_with_domain(chat_id, domain)

    elif call.data == "delete":
        links = user_data.get(chat_id, {}).get('links', {})
        if not links:
            bot.send_message(chat_id, "⚠️ No links to delete.")
            send_main_menu(chat_id)
        else:
            markup = types.InlineKeyboardMarkup()
            for fname in links.keys():
                markup.add(types.InlineKeyboardButton(f"🗑 {fname}", callback_data=f"delete_file:{fname}"))
            bot.send_message(chat_id, "Select a link to delete:", reply_markup=markup)

    elif call.data.startswith("delete_file:"):
        fname = call.data.split("delete_file:")[1]
        links = user_data.get(chat_id, {}).get('links', {})
        if fname in links:
            del links[fname]
            bot.send_message(chat_id, f"✅ Link `{fname}` removed.", parse_mode="Markdown")
        else:
            bot.send_message(chat_id, "⚠️ Link not found.")
        send_main_menu(chat_id)

    elif call.data.startswith("search_file:"):
        fname = call.data.split("search_file:")[1]
        if fname in user_data[chat_id]['links']:
            user_states[chat_id] = f"awaiting_domain:{fname}"
            bot.send_message(chat_id, f"🔍 Send me the domain to search in `{fname}`", parse_mode="Markdown")
        else:
            bot.send_message(chat_id, "⚠️ Link not found.")
            send_main_menu(chat_id)

# --- Search Flow ---
def choose_file_for_search(chat_id):
    markup = types.InlineKeyboardMarkup()
    for fname in user_data[chat_id]['links'].keys():
        markup.add(types.InlineKeyboardButton(f"🔍 {fname}", callback_data=f"search_file:{fname}"))
    bot.send_message(chat_id, "Select a link to search:", reply_markup=markup)

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, "").startswith('awaiting_domain:'))
def handle_domain_and_search(message):
    chat_id = message.chat.id
    state = user_states[chat_id]
    fname = state.split("awaiting_domain:")[1]
    file_info = user_data[chat_id]['links'].get(fname)
    if not file_info:
        bot.send_message(chat_id, "⚠️ Link not found.")
        send_main_menu(chat_id)
        return
    target_domain = message.text.strip()
    save_searched_domain(chat_id, target_domain)
    stream_search_file(chat_id, fname, target_domain)

# --- Search one file (supports ZIP and TXT) ---
def stream_search_file(chat_id, fname, target_domain):
    file_info = user_data[chat_id]['links'][fname]
    url = file_info['url']

    try:
        progress_msg = bot.send_message(chat_id, f"⏳ Starting search in `{fname}`...", parse_mode="Markdown")
        response = requests.get(url, stream=True, timeout=(10, 60))
        response.raise_for_status()

        found_lines = io.BytesIO()
        total_matches = 0
        pattern = re.compile(re.escape(target_domain), re.IGNORECASE)

        if fname.lower().endswith(".zip"):
            # Read zip in memory
            zip_bytes = io.BytesIO(response.content)
            with zipfile.ZipFile(zip_bytes) as z:
                for inner_name in z.namelist():
                    with z.open(inner_name) as f:
                        for line in f:
                            line = line.decode(errors='ignore').strip()
                            if pattern.search(line):
                                found_lines.write(f"[{inner_name}] {line}\n".encode("utf-8"))
                                total_matches += 1
        else:
            # TXT file
            for line in response.iter_lines(decode_unicode=True):
                if line and pattern.search(line):
                    found_lines.write((line + "\n").encode("utf-8"))
                    total_matches += 1

        # Send results
        if total_matches > 0:
            found_lines.seek(0)
            bot.send_document(chat_id, found_lines,
                              visible_file_name=f"search_{fname}_{target_domain}.txt",
                              caption=f"✅ Found {total_matches} matches in `{fname}`",
                              parse_mode="Markdown")
        else:
            bot.send_message(chat_id, f"❌ No matches for `{target_domain}` in `{fname}`", parse_mode="Markdown")

    except Exception as e:
        bot.send_message(chat_id, f"⚠️ Error searching `{fname}`: {e}")

    finally:
        send_main_menu(chat_id)

# --- Search All Files ---
@bot.message_handler(func=lambda m: user_states.get(m.chat.id) == "awaiting_domain_all")
def handle_search_all(message):
    chat_id = message.chat.id
    target_domain = message.text.strip()
    save_searched_domain(chat_id, target_domain)
    run_search_all(chat_id, target_domain)

def handle_search_all_with_domain(chat_id, target_domain):
    save_searched_domain(chat_id, target_domain)
    run_search_all(chat_id, target_domain)

def run_search_all(chat_id, target_domain):
    links = user_data.get(chat_id, {}).get('links', {})
    if not links:
        bot.send_message(chat_id, "⚠️ No files to search.")
        send_main_menu(chat_id)
        return

    bot.send_message(chat_id, f"🔎 Searching `{target_domain}` across {len(links)} files...", parse_mode="Markdown")
    total_matches = 0
    found_lines_stream = io.BytesIO()
    pattern = re.compile(re.escape(target_domain), re.IGNORECASE)

    for fname, info in links.items():
        url = info['url']
        try:
            response = requests.get(url, stream=True, timeout=(10, 60))
            response.raise_for_status()
            if fname.lower().endswith(".zip"):
                zip_bytes = io.BytesIO(response.content)
                with zipfile.ZipFile(zip_bytes) as z:
                    for inner_name in z.namelist():
                        with z.open(inner_name) as f:
                            for line in f:
                                line = line.decode(errors='ignore').strip()
                                if pattern.search(line):
                                    found_lines_stream.write(f"[{fname}/{inner_name}] {line}\n".encode("utf-8"))
                                    total_matches += 1
            else:
                for line in response.iter_lines(decode_unicode=True):
                    if line and pattern.search(line):
                        found_lines_stream.write(f"[{fname}] {line}\n".encode("utf-8"))
                        total_matches += 1
        except Exception as e:
            bot.send_message(chat_id, f"⚠️ Error searching `{fname}`: {e}")

    if total_matches > 0:
        found_lines_stream.seek(0)
        bot.send_document(chat_id, found_lines_stream,
                          visible_file_name=f"search_all_{target_domain}.txt",
                          caption=f"✅ Found {total_matches} matches across all files",
                          parse_mode="Markdown")
    else:
        bot.send_message(chat_id, f"❌ No results for `{target_domain}` in any file.", parse_mode="Markdown")

    send_main_menu(chat_id)

# --- Cleanup Expired Files ---
def cleanup_expired_files():
    while True:
        now = int(time.time())
        for chat_id in list(user_data.keys()):
            links = user_data[chat_id].get("links", {})
            for fname, info in list(links.items()):
                if now - info["timestamp"] > 86400:
                    try:
                        os.remove(os.path.join(UPLOAD_DIR, fname))
                    except FileNotFoundError:
                        pass
                    del links[fname]
        time.sleep(600)

threading.Thread(target=cleanup_expired_files, daemon=True).start()

# --- Run Flask + Bot ---
if __name__ == '__main__':
    print("🤖 Bot is running with Flask health check and auto-cleaner...")
    threading.Thread(target=run_flask).start()
    bot.polling(none_stop=True)
