"""NutriSnap — personal nutrition-tracking Telegram bot.

Flow:
  photo or text  ->  LLM analysis  ->  card with inline buttons
                     [✅ Log] [✏️ Fix] [½ / ×2 portion] [🗑 Discard]
  /today /week /chart /goal /limits for summaries, charts and limit meters.

Run:  python bot.py   (polling mode — no public URL needed)
"""
import logging
import os
import re
import uuid
from datetime import time as dtime

from dotenv import load_dotenv
load_dotenv()

from telegram import (BotCommand, InlineKeyboardButton, InlineKeyboardMarkup,
                      Update)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

import charts
import llm
import storage

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s %(message)s",
                    level=logging.INFO)
log = logging.getLogger("nutrisnap")

ALLOWED_IDS = {int(x) for x in
               os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()}
CAL_GOAL = int(os.environ.get("DEFAULT_CALORIE_GOAL", 1800))
SODIUM_LIMIT_MG = float(os.environ.get("DAILY_SODIUM_LIMIT_MG", 2000))
SUGAR_LIMIT_G = float(os.environ.get("DAILY_SUGAR_LIMIT_G", 50))


# --------------------------------------------------------------- utilities
def authorized(update: Update) -> bool:
    uid = update.effective_user.id
    if uid in ALLOWED_IDS:
        return True
    log.warning("Rejected user %s (%s)", uid, update.effective_user.username)
    return False


def sanitize(analysis: dict) -> dict:
    """LLM text may contain Markdown control chars that break Telegram's
    parse_mode=MARKDOWN — strip them from every displayed string."""
    clean = lambda s: re.sub(r"[*_`\[\]]", "", s) if isinstance(s, str) else s
    for key in ("meal_name", "notes"):
        if key in analysis:
            analysis[key] = clean(analysis[key])
    for item in analysis.get("items", []):
        item["name"] = clean(item.get("name", ""))
        item["portion"] = clean(item.get("portion", ""))
    return analysis


def meter(value: float, limit: float, width: int = 10) -> str:
    """🟩🟩🟩▫️… progress bar with %; solid red once over the limit."""
    pct = value / limit if limit > 0 else 0
    if pct > 1:
        return "🟥" * width + f" {int(round(pct * 100))}%"
    filled = int(round(width * pct))
    return "🟩" * filled + "▫️" * (width - filled) + f" {int(round(pct * 100))}%"


