# bot.py
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from game_state import GameState
from telegram.constants import ChatType
import asyncio
import os
from datetime import datetime

# 初始化游戏状态（群组为单位）
games = {}
latest_input_round = {}

ADMIN_ID = int(os.getenv("ADMIN_ID"))

# 创建游戏局号
def generate_round_id():
    now = datetime.now()
    return f"{now.strftime('%y%m%d')}{str(now.microsecond)[0:3]}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id not in games:
        games[chat_id] = GameState()

    games[chat_id].start_new_round()
    await update.message.reply_photo(photo="https://i.imgur.com/53yb9o.png",caption="🎯 本局下注已开启！请输入格式如 27/10 进行下注")

    context.job_queue.run_once(lock_bets_job, when=20, data=chat_id, name=str(chat_id))
                                    
async def handle_bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    print("当前下注状态：", chat_id, getattr(games.get(chat_id), "is_betting_open", "无状态"))

    if chat_id not in games or not games[chat_id].is_betting_open:
        await update.message.reply_text("⚠️ 当前无法下注，可能本局尚未开始或已经锁注！")
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
    print(f"记录下注：号码={number} 金额={amount}")
    await update.message.reply_text("✅ 下注成功！下注后不能修改或撤回")

async def handle_open_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != ChatType.PRIVATE or update.effective_user.id != ADMIN_ID:
        return

    user_id = update.effective_user.id
    group_id = latest_input_round.get(user_id)
    if not group_id or group_id not in games:
        return

    if not games[group_id].is_waiting_result:
        return

    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("⚠️ 请输入 01–99 的开奖号码")
        return

    number = int(text)
    if number < 1 or number > 99:
        await update.message.reply_text("⚠️ 请输入 01–99 的开奖号码")
        return

    games[group_id].is_waiting_result = False
    await context.bot.send_message(group_id, f"🎉 本局开奖号码为：{number:02d}")

    # （可扩展：比对下注记录，判断是否中奖）


async def handle_in(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != ChatType.PRIVATE:
        await update.message.reply_text("⚠️ 请输入此指令于私聊中")
        return

    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⚠️ 你没有权限使用此指令")
        return

    # 找出最近一个有效群组和局号
    if not games:
        await update.message.reply_text("⚠️ 当前没有任何群组正在游戏")
        return

    # 获取最新群组与局号
    latest_group_id = list(games.keys())[-1]
    round_id = games[latest_group_id].round_id

    # 保存上下文状态
    latest_input_round[update.effective_user.id] = latest_group_id

    keyboard = [[
        InlineKeyboardButton(f"输入开奖号码（局号: {round_id}）", callback_data=f"in:{round_id}")
    ]]
    await update.message.reply_text("👇 请选择要输入开奖号码的局号", reply_markup=InlineKeyboardMarkup(keyboard))

async def lock_bets_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.data

    if chat_id in games:
        games[chat_id].is_betting_open = False
        await context.bot.send_photo(
            chat_id=chat_id,
            photo="https://i.imgur.com/hmoP26c.png",
            caption="⛔️ 本局已锁注，无法再下注！"
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data.startswith("in:"):
        group_id = latest_input_round.get(user_id)
        if not group_id or group_id not in games:
            await query.edit_message_text("⚠️ 无效局号或已过期")
            return

        games[group_id].is_waiting_result = True
        await query.edit_message_text("请输入开奖号码（01–99）：")

if __name__ == '__main__':
    app = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, handle_bet))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(CommandHandler("in", handle_in))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, handle_open_number))

    app.run_polling()
