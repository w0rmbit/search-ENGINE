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

# --- Flask App for Koyeb Health Check ---
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
        bot.send_message(chat_id, "🔍 Search features unchanged for now.")
        send_main_menu(chat_id)

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

# --- Run Flask + Bot ---
if __name__ == '__main__':
    print("🤖 Bot is running with Flask health check...")
    threading.Thread(target=run_flask).start()
    bot.polling(none_stop=True)