def limits_meters(summary: dict) -> str:
    """Sodium + sugar consumed today as % of daily limits."""
    sodium = summary.get("sodium_mg", 0)
    sugar = summary.get("sugar_g", 0)
    lines = [f"🧂 Sodium {int(sodium)} / {SODIUM_LIMIT_MG:g} mg",
             meter(sodium, SODIUM_LIMIT_MG)]
    if sodium > SODIUM_LIMIT_MG:
        lines[-1] += f" 🚨 {int(sodium - SODIUM_LIMIT_MG)} mg over"
    lines += [f"🍬 Sugar {sugar:g} / {SUGAR_LIMIT_G:g} g",
              meter(sugar, SUGAR_LIMIT_G)]
    if sugar > SUGAR_LIMIT_G:
        lines[-1] += f" 🚨 {round(sugar - SUGAR_LIMIT_G, 1):g} g over"
    return "\n".join(lines)


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
    analysis = sanitize(analysis)
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
        "👋 Welcome to NutriSnap! Send me:\n"
        "📸 a photo of your meal or a nutrition label\n"
        "💬 or just type what you ate ('chicken rice + teh bing')\n\n"
        "Commands:\n"
        "/today — today's totals + limit meters\n"
        "/week — 7-day summary\n"
        "/chart [days] — progress chart (default 30)\n"
        "/goal <kcal> — set daily calorie goal\n"
        "/limits — sodium & sugar limits, % consumed today\n"
        "/undo — delete your last logged meal\n"
        "/info — how the meters work, limit sources, full feature list")


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

    # Forwarded text from someone else → whitelist helper: show their ID.
    # (Forwarded photos still go to handle_photo and get analyzed as food.)
    fo = update.message.forward_origin
    if fo is not None:
        u = getattr(fo, "sender_user", None)   # absent if privacy-hidden
        if u is not None:
            await update.message.reply_text(
                f"👤 Forwarded from: {u.full_name}"
                + (f" (@{u.username})" if u.username else "") + "\n"
                f"🆔 Telegram ID: {u.id}\n\n"
                "To whitelist them, add this ID to ALLOWED_USER_IDS in .env "
                "and restart the bot.")
        else:
            await update.message.reply_text(
                "⚠️ Their privacy settings hide the sender ID on forwards. "
                "Ask them to message @userinfobot for their ID, or to send "
                "/start to this bot — their ID then shows up in its log.")
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
        storage.log_meal(analysis, q.from_user.id)
        pending.pop(meal_id, None)
        s = storage.today_summary(q.from_user.id)
        goal = context.user_data.get("goal", CAL_GOAL)
        cal = s.get("calories", 0)
        remaining = goal - cal
        # Keep the itemized card visible in chat: only strip its buttons,
        # then send the confirmation as a separate message.
        await q.edit_message_reply_markup(reply_markup=None)
        await q.message.reply_text(
            f"✅ Logged *{analysis['meal_name']}*\n\n"
            f"🔥 Calories {cal} / {goal} kcal\n{meter(cal, goal)}\n"
            f"{'💪 ' + str(remaining) + ' kcal left' if remaining >= 0 else '🚨 ' + str(-remaining) + ' kcal over'}\n\n"
            f"{limits_meters(s)}",
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
    s = storage.today_summary(update.effective_user.id)
    if s["meals"] == 0:
        await update.message.reply_text("Nothing logged today yet. 📭")
        return
    goal = context.user_data.get("goal", CAL_GOAL)
    meals = "\n".join(f"  • {m}" for m in s["meal_names"])
    await update.message.reply_text(
        f"📊 *Today* ({s['meals']} meals)\n{meals}\n\n"
        f"🔥 Calories {s['calories']} / {goal} kcal\n"
        f"{meter(s['calories'], goal)}\n"
        f"🥩 {s['protein_g']}g protein  🍞 {s['carbs_g']}g carbs  "
        f"🧈 {s['fat_g']}g fat\n\n"
        f"{limits_meters(s)}",
        parse_mode=ParseMode.MARKDOWN)


def fmt_week(df, goal: int) -> str:
    """7-day rundown: per-day calories with over-limit flags, then averages
    and how many days breached the sodium/sugar limits."""
    daily = df.groupby(df["date"].dt.date)[["calories", "protein_g",
                                            "sodium_mg", "sugar_g"]].sum()
    lines = [f"📅 *Last 7 days* (goal {goal} kcal/day)\n"]
    for day, row in daily.iterrows():
        mark = "🟢" if row["calories"] <= goal else "🔴"
        flags = ("" + (" 🧂" if row["sodium_mg"] > SODIUM_LIMIT_MG else "")
                    + (" 🍬" if row["sugar_g"] > SUGAR_LIMIT_G else ""))
        lines.append(f"{mark} {day.strftime('%a %d %b')}: "
                     f"{int(row['calories'])} kcal{flags}")
    n = len(daily)
    over_sodium = int((daily["sodium_mg"] > SODIUM_LIMIT_MG).sum())
    over_sugar = int((daily["sugar_g"] > SUGAR_LIMIT_G).sum())
    lines += [
        f"\nAvg/day: *{int(daily['calories'].mean())} kcal*  "
        f"🥩 {daily['protein_g'].mean():.0f}g  "
        f"🧂 {daily['sodium_mg'].mean():.0f}mg  "
        f"🍬 {daily['sugar_g'].mean():.0f}g",
        f"🧂 Sodium over limit: {over_sodium}/{n} days",
        f"🍬 Sugar over limit: {over_sugar}/{n} days",
        f"{n}/7 days tracked",
    ]
    return "\n".join(lines)


async def week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    df = storage.fetch_history(days=7, user_id=update.effective_user.id)
    if df.empty:
        await update.message.reply_text("No data in the last 7 days. 📭")
        return
    goal = context.user_data.get("goal", CAL_GOAL)
    await update.message.reply_text(fmt_week(df, goal),
                                    parse_mode=ParseMode.MARKDOWN)


async def chart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    days = int(context.args[0]) if context.args else 30
    await update.message.chat.send_action(ChatAction.UPLOAD_PHOTO)
    df = storage.fetch_history(days=days, user_id=update.effective_user.id)
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


async def limits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    s = storage.today_summary(update.effective_user.id)
    await update.message.reply_text(
        "⚖️ *Daily limits* (WHO-based — override in .env)\n\n"
        + limits_meters(s),
        parse_mode=ParseMode.MARKDOWN)


async def undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    name = storage.delete_last(update.effective_user.id)
    if name is None:
        await update.message.reply_text("Nothing to undo. 📭")
        return
    s = storage.today_summary(update.effective_user.id)
    await update.message.reply_text(
        f"↩️ Deleted *{name}*.\n\n{limits_meters(s)}",
        parse_mode=ParseMode.MARKDOWN)


async def daily_summary(context: ContextTypes.DEFAULT_TYPE):
    """9pm push: today's totals + limit meters to every whitelisted user."""
    for uid in ALLOWED_IDS:
        s = storage.today_summary(uid)
        if s["meals"] == 0:
            text = "🌙 Daily check-in: nothing logged today. 📭"
        else:
            goal = context.application.user_data.get(uid, {}).get(
                "goal", CAL_GOAL)
            meals = "\n".join(f"  • {m}" for m in s["meal_names"])
            text = (f"🌙 *Daily summary* ({s['meals']} meals)\n{meals}\n\n"
                    f"🔥 Calories {s['calories']} / {goal} kcal\n"
                    f"{meter(s['calories'], goal)}\n"
                    f"🥩 {s['protein_g']}g protein  🍞 {s['carbs_g']}g carbs  "
                    f"🧈 {s['fat_g']}g fat\n\n"
                    f"{limits_meters(s)}")
        try:
            await context.bot.send_message(uid, text,
                                           parse_mode=ParseMode.MARKDOWN)
        except Exception:
            log.exception("daily summary to %s failed", uid)


def info_text(goal: int) -> str:
    return (
        "ℹ️ *How NutriSnap works*\n\n"
        "Send a meal photo or type what you ate; AI (Gemini) estimates the "
        "nutrition. Nothing is saved until you press ✅ Log — Fix/½/×2/🗑 "
        "adjust or drop the estimate first.\n\n"

        "*How the % meters are computed*\n"
        "% = total from your ✅-logged meals today ÷ daily limit × 100\n"
        "• \"Today\" resets at midnight Singapore time\n"
        "• Only confirmed (✅) meals count\n"
        "• Values are AI estimates from photos/text — read them as trends, "
        "not lab measurements\n\n"

        "*Limits and where they come from*\n"
        f"🧂 Sodium: {SODIUM_LIMIT_MG:g} mg/day — WHO guideline "
        "(≈5 g of salt)\n"
        f"🍬 Sugar: {SUGAR_LIMIT_G:g} g/day — WHO: free sugars <10% of "
        "energy on a 2,000 kcal diet\n"
        f"🔥 Calories: {goal} kcal/day — your personal goal (/goal), "
        "not a WHO value\n"
        "_Note: the bot estimates total sugar, while WHO's limit covers "
        "free sugars — days heavy on fruit/dairy read slightly high._\n"
        "_Limits are configurable in the bot's .env file._\n\n"

        "*Everything you can do*\n"
        "📸 photo — analyze a meal or nutrition label\n"
        "📸+💬 photo with caption — caption overrides the photo: "
        "\"no sugar\", \"oat milk\", \"ate half\"\n"
        "💬 text — \"chicken rice + teh bing\"\n"
        "✏️ Fix — correct an estimate: \"it was 2 slices, no rice\"\n"
        "½ / ×2 — scale the portion before logging\n"
        "/today — today's totals + limit meters\n"
        "/week — 7-day rundown: daily calories, averages, days over limit\n"
        "/chart [days] — progress charts, e.g. /chart 7 (default 30)\n"
        "/goal <kcal> — set your calorie goal, e.g. /goal 1800\n"
        "/limits — sodium & sugar % consumed today\n"
        "/undo — delete your last logged meal\n"
        "↪️ forward someone's message — shows their Telegram ID "
        "(for whitelisting)\n\n"

        "🌙 A daily summary is pushed automatically at 21:00 SGT.")


async def info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    goal = context.user_data.get("goal", CAL_GOAL)
    await update.message.reply_text(info_text(goal),
                                    parse_mode=ParseMode.MARKDOWN)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    log.error("Handler error: %s: %s", type(err).__name__, err)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Something went wrong (often just a network blip) — "
                "please try that again.")
        except Exception:
            pass  # the network may still be down; the log line is enough


