"""
House Cleaning Bot
-------------------
Rotates through a list of "turns" (some turns have one person, some
have two or more) and reminds the group whose turn it is to clean
the house / take out the trash each weekend, tagging everyone on
that turn.

Three-stage reminder flow:
  1. Friday evening   - first heads-up, with an "I'll do it OK" button
  2. Saturday morning - second reminder (only sent if nobody clicked yet)
  3. Saturday afternoon - funny final warning (only sent if still nobody clicked)

Clicking the button at any point cancels the remaining reminders for
that week. Anyone on the current turn's tapping counts.

Setup:
1. pip install python-telegram-bot==21.*
2. Set your bot token as an environment variable (don't hardcode it!):
     export HC_BOT_TOKEN="your-token-here"
3. Fill in TURNS below. For each person give a "name", and EITHER a
   "username" (without the @) OR a "user_id" (numeric). A username is
   enough to @-tag them; if they don't have one, use their numeric
   user_id instead (get it by having them run /myid in the group).
4. Run: python bot.py
5. Add the bot to your group and send /setgroup once inside it.
"""

import json
import logging
import os
import random
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TOKEN = os.environ.get("HC_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "Set the HC_BOT_TOKEN environment variable before running the bot."
    )

# Each turn is a list of people. Most turns have one person, some have two.
# For each person: "name" is required. Give "username" (no @) if they have
# one - that's enough to tag them. If they don't have a username, give
# "user_id" (numeric int) instead and they'll still be tagged, via a
# clickable name-mention. If you have neither yet, leave both out - they'll
# just be named, not tagged, until you fill one in.
TURNS = [
    [
        {"name": "Danial", "username": "D4NYAL_BK"},
        {"name": "Pourya", "username": "Pouryashahbazzadeh"},
    ],
    [
        {"name": "Alireza", "username": "Deartahmasebi"},
    ],
    [
        {"name": "Soroush", "username": "try38474727"},
        {"name": "Aydin", "username": "Aydin_Pouladvand"},
    ],
    [
        {"name": "Daniele"},  # no username yet - add "user_id": <int> once known
    ],
]

TIMEZONE = ZoneInfo("Europe/Rome")

# weekday: 0=Monday ... 4=Friday, 5=Saturday, 6=Sunday
REMINDER_1 = dict(weekday=4, hour=19, minute=0)   # Friday evening
REMINDER_2 = dict(weekday=5, hour=10, minute=0)   # Saturday morning
REMINDER_3 = dict(weekday=5, hour=16, minute=0)   # Saturday afternoon (warning)

ACK_CALLBACK = "ack_turn"

FUNNY_WARNINGS = [
    "The trash is filing a missing person's report on {names}.",
    "{names}, the broom has started a countdown. It is not a fan of being ignored.",
    "{names}, the dishes are staging a silent protest. Emphasis on silent, "
    "because you still haven't shown up.",
    "Breaking news: {names} has not been seen anywhere near a trash bag. "
    "Search parties are forming.",
    "{names}, the dust bunnies are unionizing. This is your last warning.",
]

STATE_FILE = Path(__file__).parent / "state.json"

# ---------------------------------------------------------------------------
# State handling
# ---------------------------------------------------------------------------


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"turn_index": 0, "group_chat_id": None, "acknowledged": False}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def current_turn(state: dict) -> list:
    return TURNS[state["turn_index"] % len(TURNS)]


def advance_turn(state: dict) -> None:
    state["turn_index"] = (state["turn_index"] + 1) % len(TURNS)
    state["acknowledged"] = False
    save_state(state)


def mention(person: dict) -> str:
    """HTML-safe mention: @username if set, else a tg://user link via
    user_id, else just the plain name."""
    if person.get("username"):
        return f"@{person['username']}"
    if person.get("user_id"):
        return f'<a href="tg://user?id={person["user_id"]}">{person["name"]}</a>'
    return person["name"]


def names_only(people: list) -> str:
    return " & ".join(p["name"] for p in people)


