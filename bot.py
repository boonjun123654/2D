import os
import asyncio
import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (ApplicationBuilder, CommandHandler, MessageHandler,
                          CallbackQueryHandler, ContextTypes, filters)
from game_state import state

TOKEN = os.getenv("TOKEN") or "YOUR_BOT_TOKEN"
ADMIN_ID = int(os.getenv("ADMIN_ID") or 123456789)  # 替换为你自己的 Telegram ID
GROUP_ID = int(os.getenv("GROUP_ID") or -1001234567890)  # 替换为你的群组 ID

print("✅ 正在运行最新版本的 bot.py")
# 开始游戏

async def print_group_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type in ["group", "supergroup"]:
        await update.message.reply_text(f"✅ 本群组 ID 是：{chat.id}")
        print("📢 群组 ID 是：", chat.id)

async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.lower() != "开始":
        return
    if update.effective_chat.type != "group":
        return

    state.reset()
    await context.bot.send_photo(
        chat_id=GROUP_ID,
        photo="https://i.imgur.com/53ybo9o.png",  # 替换为你自己的图片链接
        caption=f"🎯 第 {state.get_round_id()} 局开始下注！\n请输入格式如：27/10 表示下注 27号 RM10\n⏳ 20 秒后自动锁注"
    )
    await asyncio.sleep(20)
    state.lock()

    has_valid_bet = any(len(bet_list) > 0 for bet_list in state.get_all_bets().values())
    if not has_valid_bet:
        await context.bot.send_message(
            chat_id=GROUP_ID,
            text=f"⚠️ 第 {state.get_round_id()} 局无人下注，本轮作废"
        )  
        state.next_round()
        return

    # ✅ 只有有人下注才执行这一段
    await context.bot.send_photo(
        chat_id=GROUP_ID,
        photo="https://i.imgur.com/hmoP26c.png",  # ✅ 替换为你的锁注横幅图
        caption="🔒 已锁注，无法再下注！"
    )



# 玩家下注
async def handle_bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("🎯 handle_bet triggered")
    if state.is_locked():
        return
    try:
        text = update.message.text.strip()
        if "/" not in text:
            return
        num_str, amt_str = text.split("/")
        number = int(num_str)
        amount = int(amt_str)
        if not (1 <= number <= 99):
            return
        user_id = update.effective_user.id
        if state.add_bet(user_id, number, amount):
            await update.message.reply_text(
              f"✅ 下注成功！你下注了 {number} 号，金额 RM{amount}。\n下注后不能修改或撤回"
            )
    except:
        return

# 管理员私聊输入 /in
async def handle_input_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    if update.effective_user.id != ADMIN_ID:
        return
    button = InlineKeyboardButton(state.get_round_id(), callback_data=f"set_result:{state.get_round_id()}")
    keyboard = InlineKeyboardMarkup([[button]])
    await update.message.reply_text("点击当前局号以输入开奖结果：", reply_markup=keyboard)

# 管理员点击局号按钮
async def handle_result_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != ADMIN_ID:
        return
    context.user_data["awaiting_result"] = True
    await query.message.reply_text("请输入开奖号码（01–99）：")

# 管理员输入开奖号码
async def handle_result_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.user_data.get("awaiting_result"):
        return
    number = update.message.text.strip()
    if not (number.isdigit() and 1 <= int(number) <= 99):
        await update.message.reply_text("请输入 01 至 99 之间的数字")
        return

    result = str(number).zfill(2)
    state.set_winning_number(result)
    context.user_data["awaiting_result"] = False

    winners = state.get_winners()
    text = f"🎉 第 {state.get_round_id()} 局开奖结果：{result}\n"
    if winners:
        for uid in winners:
            text += f"🎯 恭喜 <a href='tg://user?id={uid}'>这位玩家</a> 命中！\n"
    else:
        text += "😢 本轮无人中奖"

    await context.bot.send_message(chat_id=GROUP_ID, text=text, parse_mode="HTML")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("getid", print_group_id))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, start_game))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS, handle_bet))
    app.add_handler(CommandHandler("in", handle_input_command))
    app.add_handler(CallbackQueryHandler(handle_result_button, pattern=r"^set_result:"))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, handle_result_input))

    app.run_polling()
