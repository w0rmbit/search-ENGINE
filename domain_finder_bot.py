import os
import time
import threading
import telebot
from telebot import types
from flask import Flask, send_from_directory, request, render_template_string

# --- Directories ---
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# --- Telegram Bot Setup ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    print("Error: BOT_TOKEN environment variable is not set.")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

# --- Flask App for Koyeb Health Check & File Upload ---
app = Flask(__name__)

# User data
user_states = {}
user_data = {}

def reset_user(chat_id):
    user_states[chat_id] = None
    user_data[chat_id] = {'links': {}, 'temp_url': None, 'domains': []}

# --- Flask Routes ---
@app.route('/')
def health():
    return "OK", 200

@app.route('/files/<path:filename>')
def serve_file(filename):
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=True)

UPLOAD_HTML = """
<!doctype html>
<title>Upload File</title>
<h2>📂 Upload a File (max 2GB)</h2>
<form method=post enctype=multipart/form-data>
  <input type=file name=file required>
  <input type=submit value=Upload>
</form>
"""

@app.route('/upload', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        f = request.files['file']
        if f:
            save_path = os.path.join(UPLOAD_DIR, f.filename)
            f.save(save_path)
            file_url = f"{os.getenv('BASE_URL', 'https://your-app.koyeb.app')}/files/{f.filename}"
            return f"""
            ✅ File uploaded successfully! <br>
            🔗 Link: <a href="{file_url}">{file_url}</a> <br><br>
            Now you can go back to Telegram bot and search in it.
            """
    return render_template_string(UPLOAD_HTML)

def run_flask():
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)

# --- Main Menu ---
def send_main_menu(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📤 Add Link (URL)", callback_data="upload_file"),
        types.InlineKeyboardButton("📂 Upload File", callback_data="upload_tg"),
        types.InlineKeyboardButton("🔍 Search", callback_data="search"),
        types.InlineKeyboardButton("🗑 Delete", callback_data="delete")
    )
    bot.send_message(chat_id, "📌 Choose an action:", reply_markup=markup)

# --- Start Command ---
@bot.message_handler(commands=['start'])
def handle_start(message):
    reset_user(message.chat.id)
    send_main_menu(message.chat.id)

# --- File Upload from Telegram ---
@bot.message_handler(content_types=['document'])
def handle_file_upload(message):
    chat_id = message.chat.id
    file_name = message.document.file_name
    file_size = message.document.file_size

    # Check Telegram API size limit
    if file_size > 50 * 1024 * 1024:  # 50MB
        bot.send_message(
            chat_id,
            "⚠️ This file is too big for Telegram Bot API (limit 50MB).\n\n"
            f"👉 Please upload it here instead:\n{os.getenv('BASE_URL', 'https://your-app.koyeb.app')}/upload"
        )
        return

    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        save_path = os.path.join(UPLOAD_DIR, file_name)

        with open(save_path, "wb") as f:
            f.write(downloaded_file)

        file_url = f"{os.getenv('BASE_URL', 'https://your-app.koyeb.app')}/files/{file_name}"

        user_data.setdefault(chat_id, {"links": {}, "domains": []})
        user_data[chat_id]['links'][file_name] = {
            "url": file_url,
            "timestamp": int(time.time())
        }

        bot.send_message(
            chat_id,
            f"✅ File `{file_name}` saved!\n🔗 Link: {file_url}\n\n⚠️ Link expires in 24h.",
            parse_mode="Markdown"
        )
        send_main_menu(chat_id)

    except Exception as e:
        bot.send_message(chat_id, f"⚠️ Failed to save file: {e}")

# --- Callback Handler ---
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id

    if call.data == "upload_file":
        user_states[chat_id] = 'awaiting_url'
        bot.send_message(chat_id, "📤 Send me the file URL.")

    elif call.data == "upload_tg":
        bot.send_message(chat_id, "📂 Send me a file directly in Telegram (<=50MB).\n\n"
                                  "For bigger files use /upload page.")

    elif call.data == "search":
        links = user_data.get(chat_id, {}).get('links', {})
        if not links:
            bot.send_message(chat_id, "⚠️ No links added yet.")
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

# --- Upload Flow (via URL) ---
@bot.message_handler(func=lambda m: user_states.get(m.chat.id) == 'awaiting_url')
def handle_url(message):
    chat_id = message.chat.id
    url = message.text.strip()
    if not url.startswith(('http://', 'https://')):
        bot.send_message(chat_id, "⚠️ Invalid URL. Must start with http:// or https://")
        return
    user_data[chat_id]['temp_url'] = url
    user_states[chat_id] = 'awaiting_filename'
    bot.send_message(chat_id, "✏️ What name do you want to give this file?")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id) == 'awaiting_filename')
def handle_filename(message):
    chat_id = message.chat.id
    file_name = message.text.strip()
    if not file_name:
        bot.send_message(chat_id, "⚠️ Name cannot be empty.")
        return
    url = user_data[chat_id].pop('temp_url', None)
    if not url:
        bot.send_message(chat_id, "⚠️ No URL found.")
        send_main_menu(chat_id)
        return
    user_data[chat_id]['links'][file_name] = {
        "url": url,
        "timestamp": int(time.time())
    }
    bot.send_message(chat_id, f"✅ Link saved as `{file_name}`", parse_mode="Markdown")
    send_main_menu(chat_id)

# --- Search One File ---
def choose_file_for_search(chat_id):
    markup = types.InlineKeyboardMarkup()
    for fname in user_data[chat_id]['links'].keys():
        markup.add(types.InlineKeyboardButton(f"🔍 {fname}", callback_data=f"search_file:{fname}"))
    bot.send_message(chat_id, "Select a link to search:", reply_markup=markup)

# --- Auto-clean expired files ---
def cleanup_expired_files():
    while True:
        now = int(time.time())
        for chat_id in list(user_data.keys()):
            links = user_data[chat_id].get("links", {})
            for file_name, info in list(links.items()):
                if now - info["timestamp"] > 86400:  # 24 hours
                    try:
                        os.remove(os.path.join(UPLOAD_DIR, file_name))
                    except FileNotFoundError:
                        pass
                    del links[file_name]
        time.sleep(600)  # run every 10 minutes

# Start cleanup thread
threading.Thread(target=cleanup_expired_files, daemon=True).start()

# --- Run Flask + Bot ---
if __name__ == '__main__':
    print("🤖 Bot is running with Flask health check and auto-cleaner...")
    threading.Thread(target=run_flask).start()
    bot.polling(none_stop=True)
