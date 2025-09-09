import os
import re
import io
import time
import threading
import requests
import telebot
from flask import Flask, send_from_directory
from telebot import types

# --- Directories ---
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# --- Telegram Bot Setup ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
BASE_URL = os.getenv("BASE_URL", "https://your-app.koyeb.app")  # <-- change this

if not BOT_TOKEN:
    print("Error: BOT_TOKEN environment variable is not set.")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

# --- Flask App (for Koyeb health check + file serving) ---
app = Flask(__name__)

@app.route('/')
def health():
    return "OK", 200

@app.route('/files/<filename>')
def serve_file(filename):
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=True)

def run_flask():
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)

# --- Bot State ---
user_states = {}
user_data = {}  # {chat_id: {"links": {fname: url}, "timestamps": {fname: ts}, "searched_domains": []}}

def reset_user(chat_id):
    user_states[chat_id] = None
    user_data[chat_id] = {
        'links': {},           
        'timestamps': {},      
        'searched_domains': [] 
    }

def save_searched_domain(chat_id, domain, max_domains=20):
    domains = user_data[chat_id].setdefault('searched_domains', [])
    if domain not in domains:
        domains.append(domain)
        if len(domains) > max_domains:
            domains.pop(0)

# --- Auto-cleaner (delete files older than 24h) ---
FILE_TTL = 24 * 3600  # 24 hours in seconds

def cleanup_expired_files():
    while True:
        now = time.time()
        for chat_id, data in list(user_data.items()):
            for fname, ts in list(data.get("timestamps", {}).items()):
                if now - ts > FILE_TTL:
                    try:
                        os.remove(os.path.join(UPLOAD_DIR, fname))
                    except FileNotFoundError:
                        pass
                    data['links'].pop(fname, None)
                    data['timestamps'].pop(fname, None)
                    try:
                        bot.send_message(chat_id, f"⏳ File `{fname}` expired and was auto-deleted.", parse_mode="Markdown")
                    except Exception:
                        pass
        time.sleep(3600)  # run every hour

# --- Main Menu ---
def send_main_menu(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📂 Upload File", callback_data="upload_tg"),
        types.InlineKeyboardButton("🔍 Search", callback_data="search"),
        types.InlineKeyboardButton("🗑 Delete", callback_data="delete")
    )
    bot.send_message(chat_id, "📌 Choose an action:", reply_markup=markup)

# --- Start Command ---
@bot.message_handler(commands=['start'])
def handle_start(message):
    reset_user(message.chat.id)
    bot.send_message(message.chat.id, "👋 Welcome! Forward me a file and I’ll store it (expires after 24h).")
    send_main_menu(message.chat.id)

# --- File Upload from Telegram ---
@bot.message_handler(content_types=['document'])
def handle_file_upload(message):
    chat_id = message.chat.id
    file_info = bot.get_file(message.document.file_id)
    file_name = message.document.file_name

    try:
        # Download file from Telegram
        downloaded_file = bot.download_file(file_info.file_path)
        save_path = os.path.join(UPLOAD_DIR, file_name)

        with open(save_path, "wb") as f:
            f.write(downloaded_file)

        # Generate link
        file_url = f"{BASE_URL}/files/{file_name}"

        # Save
        user_data.setdefault(chat_id, {"links": {}, "timestamps": {}, "searched_domains": []})
        user_data[chat_id]['links'][file_name] = file_url
        user_data[chat_id]['timestamps'][file_name] = time.time()

        bot.send_message(
            chat_id,
            f"✅ File `{file_name}` saved!\n🔗 Link: {file_url}\n🕒 Expires in 24h\n\nNow you can search inside it.",
            parse_mode="Markdown"
        )
        send_main_menu(chat_id)

    except Exception as e:
        bot.send_message(chat_id, f"⚠️ Failed to save file: {e}")

# --- Callback Handler ---
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id

    if call.data == "upload_tg":
        bot.send_message(chat_id, "📂 Send me a file directly in Telegram.")

    elif call.data == "search":
        links = user_data.get(chat_id, {}).get('links', {})
        if not links:
            bot.send_message(chat_id, "⚠️ No files uploaded yet.")
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
        user_states[chat_id] = f"use_existing_domain:{domain}"
        choose_file_for_search(chat_id)

    elif call.data.startswith("use_domain_all:"):
        domain = call.data.split("use_domain_all:")[1]
        handle_search_all_with_domain(chat_id, domain)

    elif call.data == "delete":
        links = user_data.get(chat_id, {}).get('links', {})
        if not links:
            bot.send_message(chat_id, "⚠️ No files to delete.")
            send_main_menu(chat_id)
        else:
            markup = types.InlineKeyboardMarkup()
            for fname in links.keys():
                markup.add(types.InlineKeyboardButton(f"🗑 {fname}", callback_data=f"delete_file:{fname}"))
            bot.send_message(chat_id, "Select a file to delete:", reply_markup=markup)

    elif call.data.startswith("delete_file:"):
        fname = call.data.split("delete_file:")[1]
        links = user_data.get(chat_id, {}).get('links', {})
        if fname in links:
            # also delete file physically
            try:
                os.remove(os.path.join(UPLOAD_DIR, fname))
            except FileNotFoundError:
                pass
            links.pop(fname, None)
            user_data[chat_id]['timestamps'].pop(fname, None)
            bot.send_message(chat_id, f"✅ File `{fname}` removed.", parse_mode="Markdown")
        else:
            bot.send_message(chat_id, "⚠️ File not found.")
        send_main_menu(chat_id)

    elif call.data.startswith("search_file:"):
        fname = call.data.split("search_file:")[1]
        if fname in user_data[chat_id]['links']:
            state = user_states.get(chat_id, "")
            if state.startswith("use_existing_domain:"):
                domain = state.split("use_existing_domain:")[1]
                save_searched_domain(chat_id, domain)
                stream_search_with_live_progress(chat_id, user_data[chat_id]['links'][fname], domain, fname)
            else:
                user_states[chat_id] = f"awaiting_domain:{fname}"
                bot.send_message(chat_id, f"🔍 Send me the domain to search in `{fname}`", parse_mode="Markdown")
        else:
            bot.send_message(chat_id, "⚠️ File not found.")
            send_main_menu(chat_id)

