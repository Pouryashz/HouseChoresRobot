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

Owner-only commands: /nextturn and /setgroup only work for OWNER_ID.
Anyone else gets a random funny refusal.

Setup:
1. pip install -r requirements.txt
2. Set your bot token as an environment variable (don't hardcode it!):
     export HC_BOT_TOKEN="your-token-here"
3. Fill in TURNS below. For each person give a "name", and EITHER a
   "username" (without the @) OR a "user_id" (numeric). A username is
   enough to @-tag them; if they don't have one, use their numeric
   user_id instead (get it by messaging @userinfobot on Telegram, which
   replies with the ID of any account you forward a message from).
4. Set OWNER_ID below to your own numeric Telegram ID (get it from
   @userinfobot the same way).
5. Run: python bot.py
6. Add the bot to your group and send /setgroup once inside it.

The bot registers its commands with Telegram on startup, so they show
up in Telegram's native "/" menu (tap the menu icon next to the text
box, or type "/") with descriptions - no extra buttons needed.

To preview message output without running the bot at all, see
test_preview.py - it imports the text-building functions from this
file directly and prints them to the console.
"""

import json
import logging
import os
import random
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
)
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

# Your numeric Telegram user ID. Only this ID can run "major" commands
# (/nextturn, /setgroup). Run /myid in the bot to find your own ID, then
# put it here. Left as 0 until you fill it in - 0 means "no owner set",
# in which case owner-only commands are open to everyone (fine for
# initial testing, but set this before real use).
OWNER_ID = 0

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
SCHEDULE_CALLBACK = "show_schedule"

FUNNY_WARNINGS = [
    "The trash is filing a missing person's report on {names}.",
    "{names}, the broom has started a countdown. It is not a fan of being ignored.",
    "{names}, the dishes are staging a silent protest. Emphasis on silent, "
    "because you still haven't shown up.",
    "Breaking news: {names} has not been seen anywhere near a trash bag. "
    "Search parties are forming.",
    "{names}, the dust bunnies are unionizing. This is your last warning.",
]

# Shown to anyone who isn't OWNER_ID and tries an owner-only command.
DENIED_MESSAGES = [
    "I'm not working for you!",
    "Nice try. This button only listens to one person, and it's not you.",
    "Access denied. Please direct all complaints to management (Pourya).",
    "I only take orders from my one true boss. You are not him.",
    "Sorry, that command requires a level of authority you simply do not have.",
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


def is_owner(user_id: int) -> bool:
    if OWNER_ID == 0:
        return True  # no owner configured yet - open to everyone
    return user_id == OWNER_ID


def ack_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("OK - I'll do it", callback_data=ACK_CALLBACK)]]
    )


def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("📅 Weekly Schedule", callback_data=SCHEDULE_CALLBACK)]]
    )


# Registered with Telegram on startup (see post_init below) so they show
# up in the native "/" command menu, each with its own description.
BOT_COMMANDS = [
    BotCommand("whoseturn", "See whose turn it is right now"),
    BotCommand("schedule", "See the full weekly rotation"),
    BotCommand("nextturn", "Advance to the next turn (owner only)"),
    BotCommand("setgroup", "Set this chat for reminders (owner only)"),
]


# ---------------------------------------------------------------------------
# Text builders (pure functions - no Telegram calls, easy to test/preview)
# ---------------------------------------------------------------------------


def build_whoseturn_text(state: dict) -> str:
    status = "already confirmed" if state["acknowledged"] else "not confirmed yet"
    return f"This weekend it's {names_only(current_turn(state))}'s turn ({status})."


def build_schedule_text(state: dict) -> str:
    lines = ["📅 <b>Weekly Cleaning Schedule</b>\n"]
    current_idx = state["turn_index"] % len(TURNS)
    for i, people in enumerate(TURNS):
        marker = "👉" if i == current_idx else "  "
        lines.append(f"{marker} Week {i + 1}: {names_only(people)}")
    lines.append("\n(Rotation repeats after the last week.)")
    return "\n".join(lines)


def reminder1_text(people: list) -> str:
    return (
        f"Heads up {mentions_joined(people)} — you're on cleaning + trash "
        f"duty this weekend! Tap below once you're planning to handle it."
    )


def reminder2_text(people: list) -> str:
    return (
        f"Morning reminder: {mentions_joined(people)}, it's still your "
        f"turn to clean and take out the trash this weekend. Tap the "
        f"button once it's sorted!"
    )


def reminder3_text(people: list, warning_index: int = None) -> str:
    warning = (
        FUNNY_WARNINGS[warning_index]
        if warning_index is not None
        else random.choice(FUNNY_WARNINGS)
    )
    return warning.format(names=mentions_joined(people))


def denied_text(index: int = None) -> str:
    return DENIED_MESSAGES[index] if index is not None else random.choice(DENIED_MESSAGES)


# ---------------------------------------------------------------------------
# Owner-only guard
# ---------------------------------------------------------------------------


async def require_owner(update: Update) -> bool:
    """Returns True if the caller is allowed to proceed; otherwise sends a
    funny refusal and returns False."""
    user = update.effective_user
    if is_owner(user.id):
        return True
    await update.message.reply_text(denied_text())
    return False


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hi! I track whose turn it is to clean the house and take out "
        "the trash each weekend, and I nag until someone on that turn "
        "confirms.\n\n"
        "Tap the menu icon next to the text box (or type /) to see all "
        "my commands, or tap the button below for the schedule.",
        reply_markup=start_keyboard(),
    )


async def whoseturn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    await update.message.reply_text(build_whoseturn_text(state))


async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    await update.message.reply_text(build_schedule_text(state), parse_mode=ParseMode.HTML)


async def schedule_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    state = load_state()
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=build_schedule_text(state),
        parse_mode=ParseMode.HTML,
    )


async def nextturn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    state = load_state()
    finished = names_only(current_turn(state))
    advance_turn(state)
    await update.message.reply_text(
        f"Thanks {finished}! Next up: {names_only(current_turn(state))}."
    )


async def setgroup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    state = load_state()
    state["group_chat_id"] = update.effective_chat.id
    save_state(state)
    await update.message.reply_text(
        "Got it — I'll send reminders to this chat from now on."
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
    await _send(context, reminder1_text(current_turn(state)), with_button=True)


async def reminder_2(context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    await _send(context, reminder2_text(current_turn(state)), with_button=True)


async def reminder_3(context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    await _send(context, reminder3_text(current_turn(state)), with_button=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def post_init(application: Application) -> None:
    """Runs once after the bot connects, before polling starts. Registers
    the command list so it shows up in Telegram's native '/' menu."""
    await application.bot.set_my_commands(BOT_COMMANDS)