def mentions_joined(people: list) -> str:
    return " & ".join(mention(p) for p in people)


def is_person_in_turn(user_id: int, username: str, people: list) -> bool:
    username = (username or "").lower()
    for p in people:
        if p.get("user_id") and p["user_id"] == user_id:
            return True
        if p.get("username") and p["username"].lower() == username:
            return True
    return False


def ack_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("OK - I'll do it", callback_data=ACK_CALLBACK)]]
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hi! I track whose turn it is to clean the house and take out "
        "the trash each weekend, and I nag until someone on that turn "
        "confirms.\n\n"
        "Commands:\n"
        "/whoseturn - see whose turn it is right now\n"
        "/nextturn - manually advance to the next turn\n"
        "/setgroup - run this inside your group chat so I know where "
        "to send reminders\n"
        "/myid - get your Telegram numeric ID (useful if you don't "
        "have a username set)"
    )


async def whoseturn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    status = "already confirmed" if state["acknowledged"] else "not confirmed yet"
    await update.message.reply_text(
        f"This weekend it's {names_only(current_turn(state))}'s turn "
        f"({status})."
    )


async def nextturn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    finished = names_only(current_turn(state))
    advance_turn(state)
    await update.message.reply_text(
        f"Thanks {finished}! Next up: {names_only(current_turn(state))}."
    )


async def setgroup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    state["group_chat_id"] = update.effective_chat.id
    save_state(state)
    await update.message.reply_text(
        "Got it — I'll send reminders to this chat from now on."
    )


async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uname = f"@{user.username}" if user.username else "(no username set)"
    await update.message.reply_text(
        f"Name: {user.full_name}\nUsername: {uname}\nUser ID: {user.id}"
    )


async def ack_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    clicker = query.from_user

    state = load_state()
    people = current_turn(state)

    if not is_person_in_turn(clicker.id, clicker.username, people):
        await query.answer(
            "This isn't your turn to confirm 😄", show_alert=True
        )
        return

    await query.answer()
    state["acknowledged"] = True
    save_state(state)

    await query.edit_message_text(
        f"Confirmed by {clicker.first_name} — thanks! No more nagging this week.",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Scheduled reminders
# ---------------------------------------------------------------------------


async def _send(context: ContextTypes.DEFAULT_TYPE, text: str, with_button: bool):
    state = load_state()
    chat_id = state.get("group_chat_id")
    if not chat_id:
        logger.warning("No group_chat_id set yet — run /setgroup in the group.")
        return
    if state["acknowledged"]:
        return  # already confirmed, stay quiet
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=ack_keyboard() if with_button else None,
    )


async def reminder_1(context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    people = current_turn(state)
    await _send(
        context,
        f"Heads up {mentions_joined(people)} — you're on cleaning + trash "
        f"duty this weekend! Tap below once you're planning to handle it.",
        with_button=True,
    )


async def reminder_2(context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    people = current_turn(state)
    await _send(
        context,
        f"Morning reminder: {mentions_joined(people)}, it's still your "
        f"turn to clean and take out the trash this weekend. Tap the "
        f"button once it's sorted!",
        with_button=True,
    )


async def reminder_3(context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    people = current_turn(state)
    warning = random.choice(FUNNY_WARNINGS).format(names=mentions_joined(people))
    await _send(context, warning, with_button=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("whoseturn", whoseturn_command))
    app.add_handler(CommandHandler("nextturn", nextturn_command))
    app.add_handler(CommandHandler("setgroup", setgroup_command))
    app.add_handler(CommandHandler("myid", myid_command))
    app.add_handler(CallbackQueryHandler(ack_button_handler, pattern=f"^{ACK_CALLBACK}$"))

    for cfg, job in ((REMINDER_1, reminder_1), (REMINDER_2, reminder_2), (REMINDER_3, reminder_3)):
        app.job_queue.run_daily(
            job,
            time=time(hour=cfg["hour"], minute=cfg["minute"], tzinfo=TIMEZONE),
            days=(cfg["weekday"],),
        )

    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