async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("today", "Today's totals + limit meters"),
        BotCommand("week", "7-day summary"),
        BotCommand("chart", "Progress chart (default 30 days)"),
        BotCommand("goal", "Set/show daily calorie goal"),
        BotCommand("limits", "Sodium & sugar % of daily limits"),
        BotCommand("undo", "Delete your last logged meal"),
        BotCommand("info", "How % meters work, limit sources, all features"),
        BotCommand("start", "How to use NutriSnap"),
    ])


def main():
    app = (Application.builder()
           .token(os.environ["TELEGRAM_BOT_TOKEN"])
           .connect_timeout(20).read_timeout(20).write_timeout(30)
           .post_init(post_init)
           .build())
    app.add_error_handler(on_error)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("week", week))
    app.add_handler(CommandHandler("chart", chart))
    app.add_handler(CommandHandler("goal", goal))
    app.add_handler(CommandHandler("limits", limits))
    app.add_handler(CommandHandler("undo", undo))
    app.add_handler(CommandHandler("info", info))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,
                                   handle_text))
    app.add_handler(CallbackQueryHandler(handle_button))

    hh, mm = map(int, os.environ.get("DAILY_SUMMARY_TIME", "21:00").split(":"))
    app.job_queue.run_daily(daily_summary,
                            time=dtime(hh, mm, tzinfo=storage.TZ))
    log.info("Daily summary push scheduled for %02d:%02d SGT", hh, mm)
    log.info("NutriSnap starting (polling)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