def main():
    if not TOKEN:
        raise RuntimeError(
            "Set the HC_BOT_TOKEN environment variable before running the bot."
        )

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("whoseturn", whoseturn_command))
    app.add_handler(CommandHandler("schedule", schedule_command))
    app.add_handler(CommandHandler("nextturn", nextturn_command))
    app.add_handler(CommandHandler("setgroup", setgroup_command))
    app.add_handler(CallbackQueryHandler(ack_button_handler, pattern=f"^{ACK_CALLBACK}$"))
    app.add_handler(
        CallbackQueryHandler(schedule_button_handler, pattern=f"^{SCHEDULE_CALLBACK}$")
    )

    for cfg, job in ((REMINDER_1, reminder_1), (REMINDER_2, reminder_2), (REMINDER_3, reminder_3)):
        app.job_queue.run_daily(
            job,
            time=time(hour=cfg["hour"], minute=cfg["minute"], tzinfo=TIMEZONE),
            days=(cfg["weekday"],),
        )

    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()"""
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

Owner-only commands: /nextturn and /setgroup only work for OWNER_ID.
Anyone else gets a random funny refusal.

Setup:
1. pip install -r requirements.txt
2. Set your bot token as an environment variable (don't hardcode it!):
     export HC_BOT_TOKEN="your-token-here"
3. Fill in TURNS below. For each person give a "name", and EITHER a
   "username" (without the @) OR a "user_id" (numeric). A username is
   enough to @-tag them; if they don't have one, use their numeric
   user_id instead (get it by having them run /myid in the group).
4. Set OWNER_ID below to your own numeric Telegram ID (run /myid to
   get it once the bot is running).
5. Run: python bot.py
6. Add the bot to your group and send /setgroup once inside it.

To preview message output without running the bot at all, see
test_preview.py - it imports the text-building functions from this
file directly and prints them to the console.
"""

