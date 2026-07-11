"""Personal nutrition-tracking Telegram bot.

Flow:
  photo or text  ->  LLM analysis  ->  card with inline buttons
                     [✅ Log] [✏️ Fix] [½ / ×2 portion] [🗑 Discard]
  /today /week /chart /goal for summaries and charts.

Run:  python bot.py   (polling mode — no public URL needed)
"""
import logging
import os
import uuid

from dotenv import load_dotenv
load_dotenv()

from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, Update)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

import charts
import llm
import sheets

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s %(message)s",
                    level=logging.INFO)
log = logging.getLogger("calbot")

ALLOWED_IDS = {int(x) for x in
               os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()}
CAL_GOAL = int(os.environ.get("DEFAULT_CALORIE_GOAL", 1800))


# --------------------------------------------------------------- utilities
def authorized(update: Update) -> bool:
    uid = update.effective_user.id
    if uid in ALLOWED_IDS:
        return True
    log.warning("Rejected user %s (%s)", uid, update.effective_user.username)
    return False


def fmt_analysis(a: dict) -> str:
    t = a["total"]
    lines = [f"🍽 *{a.get('meal_name', 'Meal')}*", ""]
    for item in a.get("items", []):
        lines.append(f"  • {item['name']} — {item['portion']} "
                     f"({item['calories']} kcal)")
    conf = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(
        a.get("confidence", ""), "⚪️")
    lines += [
        "",
        f"🔥 *{t['calories']} kcal*",
        f"🥩 Protein {t['protein_g']}g   🍞 Carbs {t['carbs_g']}g   "
        f"🧈 Fat {t['fat_g']}g",
        f"🧂 Sodium {t['sodium_mg']}mg   🍬 Sugar {t['sugar_g']}g",
        "",
        f"{conf} confidence: {a.get('confidence', '?')}",
    ]
    if a.get("notes"):
        lines.append(f"_{a['notes']}_")
    return "\n".join(lines)


def keyboard(meal_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Log it", callback_data=f"log:{meal_id}"),
         InlineKeyboardButton("✏️ Fix", callback_data=f"fix:{meal_id}")],
        [InlineKeyboardButton("½ portion", callback_data=f"half:{meal_id}"),
         InlineKeyboardButton("×2 portion", callback_data=f"dbl:{meal_id}"),
         InlineKeyboardButton("🗑 Discard", callback_data=f"del:{meal_id}")],
    ])


def scale(a: dict, factor: float) -> dict:
    for item in a.get("items", []):
        for k in ("calories", "protein_g", "carbs_g", "fat_g",
                  "sodium_mg", "sugar_g"):
            item[k] = round(item[k] * factor, 1)
    for k in a["total"]:
        a["total"][k] = round(a["total"][k] * factor, 1)
    a["meal_name"] += f" (×{factor})" if factor > 1 else " (½)"
    return a


async def send_card(update, context, analysis: dict, edit_msg=None):
    meal_id = uuid.uuid4().hex[:8]
    context.user_data.setdefault("pending", {})[meal_id] = analysis
    text, kb = fmt_analysis(analysis), keyboard(meal_id)
    if edit_msg:
        await edit_msg.edit_text(text, parse_mode=ParseMode.MARKDOWN,
                                 reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN,
                                        reply_markup=kb)


