# bot.py
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from game_state import GameState
from telegram.constants import ChatType
from collections import defaultdict
from db import execute_query
import asyncio
import os
from datetime import datetime

# 初始化游戏状态（群组为单位）
games = {}
latest_input_round = {}
round_counter_per_day = defaultdict(int)

ADMIN_ID = int(os.getenv("ADMIN_ID"))

# 创建游戏局号
def generate_round_id():
    now = datetime.now()
    return f"{now.strftime('%y%m%d')}{str(now.microsecond)[0:3]}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id not in games:
        games[chat_id] = GameState(round_counter_per_day)

    games[chat_id].start_new_round(chat_id)
    round_id = games[chat_id].round_id
    await update.message.reply_photo(
    photo="https://i.imgur.com/iXzN6Bm.jpeg",caption=f"🎯 Start Betting 📌{round_id}")

    context.job_queue.run_once(lock_bets_job, when=20, data=chat_id, name=str(chat_id))
                                    
async def handle_bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    name = update.effective_user.full_name

    if chat_id not in games or not games[chat_id].is_betting_open:
        await update.message.reply_text("⚠️ Betting is currently unavailable")
        return

    text = update.message.text.strip()
    if "/" not in text:
        return

    try:
        number_part, amount_part = text.split("/", 1)
        numbers = [int(n) for n in number_part.split("+") if 1 <= int(n) <= 99]
        amount = int(amount_part)
        if not numbers or amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Format error. Use 27+28+29/10")
        return

    round_id = games[chat_id].round_id
    # 执行下注逻辑
    for number in numbers:
        games[chat_id].add_bet(number, amount, user_id, name)
    
        # ✅ 保存到数据库
        execute_query("""
            INSERT INTO bets_2d (group_id, round_id, user_id, user_name, number, amount)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (chat_id, round_id, user_id, name, number, amount))

        print(f"记录下注: 号码={number} 金额={amount}")

    total = len(numbers) * amount
    number_str = ", ".join(f"{n:02d}" for n in numbers)
    await update.message.reply_text(f"✅Successfully")

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
        f"✨ Draw Results ✨\n"
        f"🎯 1st Prize：{w_number:02d}\n"
        f"🎯 Special Prize：{' ~ '.join(f'{n:02d}' for n in t_numbers)}"
    )

    # 获取下注记录（数据库）
    round_id = game.round_id
    bets = execute_query(
        "SELECT number, user_id, user_name, amount FROM bets_2d WHERE round_id = %s AND group_id = %s",
        (round_id, group_id)
    )

    # 结算
    results = []
    for row in bets:
        number = row["number"]
        user_id = row["user_id"]
        name = row["user_name"]
        amount = row["amount"]

        if number == game.winning_w:
            payout = amount * 66
            results.append((user_id, name, number, amount, "1st", payout))
        elif number in game.winning_t:
            payout = amount * 6.6
            results.append((user_id, name, number, amount, "Special", payout))

    if results:
        msg += "\n-------------------------------\n🏆 Winning List 🏆\n"
        for uid, name, num, amt, prize, win in results:
            mention = f"[{name}](tg://user?id={uid})"
            msg += f"{mention} 🎯 Number {num:02d}（{prize}）Bet RM{amt}，Win RM{win:.2f}\n"

    # 创建按钮
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📜 查看历史记录", callback_data=f"view_history:{group_id}")]
    ])

    # 一次发送 文案 + 按钮
    await context.bot.send_message(
        chat_id=group_id,
        text=msg,  # msg 就是 draw results 内容
        parse_mode="Markdown",
        reply_markup=keyboard
    )

async def handle_history_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    data = query.data
    if not data.startswith("view_history:"):
        return

    group_id = int(data.split(":")[1])

    rows = execute_query(
        "SELECT round_id, winning_w, winning_t FROM results_2d WHERE group_id = %s ORDER BY created_at DESC LIMIT 10",
        (group_id,)
    )

    if not rows:
        await query.answer("暂无开奖记录", show_alert=True)
        return

    text = "📜 最近10局开奖记录：\n"
    for row in rows:
        rid, w, t_list = row
        text += f"• {rid}: 🎯{w:02d} ✨{' ~ '.join(f'{n:02d}' for n in t_list)}\n"

    await query.answer(text, show_alert=True)

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

    if chat_id not in games:
        return

    game = games[chat_id]
    game.is_betting_open = False

    # 整理下注信息
    bets = game.get_total_bets()  # { number: [(user_id, name, amount), ...] }
    user_bets = {}

    for number, entries in bets.items():
        for user_id, name, amount in entries:
            if name not in user_bets:
                user_bets[name] = {}
            if amount not in user_bets[name]:
                user_bets[name][amount] = []
            user_bets[name][amount].append(number)

    # 构建参与者下注文字
    lines = ["📋 Participants:"]
    for name, bet_dict in user_bets.items():
        parts = []
        for amount, nums in bet_dict.items():
            nums_str = "+".join(f"{n:02d}" for n in sorted(nums))
            parts.append(f"{nums_str}/{amount}")
        lines.append(f"{name} → {', '.join(parts)}")

    summary_text = "\n".join(lines)

    # 发送锁注图片和下注名单
    await context.bot.send_photo(
        chat_id=chat_id,
        photo="https://i.imgur.com/sTG7AiW.jpeg",  # 你当前使用的锁注图
        caption=f"🚫 Betting has ended for this round!\n\n{summary_text}"
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
    app.add_handler(CallbackQueryHandler(handle_history_button, pattern=r'^view_history:'))

    app.run_polling()