import json
import logging
import os
import random
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TOKEN = os.environ.get("HC_BOT_TOKEN")

# Your numeric Telegram user ID. Only this ID can run "major" commands
# (/nextturn, /setgroup). Run /myid in the bot to find your own ID, then
# put it here. Left as 0 until you fill it in - 0 means "no owner set",
# in which case owner-only commands are open to everyone (fine for
# initial testing, but set this before real use).
OWNER_ID = 0

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
SCHEDULE_CALLBACK = "show_schedule"

FUNNY_WARNINGS = [
    "The trash is filing a missing person's report on {names}.",
    "{names}, the broom has started a countdown. It is not a fan of being ignored.",
    "{names}, the dishes are staging a silent protest. Emphasis on silent, "
    "because you still haven't shown up.",
    "Breaking news: {names} has not been seen anywhere near a trash bag. "
    "Search parties are forming.",
    "{names}, the dust bunnies are unionizing. This is your last warning.",
]

# Shown to anyone who isn't OWNER_ID and tries an owner-only command.
DENIED_MESSAGES = [
    "I'm not working for you!",
    "Nice try. This button only listens to one person, and it's not you.",
    "Access denied. Please direct all complaints to management (Pourya).",
    "I only take orders from my one true boss. You are not him.",
    "Sorry, that command requires a level of authority you simply do not have.",
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


def is_owner(user_id: int) -> bool:
    if OWNER_ID == 0:
        return True  # no owner configured yet - open to everyone
    return user_id == OWNER_ID


def ack_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("OK - I'll do it", callback_data=ACK_CALLBACK)]]
    )


def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("📅 Weekly Schedule", callback_data=SCHEDULE_CALLBACK)]]
    )


# Persistent button menu shown under the text box. Tapping a button sends
# its label as a plain text message, which menu_button_handler below
# routes to the matching command function. Owner-only buttons are shown
# to everyone (so people know they exist) but still get the funny
# refusal if someone who isn't the owner taps them.
BTN_SCHEDULE = "📅 Weekly Schedule"
BTN_WHOSETURN = "🧹 Who's Turn?"
BTN_MYID = "🆔 My ID"
BTN_NEXTTURN = "⏭ Next Turn"
BTN_SETGROUP = "📍 Set This Group"


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [BTN_WHOSETURN, BTN_SCHEDULE],
            [BTN_NEXTTURN, BTN_SETGROUP],
            [BTN_MYID],
        ],
        resize_keyboard=True,
    )


# ---------------------------------------------------------------------------
# Text builders (pure functions - no Telegram calls, easy to test/preview)
# ---------------------------------------------------------------------------


def build_whoseturn_text(state: dict) -> str:
    status = "already confirmed" if state["acknowledged"] else "not confirmed yet"
    return f"This weekend it's {names_only(current_turn(state))}'s turn ({status})."


def build_schedule_text(state: dict) -> str:
    lines = ["📅 <b>Weekly Cleaning Schedule</b>\n"]
    current_idx = state["turn_index"] % len(TURNS)
    for i, people in enumerate(TURNS):
        marker = "👉" if i == current_idx else "  "
        lines.append(f"{marker} Week {i + 1}: {names_only(people)}")
    lines.append("\n(Rotation repeats after the last week.)")
    return "\n".join(lines)


def reminder1_text(people: list) -> str:
    return (
        f"Heads up {mentions_joined(people)} — you're on cleaning + trash "
        f"duty this weekend! Tap below once you're planning to handle it."
    )