# ----------------------------------------------------------------- handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    await update.message.reply_text(
        "👋 Send me:\n"
        "📸 a photo of your meal or a nutrition label\n"
        "💬 or just type what you ate ('chicken rice + teh bing')\n\n"
        "Commands:\n"
        "/today — today's totals\n"
        "/week — 7-day summary\n"
        "/chart [days] — progress chart (default 30)\n"
        "/goal <kcal> — set daily calorie goal")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    await update.message.chat.send_action(ChatAction.TYPING)
    msg = await update.message.reply_text("🔍 Analyzing your food...")

    photo = update.message.photo[-1]                 # highest resolution
    file = await photo.get_file()
    image_bytes = bytes(await file.download_as_bytearray())

    try:
        analysis = llm.analyze_food(image_bytes=image_bytes,
                                    description=update.message.caption)
    except Exception as e:
        log.exception("analysis failed")
        await msg.edit_text(f"⚠️ Couldn't analyze that: {e}")
        return
    await send_card(update, context, analysis, edit_msg=msg)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return

    # Are we waiting for a correction to a previous estimate?
    fix_id = context.user_data.pop("awaiting_fix", None)
    if fix_id:
        previous = context.user_data.get("pending", {}).get(fix_id)
        if previous:
            msg = await update.message.reply_text("✏️ Re-estimating...")
            try:
                analysis = llm.refine_estimate(previous, update.message.text)
            except Exception as e:
                await msg.edit_text(f"⚠️ Fix failed: {e}")
                return
            context.user_data["pending"].pop(fix_id, None)
            await send_card(update, context, analysis, edit_msg=msg)
            return

    # Otherwise treat text as a food description
    msg = await update.message.reply_text("🔍 Estimating from description...")
    try:
        analysis = llm.analyze_food(description=update.message.text)
    except Exception as e:
        await msg.edit_text(f"⚠️ Couldn't estimate: {e}")
        return
    await send_card(update, context, analysis, edit_msg=msg)


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in ALLOWED_IDS:
        return
    action, meal_id = q.data.split(":", 1)
    pending = context.user_data.setdefault("pending", {})
    analysis = pending.get(meal_id)
    if analysis is None:
        await q.edit_message_text("⚠️ This card expired — send it again.")
        return

    if action == "log":
        sheets.log_meal(analysis, q.from_user.id)
        pending.pop(meal_id, None)
        s = sheets.today_summary()
        goal = context.user_data.get("goal", CAL_GOAL)
        remaining = goal - s.get("calories", 0)
        bar = "🟩" * min(10, int(10 * s.get("calories", 0) / goal)) or "▫️"
        await q.edit_message_text(
            f"✅ Logged *{analysis['meal_name']}*\n\n"
            f"Today: *{s.get('calories', 0)} / {goal} kcal*\n{bar}\n"
            f"{'💪 ' + str(remaining) + ' kcal left' if remaining >= 0 else '🚨 ' + str(-remaining) + ' kcal over'}",
            parse_mode=ParseMode.MARKDOWN)
    elif action == "del":
        pending.pop(meal_id, None)
        await q.edit_message_text("🗑 Discarded.")
    elif action == "fix":
        context.user_data["awaiting_fix"] = meal_id
        await q.message.reply_text(
            "✏️ Tell me what to fix — e.g. 'it was 2 slices not 3', "
            "'no rice', 'the drink was kopi o kosong'.")
    elif action in ("half", "dbl"):
        analysis = scale(analysis, 0.5 if action == "half" else 2.0)
        pending[meal_id] = analysis
        await q.edit_message_text(fmt_analysis(analysis),
                                  parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=keyboard(meal_id))


async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    s = sheets.today_summary()
    if s["meals"] == 0:
        await update.message.reply_text("Nothing logged today yet. 📭")
        return
    goal = context.user_data.get("goal", CAL_GOAL)
    meals = "\n".join(f"  • {m}" for m in s["meal_names"])
    await update.message.reply_text(
        f"📊 *Today* ({s['meals']} meals)\n{meals}\n\n"
        f"🔥 {s['calories']} / {goal} kcal\n"
        f"🥩 {s['protein_g']}g protein  🍞 {s['carbs_g']}g carbs  "
        f"🧈 {s['fat_g']}g fat\n"
        f"🧂 {s['sodium_mg']}mg sodium  🍬 {s['sugar_g']}g sugar",
        parse_mode=ParseMode.MARKDOWN)


async def week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    df = sheets.fetch_history(days=7)
    if df.empty:
        await update.message.reply_text("No data in the last 7 days. 📭")
        return
    daily = df.groupby(df["date"].dt.date)["calories"].sum()
    goal = context.user_data.get("goal", CAL_GOAL)
    lines = [f"📅 *Last 7 days* (goal {goal} kcal/day)\n"]
    for day, cal in daily.items():
        mark = "🟢" if cal <= goal else "🔴"
        lines.append(f"{mark} {day.strftime('%a %d %b')}: {int(cal)} kcal")
    lines.append(f"\nAvg: *{int(daily.mean())} kcal/day*, "
                 f"{len(daily)}/7 days tracked")
    await update.message.reply_text("\n".join(lines),
                                    parse_mode=ParseMode.MARKDOWN)


async def chart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    days = int(context.args[0]) if context.args else 30
    await update.message.chat.send_action(ChatAction.UPLOAD_PHOTO)
    df = sheets.fetch_history(days=days)
    if df.empty:
        await update.message.reply_text("No data to chart yet. 📭")
        return
    goal = context.user_data.get("goal", CAL_GOAL)
    png = charts.progress_chart(df, goal, days)
    await update.message.reply_photo(png,
                                     caption=f"📈 Last {days} days")


async def goal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    if context.args:
        context.user_data["goal"] = int(context.args[0])
        await update.message.reply_text(
            f"🎯 Daily goal set to {context.args[0]} kcal")
    else:
        g = context.user_data.get("goal", CAL_GOAL)
        await update.message.reply_text(f"🎯 Current goal: {g} kcal/day")


def main():
    app = (Application.builder()
           .token(os.environ["TELEGRAM_BOT_TOKEN"])
           .build())
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("week", week))
    app.add_handler(CommandHandler("chart", chart))
    app.add_handler(CommandHandler("goal", goal))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,
                                   handle_text))
    app.add_handler(CallbackQueryHandler(handle_button))
    log.info("Bot starting (polling)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
