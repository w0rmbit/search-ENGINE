import os
import re
import io
import threading
import requests
import telebot
from flask import Flask
from telebot import types

# --- Telegram Bot Setup ---
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    print("Error: BOT_TOKEN environment variable is not set.")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

# --- Flask App for Health Check ---
app = Flask(__name__)
@app.route('/')
def health():
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)

# --- Bot State ---
user_states = {}
user_data = {}

def reset_user(chat_id):
    user_states[chat_id] = None
    user_data[chat_id] = {
        'links': {},
        'searched_domains': []
    }

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
        types.InlineKeyboardButton("📤 Add Link", callback_data="upload_file"),
        types.InlineKeyboardButton("🔍 Search", callback_data="search"),
        types.InlineKeyboardButton("🗑 Delete", callback_data="delete"),
        types.InlineKeyboardButton("📄 List Files", callback_data="list_files")
    )
    bot.send_message(chat_id, "📌 Choose an action:", reply_markup=markup)

# --- Start Command ---
@bot.message_handler(commands=['start'])
def handle_start(message):
    if message.chat.id not in user_data:
        reset_user(message.chat.id)
    send_main_menu(message.chat.id)

# --- List Files Command ---
@bot.message_handler(commands=['list'])
def handle_list(message):
    chat_id = message.chat.id
    links = user_data.get(chat_id, {}).get('links', {})
    if not links:
        bot.send_message(chat_id, "⚠️ No files saved yet.")
        return
    msg = "📄 Saved Files:\n"
    for fname, url in links.items():
        msg += f"`{fname}` → {url}\n"
    bot.send_message(chat_id, msg, parse_mode="Markdown")

# --- Callback Handler ---
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id

    if call.data == "upload_file":
        user_states[chat_id] = 'awaiting_url'
        bot.send_message(chat_id, "📤 Send me the file URL.")

    elif call.data == "search":
        choose_file_for_search(chat_id)

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

    elif call.data == "list_files":
        links = user_data.get(chat_id, {}).get('links', {})
        if not links:
            bot.send_message(chat_id, "⚠️ No files saved yet.")
        else:
            msg = "📄 Saved Files:\n"
            for fname, url in links.items():
                msg += f"`{fname}` → {url}\n"
            bot.send_message(chat_id, msg, parse_mode="Markdown")
        send_main_menu(chat_id)

    elif call.data.startswith("search_file:"):
        chat_id = call.message.chat.id
        fname = call.data.split("search_file:")[1]
        if fname not in user_data[chat_id]['links']:
            bot.send_message(chat_id, "⚠️ File not found.")
            send_main_menu(chat_id)
            return
        user_states[chat_id] = f"awaiting_domain:{fname}"
        bot.send_message(chat_id, f"🔍 Send the domain to search in file `{fname}`", parse_mode="Markdown")

# --- Manual URL Upload (auto numbering, skip duplicates) ---
@bot.message_handler(func=lambda m: user_states.get(m.chat.id) == 'awaiting_url')
def handle_url(message):
    chat_id = message.chat.id
    url = message.text.strip()
    if not url.startswith(('http://', 'https://')):
        bot.send_message(chat_id, "⚠️ Invalid URL. Must start with http:// or https://")
        return

    links = user_data[chat_id]['links']
    if url in links.values():
        bot.send_message(chat_id, "⚠️ This link is already saved.")
        user_states[chat_id] = None
        send_main_menu(chat_id)
        return

    file_name = str(len(links) + 1)
    links[file_name] = url

    bot.send_message(chat_id, f"✅ Link saved as `{file_name}`", parse_mode="Markdown")
    user_states[chat_id] = None
    send_main_menu(chat_id)

# --- Forwarded Messages (batch, auto numbering, skip duplicates) ---
@bot.message_handler(func=lambda m: m.text and ("Download" in m.text or "📥" in m.text))
def handle_forwarded_file(message):
    chat_id = message.chat.id
    text = message.text

    link_match = re.search(r'(https?://\S+)', text)
    if not link_match:
        return
    url = link_match.group(1).strip()

    links = user_data[chat_id]['links']
    if url in links.values():
        return  # skip duplicates

    file_name = str(len(links) + 1)
    links[file_name] = url

    if "batch_saved" not in user_data[chat_id]:
        user_data[chat_id]["batch_saved"] = []
    user_data[chat_id]["batch_saved"].append(file_name)

    if message.entities is None and message.caption_entities is None:
        saved = user_data[chat_id].pop("batch_saved", [])
        if saved:
            bot.send_message(
                chat_id,
                f"✅ {len(saved)} new files saved ({saved[0]} → {saved[-1]})",
                parse_mode="Markdown"
            )
            send_main_menu(chat_id)

# --- Search One File ---
def choose_file_for_search(chat_id):
    links = user_data[chat_id]['links']
    if not links:
        bot.send_message(chat_id, "⚠️ No files available to search.")
        send_main_menu(chat_id)
        return
    markup = types.InlineKeyboardMarkup()
    for fname in links.keys():
        markup.add(types.InlineKeyboardButton(f"🔍 {fname}", callback_data=f"search_file:{fname}"))
    bot.send_message(chat_id, "Select a file to search:", reply_markup=markup)

# --- Domain Input Handler ---
@bot.message_handler(func=lambda m: user_states.get(m.chat.id, "").startswith("awaiting_domain:"))
def handle_search_domain(message):
    chat_id = message.chat.id
    state = user_states[chat_id]
    fname = state.split("awaiting_domain:")[1]
    url = user_data[chat_id]['links'].get(fname)
    if not url:
        bot.send_message(chat_id, "⚠️ File URL not found.")
        send_main_menu(chat_id)
        return
    domain = message.text.strip()
    save_searched_domain(chat_id, domain)
    stream_search_with_live_progress(chat_id, url, domain, fname)

# --- Streaming Search ---
def stream_search_with_live_progress(chat_id, url, target_domain, fname):
    try:
        progress_msg = bot.send_message(chat_id, f"⏳ Searching `{target_domain}` in `{fname}`...")
        response = requests.get(url, stream=True, timeout=(10, 60))
        response.raise_for_status()

        found_lines_stream = io.BytesIO()
        total_matches = 0
        pattern = re.compile(re.escape(target_domain), re.IGNORECASE)

        for line in response.iter_lines(decode_unicode=True):
            if line and pattern.search(line):
                found_lines_stream.write((line + "\n").encode("utf-8"))
                total_matches += 1

        bot.edit_message_text(
            chat_id=chat_id,
            message_id=progress_msg.message_id,
            text=f"✅ Search complete — found {total_matches} matches"
        )

        if total_matches > 0:
            found_lines_stream.seek(0)
            bot.send_document(
                chat_id,
                found_lines_stream,
                visible_file_name=f"search_results_{fname}_{target_domain}.txt",
                caption=f"✅ Found {total_matches} matches for `{target_domain}` in `{fname}`",
                parse_mode="Markdown"
            )

    except Exception as e:
        bot.send_message(chat_id, f"⚠️ Error: {e}")
    finally:
        send_main_menu(chat_id)

# --- Run Flask + Bot ---
if __name__ == '__main__':
    print("🤖 Bot is running with Flask health check...")
    threading.Thread(target=run_flask).start()
    bot.polling(none_stop=True)