def reminder2_text(people: list) -> str:
    return (
        f"Morning reminder: {mentions_joined(people)}, it's still your "
        f"turn to clean and take out the trash this weekend. Tap the "
        f"button once it's sorted!"
    )


def reminder3_text(people: list, warning_index: int = None) -> str:
    warning = (
        FUNNY_WARNINGS[warning_index]
        if warning_index is not None
        else random.choice(FUNNY_WARNINGS)
    )
    return warning.format(names=mentions_joined(people))


def denied_text(index: int = None) -> str:
    return DENIED_MESSAGES[index] if index is not None else random.choice(DENIED_MESSAGES)


# ---------------------------------------------------------------------------
# Owner-only guard
# ---------------------------------------------------------------------------


async def require_owner(update: Update) -> bool:
    """Returns True if the caller is allowed to proceed; otherwise sends a
    funny refusal and returns False."""
    user = update.effective_user
    if is_owner(user.id):
        return True
    await update.message.reply_text(denied_text())
    return False


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hi! I track whose turn it is to clean the house and take out "
        "the trash each weekend, and I nag until someone on that turn "
        "confirms.\n\n"
        "Use the buttons below, or these commands:\n"
        "/whoseturn - see whose turn it is right now\n"
        "/schedule - see the full weekly rotation\n"
        "/nextturn - manually advance to the next turn (owner only)\n"
        "/setgroup - run this inside your group chat (owner only)\n"
        "/myid - get your Telegram numeric ID",
        reply_markup=main_menu_keyboard(),
    )


async def menu_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Routes a tap on the persistent button menu to the matching command
    function. Each function below already just does update.message.reply_text,
    so it works identically whether it was triggered by /command or by a
    button tap sending that exact text."""
    text = update.message.text
    if text == BTN_WHOSETURN:
        await whoseturn_command(update, context)
    elif text == BTN_SCHEDULE:
        await schedule_command(update, context)
    elif text == BTN_MYID:
        await myid_command(update, context)
    elif text == BTN_NEXTTURN:
        await nextturn_command(update, context)
    elif text == BTN_SETGROUP:
        await setgroup_command(update, context)


async def whoseturn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    await update.message.reply_text(build_whoseturn_text(state))


async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    await update.message.reply_text(build_schedule_text(state), parse_mode=ParseMode.HTML)


async def schedule_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    state = load_state()
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=build_schedule_text(state),
        parse_mode=ParseMode.HTML,
    )


async def nextturn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
    state = load_state()
    finished = names_only(current_turn(state))
    advance_turn(state)
    await update.message.reply_text(
        f"Thanks {finished}! Next up: {names_only(current_turn(state))}."
    )


async def setgroup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return
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
    await _send(context, reminder1_text(current_turn(state)), with_button=True)


async def reminder_2(context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    await _send(context, reminder2_text(current_turn(state)), with_button=True)


async def reminder_3(context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    await _send(context, reminder3_text(current_turn(state)), with_button=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    if not TOKEN:
        raise RuntimeError(
            "Set the HC_BOT_TOKEN environment variable before running the bot."
        )

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("whoseturn", whoseturn_command))
    app.add_handler(CommandHandler("schedule", schedule_command))
    app.add_handler(CommandHandler("nextturn", nextturn_command))
    app.add_handler(CommandHandler("setgroup", setgroup_command))
    app.add_handler(CommandHandler("myid", myid_command))
    app.add_handler(CallbackQueryHandler(ack_button_handler, pattern=f"^{ACK_CALLBACK}$"))
    app.add_handler(
        CallbackQueryHandler(schedule_button_handler, pattern=f"^{SCHEDULE_CALLBACK}$")
    )
    app.add_handler(
        MessageHandler(
            filters.Text([BTN_WHOSETURN, BTN_SCHEDULE, BTN_MYID, BTN_NEXTTURN, BTN_SETGROUP]),
            menu_button_handler,
        )
    )

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
