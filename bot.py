import os
import json
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("BOT_TOKEN", "YOUR_TOKEN_HERE")
DATA_FILE = "poker_data.json"

# ─── Data helpers ────────────────────────────────────────────────────────────

def load():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"sessions": {}, "history": []}

def save(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_session(data, chat_id):
    cid = str(chat_id)
    if cid not in data["sessions"]:
        data["sessions"][cid] = {
            "active": False,
            "players": {},
            "log": [],
            "cashier": None,
            "controller": None,
            "started": None
        }
    return data["sessions"][cid]

def fmt(n):
    return f"{int(n):,}".replace(",", " ")

def pnl_str(n):
    return ("+" if n >= 0 else "") + fmt(n)

def now_str():
    return datetime.now().strftime("%H:%M")

def neskhod(session):
    total_in = sum(p["buyin"] for p in session["players"].values())
    total_out = sum(p.get("cashout", 0) for p in session["players"].values() if p.get("cashed_out"))
    return total_out - total_in, total_in, total_out

# ─── Commands ────────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "🃏 *Покер-трекер*\n\n"
        "Команды:\n"
        "`/newgame` — начать новую сессию\n"
        "`/cashier` — стать кассиром\n"
        "`/controller` — стать контролёром\n"
        "`/buyin Имя 1000` — бай-ин\n"
        "`/rebuy Имя 500` — ребай\n"
        "`/cashout Имя 7000` — кэш-аут\n"
        "`/table` — текущий стол\n"
        "`/log` — лог операций\n"
        "`/results` — итоги и несход\n"
        "`/endgame` — завершить сессию\n"
        "`/history` — история сессий"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def newgame(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load()
    session = get_session(data, update.effective_chat.id)
    if session["active"]:
        await update.message.reply_text("⚠️ Сессия уже идёт. Сначала /endgame")
        return
    session["active"] = True
    session["players"] = {}
    session["log"] = []
    session["cashier"] = None
    session["controller"] = None
    session["started"] = now_str()
    save(data)
    await update.message.reply_text(
        "✅ Новая сессия начата!\n\n"
        "Кассир → /cashier\n"
        "Контролёр → /controller\n\n"
        "Потом добавляйте игроков через /buyin"
    )

async def set_cashier(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load()
    session = get_session(data, update.effective_chat.id)
    if not session["active"]:
        await update.message.reply_text("Сначала /newgame")
        return
    user = update.effective_user
    session["cashier"] = {"id": user.id, "name": user.first_name}
    save(data)
    await update.message.reply_text(f"💼 {user.first_name} — кассир. Ведёт записи.")

async def set_controller(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load()
    session = get_session(data, update.effective_chat.id)
    if not session["active"]:
        await update.message.reply_text("Сначала /newgame")
        return
    user = update.effective_user
    session["controller"] = {"id": user.id, "name": user.first_name}
    save(data)
    await update.message.reply_text(f"🔍 {user.first_name} — контролёр. Подтверждает операции.")

async def check_cashier(update, session):
    user = update.effective_user
    if not session.get("cashier"):
        await update.message.reply_text("⚠️ Сначала назначь кассира: /cashier")
        return False
    if session["cashier"]["id"] != user.id:
        await update.message.reply_text(f"⛔ Только кассир ({session['cashier']['name']}) может это делать.")
        return False
    return True

async def buyin_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load()
    session = get_session(data, update.effective_chat.id)
    if not session["active"]:
        await update.message.reply_text("Сначала /newgame")
        return
    if not await check_cashier(update, session):
        return
    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text("Использование: `/buyin Имя 1000`", parse_mode="Markdown")
        return
    name = args[0]
    try:
        amt = int(args[1])
    except ValueError:
        await update.message.reply_text("Сумма должна быть числом")
        return
    if name not in session["players"]:
        session["players"][name] = {"buyin": 0, "entries": [], "cashed_out": False, "cashout": 0}
    session["players"][name]["buyin"] += amt
    entry = {"id": len(session["log"]), "time": now_str(), "player": name, "type": "buyin", "amt": amt, "confirmed": False}
    session["players"][name]["entries"].append(entry)
    session["log"].append(entry)
    save(data)

    keyboard = [[InlineKeyboardButton(f"✅ Подтвердить", callback_data=f"confirm_{len(session['log'])-1}")]]
    ctrl = session.get("controller")
    ctrl_mention = f"@{ctx.bot.username}" if not ctrl else ctrl["name"]
    await update.message.reply_text(
        f"💰 *{name}* — бай-ин *{fmt(amt)}*\n"
        f"Всего занёс: {fmt(session['players'][name]['buyin'])}\n\n"
        f"⏳ Ожидает подтверждения от {ctrl_mention if ctrl else 'контролёра'}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def rebuy_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load()
    session = get_session(data, update.effective_chat.id)
    if not session["active"]:
        await update.message.reply_text("Сначала /newgame")
        return
    if not await check_cashier(update, session):
        return
    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text("Использование: `/rebuy Имя 500`", parse_mode="Markdown")
        return
    name = args[0]
    try:
        amt = int(args[1])
    except ValueError:
        await update.message.reply_text("Сумма должна быть числом")
        return
    if name not in session["players"]:
        await update.message.reply_text(f"Игрок {name} не найден. Сначала /buyin")
        return
    session["players"][name]["buyin"] += amt
    entry = {"id": len(session["log"]), "time": now_str(), "player": name, "type": "rebuy", "amt": amt, "confirmed": False}
    session["players"][name]["entries"].append(entry)
    session["log"].append(entry)
    save(data)

    keyboard = [[InlineKeyboardButton("✅ Подтвердить", callback_data=f"confirm_{len(session['log'])-1}")]]
    await update.message.reply_text(
        f"🔄 *{name}* — ребай *{fmt(amt)}*\n"
        f"Всего занёс: {fmt(session['players'][name]['buyin'])}\n\n"
        f"⏳ Ожидает подтверждения",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def cashout_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load()
    session = get_session(data, update.effective_chat.id)
    if not session["active"]:
        await update.message.reply_text("Сначала /newgame")
        return
    if not await check_cashier(update, session):
        return
    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text("Использование: `/cashout Имя 7000`", parse_mode="Markdown")
        return
    name = args[0]
    try:
        amt = int(args[1])
    except ValueError:
        await update.message.reply_text("Сумма должна быть числом")
        return
    if name not in session["players"]:
        await update.message.reply_text(f"Игрок {name} не найден")
        return
    session["players"][name]["cashed_out"] = True
    session["players"][name]["cashout"] = amt
    pnl = amt - session["players"][name]["buyin"]
    entry = {"id": len(session["log"]), "time": now_str(), "player": name, "type": "cashout", "amt": amt, "confirmed": False}
    session["log"].append(entry)
    save(data)

    keyboard = [[InlineKeyboardButton("✅ Подтвердить кэш-аут", callback_data=f"confirm_{len(session['log'])-1}")]]
    emoji = "🟢" if pnl >= 0 else "🔴"
    await update.message.reply_text(
        f"{emoji} *{name}* выходит с *{fmt(amt)}*\n"
        f"Занёс: {fmt(session['players'][name]['buyin'])}\n"
        f"P&L: *{pnl_str(pnl)}*\n\n"
        f"⏳ Ожидает подтверждения",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def confirm_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = load()
    session = get_session(data, update.effective_chat.id)
    ctrl = session.get("controller")
    user = update.effective_user

    if not ctrl:
        await query.answer("Контролёр не назначен. /controller")
        return
    if ctrl["id"] != user.id:
        await query.answer(f"Только контролёр ({ctrl['name']}) может подтверждать", show_alert=True)
        return

    idx = int(query.data.split("_")[1])
    if idx < len(session["log"]):
        session["log"][idx]["confirmed"] = True
        entry = session["log"][idx]
        save(data)
        await query.edit_message_reply_markup(reply_markup=None)
        await query.answer("✅ Подтверждено!")
        await ctx.bot.send_message(
            update.effective_chat.id,
            f"✅ *{ctrl['name']}* подтвердил: {entry['player']} — {entry['type']} {fmt(entry.get('amt', 0))}",
            parse_mode="Markdown"
        )

async def table_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load()
    session = get_session(data, update.effective_chat.id)
    if not session["active"] or not session["players"]:
        await update.message.reply_text("Нет активной сессии или игроков")
        return
    nes, total_in, total_out = neskhod(session)
    lines = ["🃏 *Текущий стол*\n"]
    for name, p in sorted(session["players"].items()):
        if p.get("cashed_out"):
            pnl = p["cashout"] - p["buyin"]
            lines.append(f"✓ {name}: занёс {fmt(p['buyin'])} → вышел {fmt(p['cashout'])} ({pnl_str(pnl)})")
        else:
            lines.append(f"▪ {name}: занёс {fmt(p['buyin'])}")
    lines.append(f"\n💵 Всего в кассе: *{fmt(total_in)}*")
    if total_out > 0:
        nes_emoji = "✅" if nes == 0 else "⚠️"
        lines.append(f"{nes_emoji} Несход: *{pnl_str(nes)}*")
    pending = sum(1 for e in session["log"] if not e["confirmed"])
    if pending:
        lines.append(f"⏳ Неподтверждённых операций: {pending}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def log_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load()
    session = get_session(data, update.effective_chat.id)
    if not session["log"]:
        await update.message.reply_text("Лог пуст")
        return
    type_map = {"buyin": "бай-ин", "rebuy": "ребай", "cashout": "кэш-аут"}
    lines = ["📋 *Лог операций*\n"]
    for e in session["log"][-15:]:
        mark = "✅" if e["confirmed"] else "⏳"
        amt = fmt(e.get("amt", 0)) if e.get("amt") else ""
        lines.append(f"{mark} [{e['time']}] {e['player']} — {type_map.get(e['type'], e['type'])} {amt}")
    if len(session["log"]) > 15:
        lines.append(f"\n...и ещё {len(session['log'])-15} операций")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def results_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load()
    session = get_session(data, update.effective_chat.id)
    if not session["players"]:
        await update.message.reply_text("Нет данных")
        return
    nes, total_in, total_out = neskhod(session)
    lines = ["🏆 *Итоги сессии*\n"]
    players_sorted = sorted(session["players"].items(), key=lambda x: (x[1].get("cashout", 0) - x[1]["buyin"]), reverse=True)
    for name, p in players_sorted:
        if p.get("cashed_out"):
            pnl = p["cashout"] - p["buyin"]
            emoji = "🟢" if pnl >= 0 else "🔴"
            lines.append(f"{emoji} *{name}*: {pnl_str(pnl)} ({fmt(p['buyin'])} → {fmt(p['cashout'])})")
        else:
            lines.append(f"⏺ *{name}*: ещё за столом, занёс {fmt(p['buyin'])}")
    lines.append(f"\n💵 Банк: {fmt(total_in)}")
    nes_emoji = "✅" if nes == 0 else "⚠️"
    lines.append(f"{nes_emoji} Несход: *{pnl_str(nes)}*")
    pending = sum(1 for e in session["log"] if not e["confirmed"])
    if pending:
        lines.append(f"\n⚠️ {pending} операций без подтверждения — возможная причина несхода!")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def endgame(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load()
    session = get_session(data, update.effective_chat.id)
    if not session["active"]:
        await update.message.reply_text("Нет активной сессии")
        return
    nes, total_in, total_out = neskhod(session)
    data["history"].append({
        "date": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "players": len(session["players"]),
        "total_in": total_in,
        "total_out": total_out,
        "neskhod": nes,
        "log": session["log"]
    })
    session["active"] = False
    save(data)
    nes_emoji = "✅" if nes == 0 else "⚠️"
    await update.message.reply_text(
        f"🏁 *Сессия завершена*\n\n"
        f"Банк: {fmt(total_in)}\n"
        f"Выплачено: {fmt(total_out)}\n"
        f"{nes_emoji} Несход: *{pnl_str(nes)}*\n\n"
        f"Данные сохранены в /history",
        parse_mode="Markdown"
    )

async def history_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    data = load()
    hist = data.get("history", [])
    if not hist:
        await update.message.reply_text("История пуста")
        return
    lines = ["📅 *История сессий*\n"]
    for h in hist[-10:][::-1]:
        nes_emoji = "✅" if h["neskhod"] == 0 else "⚠️"
        lines.append(
            f"{nes_emoji} {h['date']} · {h['players']} игроков\n"
            f"   Банк {fmt(h['total_in'])} · несход {pnl_str(h['neskhod'])}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("newgame", newgame))
    app.add_handler(CommandHandler("cashier", set_cashier))
    app.add_handler(CommandHandler("controller", set_controller))
    app.add_handler(CommandHandler("buyin", buyin_cmd))
    app.add_handler(CommandHandler("rebuy", rebuy_cmd))
    app.add_handler(CommandHandler("cashout", cashout_cmd))
    app.add_handler(CommandHandler("table", table_cmd))
    app.add_handler(CommandHandler("log", log_cmd))
    app.add_handler(CommandHandler("results", results_cmd))
    app.add_handler(CommandHandler("endgame", endgame))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CallbackQueryHandler(confirm_callback, pattern="^confirm_"))
    print("Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
