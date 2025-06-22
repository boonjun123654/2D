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
    round_id = games[chat_id].round_id
    await update.message.reply_photo(photo="https://i.imgur.com/53yb9o.png",caption=f"🎯 本局下注已开启！\n局号：{round_id}\n\n下注格式：号码/金额")

    context.job_queue.run_once(lock_bets_job, when=20, data=chat_id, name=str(chat_id))
                                    
async def handle_bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    name = update.effective_user.full_name

    if chat_id not in games or not games[chat_id].is_betting_open:
        await update.message.reply_text("⚠️ 当前无法下注")
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

    games[chat_id].add_bet(number, amount, user_id, name)
    print(f"记录下注：号码={number} 金额={amount}")
    await update.message.reply_text("✅ 下注成功！")

async def handle_open_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != ChatType.PRIVATE or update.effective_user.id != ADMIN_ID:
        return

    user_id = update.effective_user.id
    group_id = latest_input_round.get(user_id)
    if not group_id or group_id not in games:
        return

    game = games[group_id]
    if not game.is_waiting_result:
        return

    text = update.message.text.strip()
    lines = text.splitlines()

    if len(lines) != 2:
        await update.message.reply_text("⚠️ 输入格式错误，应为两行：\nW-号码\nT-号码/号码/...")
        return

    try:
        if not lines[0].startswith("W-"):
            raise ValueError
        w_number = int(lines[0][2:])
        if w_number < 1 or w_number > 99:
            raise ValueError

        if not lines[1].startswith("T-"):
            raise ValueError
        t_numbers = [int(n) for n in lines[1][2:].split("/") if 1 <= int(n) <= 99]
        if len(t_numbers) != 5:
            raise ValueError

    except ValueError:
        await update.message.reply_text("⚠️ 输入错误，请用以下格式：\nW-14\nT-15/88/99/87/62")
        return

    game.winning_w = w_number
    game.winning_t = t_numbers
    game.is_waiting_result = False

    # 构造开奖信息
    msg = (
        f"🎉 开奖结果：\n"
        f"🎯 头奖：{w_number:02d}\n"
        f"✨ 特别奖：{' ~ '.join(f'{n:02d}' for n in t_numbers)}"
    )

    # 结算下注结果
    bets = game.get_total_bets()
    results = []
    for number, entries in bets.items():
        for user_id, name, amount in entries:
            if number == game.winning_w:
                payout = amount * 66
                results.append((user_id, name, number, amount, "头奖", payout))
            elif number in game.winning_t:
                payout = amount * 6.6
                results.append((user_id, name, number, amount, "特别奖", payout))

    if results:
        msg += "\n-------------------------------\n🏆 本局中奖名单：\n"
        for uid, name, num, amt, prize, win in results:
            mention = f"[{name}](tg://user?id={uid})"
            msg += f"{mention} 🎯 号码 {num:02d}（{prize}）下注 RM{amt}，赢得 RM{win:.2f}\n"

    # 发送合并后的消息
    await context.bot.send_message(group_id, msg, parse_mode="Markdown")

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
