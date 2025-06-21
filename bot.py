# bot.py
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from game_state import GameState
import asyncio
import os
from datetime import datetime

# 初始化游戏状态（群组为单位）
games = {}

# 创建游戏局号
def generate_round_id():
    now = datetime.now()
    return f"{now.strftime('%y%m%d')}{str(now.microsecond)[0:3]}"

# 开始新一局
def get_start_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("点击下注", callback_data="2d:start")]])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in games:
        games[chat_id] = GameState()
    games[chat_id].start_new_round()
    await update.message.reply_photo(photo="https://i.imgur.com/53yb9o.png",
                                      caption="🎯 本局下注已开启！请输入格式如 27/10 进行下注",
                                      reply_markup=get_start_keyboard())

async def handle_bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in games or not games[chat_id].is_betting_open:
        return

    text = update.message.text.strip()
    if "/" not in text:
        return
    number, amount = text.split("/", 1)
    if not number.isdigit() or not amount.isdigit():
        return
    number = int(number)
    amount = int(amount)
    if number < 1 or number > 99 or amount <= 0:
        return

    games[chat_id].add_bet(number, amount)
    await update.message.reply_text("✅ 下注成功！下注后不能修改或撤回")

async def lock_bets(chat_id, context):
    games[chat_id].is_betting_open = False
    await context.bot.send_photo(chat_id=chat_id, photo="https://i.imgur.com/hmoP26c.png",
                                 caption="⛔️ 本局已锁注，无法再下注！")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    if query.data == "2d:start":
        if chat_id not in games or not games[chat_id].is_betting_open:
            return
        await query.edit_message_reply_markup(None)
        await asyncio.sleep(20)
        await lock_bets(chat_id, context)

if __name__ == '__main__':
    app = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_bet))
    app.add_handler(CallbackQueryHandler(button_handler))

    app.run_polling()