# --- Choose File for Search ---
def choose_file_for_search(chat_id):
    markup = types.InlineKeyboardMarkup()
    for fname in user_data[chat_id]['links'].keys():
        markup.add(types.InlineKeyboardButton(f"🔍 {fname}", callback_data=f"search_file:{fname}"))
    bot.send_message(chat_id, "Select a file to search:", reply_markup=markup)

# --- Search Handlers ---
@bot.message_handler(func=lambda m: user_states.get(m.chat.id, "").startswith('awaiting_domain:'))
def handle_domain_and_search(message):
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
    stream_search_with_live_progress(chat_id, url, domain, fname)

@bot.message_handler(func=lambda m: user_states.get(m.chat.id) == "awaiting_domain_all")
def handle_search_all(message):
    chat_id = message.chat.id
    domain = message.text.strip()
    save_searched_domain(chat_id, domain)
    run_search_all(chat_id, domain)

def handle_search_all_with_domain(chat_id, domain):
    save_searched_domain(chat_id, domain)
    run_search_all(chat_id, domain)

# --- Run search all ---
def run_search_all(chat_id, domain):
    links = user_data.get(chat_id, {}).get('links', {})
    if not links:
        bot.send_message(chat_id, "⚠️ No files to search.")
        send_main_menu(chat_id)
        return

    bot.send_message(chat_id, f"🔎 Searching for `{domain}` across {len(links)} files...", parse_mode="Markdown")
    found_lines_stream = io.BytesIO()
    total_matches = 0
    match_counts = {}
    pattern = re.compile(re.escape(domain), re.IGNORECASE)

    for fname in links.keys():
        match_counts[fname] = 0
        try:
            with open(os.path.join(UPLOAD_DIR, fname), "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if pattern.search(line):
                        found_lines_stream.write(f"[{fname}] {line}".encode("utf-8"))
                        match_counts[fname] += 1
                        total_matches += 1
        except Exception as e:
            bot.send_message(chat_id, f"⚠️ Error searching `{fname}`: {e}")

    # Summary
    summary_lines = [f"📊 Summary for `{domain}`:"]
    for fname, count in match_counts.items():
        summary_lines.append(f"- `{fname}`: {count} match{'es' if count != 1 else ''}")
    bot.send_message(chat_id, "\n".join(summary_lines), parse_mode="Markdown")

    if total_matches > 0:
        found_lines_stream.seek(0)
        bot.send_document(
            chat_id,
            found_lines_stream,
            visible_file_name=f"search_all_{domain}.txt",
            caption=f"✅ Found {total_matches} total matches across all files",
            parse_mode="Markdown"
        )
    else:
        bot.send_message(chat_id, f"❌ No results for `{domain}` in any file.", parse_mode="Markdown")

    send_main_menu(chat_id)

# --- Streaming search one file ---
def stream_search_with_live_progress(chat_id, url, domain, fname):
    try:
        file_path = os.path.join(UPLOAD_DIR, fname)
        if not os.path.exists(file_path):
            bot.send_message(chat_id, "⚠️ File not found on server.")
            return

        progress_msg = bot.send_message(chat_id, "⏳ Starting search...")
        found_lines_stream = io.BytesIO()
        found_count = 0
        lines_processed = 0
        pattern = re.compile(re.escape(domain), re.IGNORECASE)

        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                lines_processed += 1
                if pattern.search(line):
                    found_lines_stream.write(line.encode("utf-8"))
                    found_count += 1

                if lines_processed % 5000 == 0:
                    bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=progress_msg.message_id,
                        text=f"📊 Processed {lines_processed:,} lines — found {found_count}"
                    )

        bot.edit_message_text(
            chat_id=chat_id,
            message_id=progress_msg.message_id,
            text=f"✅ Search complete — found {found_count} matches"
        )

        if found_count > 0:
            found_lines_stream.seek(0)
            bot.send_document(
                chat_id,
                found_lines_stream,
                visible_file_name=f"search_results_{domain}.txt",
                caption=f"✅ Found {found_count} matches for `{domain}` in `{fname}`",
                parse_mode="Markdown"
            )
        else:
            bot.send_message(chat_id, f"❌ No results for `{domain}` in `{fname}`", parse_mode="Markdown")

    except Exception as e:
        bot.send_message(chat_id, f"⚠️ Error: {e}")

    finally:
        send_main_menu(chat_id)

# --- Run Flask + Bot + Cleaner ---
if __name__ == '__main__':
    print("🤖 Bot is running with Flask health check and auto-cleaner...")
    threading.Thread(target=run_flask).start()
    threading.Thread(target=cleanup_expired_files, daemon=True).start()
    bot.polling(none_stop=True)
