"""
House Cleaning Bot
------------------

Rotates through a list of turns and reminds the group whose turn it is
to clean the house / take out the trash.

Flow
----
1. Every REMINDER_EVERY_HOURS hours (between REMINDER_START_HOUR and
   REMINDER_END_HOUR) the bot posts a reminder with an "Accept Turn" button,
   until someone on duty accepts. This runs on its own - nobody needs to
   press /whoseturn.
2. The person on duty accepts, cleans, then presses "Mark as Finished".
3. The bot posts an anonymous rating poll.
4. As soon as everyone who is NOT on duty has voted, the bot closes the poll
   and automatically moves on to the next turn (or after RATING_TIMEOUT_HOURS
   if not everybody votes).
"""

import html
import json
import logging
import os
import random
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllGroupChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    PollHandler,
    ContextTypes,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TOKEN = os.environ.get("HC_BOT_TOKEN")

# Your specific Telegram numeric ID.
# Only you can use /nextturn, /restart, /setgroup, /testreminder.
OWNER_ID = 1738272640

# ---------------------------------------------------------------------------
# Cleaning rotation
# ---------------------------------------------------------------------------

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
        {"name": "Daniele"},
    ],
]

# Total number of people in the house (used to know how many votes we need).
TOTAL_HOUSEMATES = sum(len(turn) for turn in TURNS)

# ---------------------------------------------------------------------------
# Time / reminders
# ---------------------------------------------------------------------------

TIMEZONE = ZoneInfo("Europe/Rome")

# Reminders go out every REMINDER_EVERY_HOURS hours, from START to END hour
# (inclusive), until the person on duty accepts the turn.
# With 9 / 21 / 3 this gives: 09:00, 12:00, 15:00, 18:00, 21:00.
REMINDER_START_HOUR = 9
REMINDER_END_HOUR = 21
REMINDER_EVERY_HOURS = 3

REMINDER_TIMES = [
    time(hour=h, minute=0, tzinfo=TIMEZONE)
    for h in range(REMINDER_START_HOUR, REMINDER_END_HOUR + 1, REMINDER_EVERY_HOURS)
]

# If not everybody has rated after this long, move on to the next turn anyway.
RATING_TIMEOUT_HOURS = 48

# Delete the previous reminder when a new one is sent (keeps the chat tidy).
# Needs the bot to be allowed to delete messages; harmless if it can't.
DELETE_OLD_REMINDERS = True

# Sends a confirmation message every time the bot boots.
# Set to False once you are done testing.
SEND_STARTUP_MESSAGE = True

# ---------------------------------------------------------------------------
# Callback identifiers
# ---------------------------------------------------------------------------

ACCEPT_CALLBACK = "accept_turn"
FINISH_CALLBACK = "finish_turn"
SCHEDULE_CALLBACK = "show_schedule"

# ---------------------------------------------------------------------------
# Funny messages & Poll Options
# ---------------------------------------------------------------------------

STARTUP_MESSAGES = [
    "I'm gonna teach you how to be clean! 🧹",
    "I'm back online. Your dust bunnies have been notified. 🐰",
    "Fresh code, same mission: making you clean. 🧼",
]

FUNNY_WARNINGS = [
    "The trash is filing a missing person's report on {names}.",
    "{names}, the broom has started a countdown. It is not a fan of being ignored.",
    "{names}, the dishes are staging a silent protest. Emphasis on silent, because you still haven't shown up.",
    "Breaking news: {names} has not been seen anywhere near a trash bag. Search parties are forming.",
    "{names}, the dust bunnies are unionizing. This is your last warning.",
    "{names}, the vacuum cleaner just texted me. It says it feels unloved.",
    "Scientists confirm the bathroom has developed its own weather system. {names}, please intervene.",
    "{names}, the mop is wondering if you two are still together.",
    "Alert: the fridge has started a science experiment without a permit. {names}, report for duty.",
    "{names}, the floor says it misses you. It has not seen you in a while.",
    "Dear {names}, the trash can has entered its final form. Please do not wait for the next one.",
    "{names}, a spider has declared the corner of the kitchen an independent nation. Diplomacy is your job.",
]

DENIED_MESSAGES = [
    "I'm not working for you!",
    "Nice try. This button only listens to one person, and it's not you.",
    "Access denied. Please direct all complaints to management (Pourya).",
    "I only take orders from my one true boss. You are not him.",
    "Sorry, that command requires a level of authority you simply do not have.",
]

POLL_OPTIONS = [
    "Poor 🤢",
    "Mehhh! 😒",
    "Acceptable 😐",
    "Good 🙂",
    "Perfect ✨",
]

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

# If your host wipes files on redeploy, point this to a persistent folder,
# e.g. Path("/data/state.json").
STATE_FILE = Path(__file__).parent / "state.json"


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
                state.setdefault("turn_index", 0)
                state.setdefault("group_chat_id", None)
                state.setdefault("acknowledged", False)
                state.setdefault("nag_count", 0)
                state.setdefault("rating", None)
                state.setdefault("last_reminder_id", None)
                return state
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read state.json: %s. Starting fresh.", exc)

    return {
        "turn_index": 0,
        "group_chat_id": None,
        "acknowledged": False,   # True once someone has accepted the turn
        "nag_count": 0,          # how many reminders sent for this turn so far
        "rating": None,          # info about the open rating poll, or None
        "last_reminder_id": None,  # message id of the latest reminder
    }


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Turn helpers
# ---------------------------------------------------------------------------


def current_turn(state: dict) -> list:
    return TURNS[state["turn_index"] % len(TURNS)]


def advance_turn(state: dict) -> None:
    state["turn_index"] = (state["turn_index"] + 1) % len(TURNS)
    state["acknowledged"] = False
    state["nag_count"] = 0
    state["rating"] = None
    state["last_reminder_id"] = None
    save_state(state)


# ---------------------------------------------------------------------------
# Mention helpers
# ---------------------------------------------------------------------------


def mention(person: dict) -> str:
    if person.get("username"):
        return f"@{person['username']}"
    if person.get("user_id"):
        return f'<a href="tg://user?id={person["user_id"]}">{html.escape(person["name"])}</a>'
    return html.escape(person["name"])


def names_only(people: list) -> str:
    return " & ".join(person["name"] for person in people)


def mentions_joined(people: list) -> str:
    # Used inside HTML messages, so the "&" must be escaped.
    return " &amp; ".join(mention(person) for person in people)


def is_person_in_turn(user_id: int, username: str, people: list) -> bool:
    username = (username or "").lower()
    for person in people:
        if person.get("user_id") and person["user_id"] == user_id:
            return True
        if person.get("username") and person["username"].lower() == username:
            return True
    return False


# ---------------------------------------------------------------------------
# Owner
# ---------------------------------------------------------------------------


def is_owner(user_id: int) -> bool:
    if OWNER_ID == 0:
        return True
    return user_id == OWNER_ID


async def require_owner(update: Update) -> bool:
    user = update.effective_user
    if user is None:
        return False
    if is_owner(user.id):
        return True
    await update.message.reply_text(random.choice(DENIED_MESSAGES))
    return False


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------


def accept_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Accept Turn", callback_data=ACCEPT_CALLBACK)]]
    )


def finish_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🧹 Mark as Finished", callback_data=FINISH_CALLBACK)]]
    )


def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("📅 Weekly Schedule", callback_data=SCHEDULE_CALLBACK)]]
    )


# ---------------------------------------------------------------------------
# Telegram command list
# ---------------------------------------------------------------------------

BOT_COMMANDS = [
    BotCommand("start", "Start the bot"),
    BotCommand("whoseturn", "See whose turn it is right now"),
    BotCommand("schedule", "See the full weekly rotation"),
    BotCommand("nextturn", "Advance to the next turn (owner only)"),
    BotCommand("restart", "Restart rotation to Week 1 (owner only)"),
    BotCommand("setgroup", "Set this chat for reminders (owner only)"),
    BotCommand("testreminder", "Send a reminder right now, for testing (owner only)"),
]

# ---------------------------------------------------------------------------
# Text builders
# ---------------------------------------------------------------------------


def reminder_times_text() -> str:
    return ", ".join(t.strftime("%H:%M") for t in REMINDER_TIMES)


def build_whoseturn_text(state: dict) -> str:
    if state.get("rating"):
        status = "finished, waiting for everyone to rate"
    elif state["acknowledged"]:
        status = "accepted, cleaning in progress"
    else:
        status = "not accepted yet"
    return f"This week it's {names_only(current_turn(state))}'s turn ({status})."


def build_schedule_text(state: dict) -> str:
    current_idx = state["turn_index"] % len(TURNS)
    lines = ["📅 <b>Weekly Cleaning Schedule</b>\n"]
    for i, people in enumerate(TURNS):
        marker = "👉" if i == current_idx else "🔹"
        lines.append(f"{marker} <b>Week {i + 1}:</b> {html.escape(names_only(people))}")
    lines.append("\n<i>(The rotation repeats after the last week.)</i>")
    return "\n".join(lines)


def reminder1_text(people: list) -> str:
    return (
        f"Heads up {mentions_joined(people)} — you're on cleaning + trash "
        f"duty this round! Tap below to accept the job."
    )


def nag_text(people: list) -> str:
    warning = random.choice(FUNNY_WARNINGS)
    return warning.format(names=mentions_joined(people))


# ---------------------------------------------------------------------------
# Command registration
# ---------------------------------------------------------------------------


async def register_commands_for_chat(bot, chat_id: int) -> list:
    chat_scope = BotCommandScopeChat(chat_id=chat_id)
    await bot.set_my_commands(BOT_COMMANDS, scope=chat_scope)
    return await bot.get_my_commands(scope=chat_scope)


# ---------------------------------------------------------------------------
# Startup confirmation
# ---------------------------------------------------------------------------


async def notify_owner(bot, text: str) -> None:
    """DM the owner. Only works if the owner has pressed Start on the bot."""
    try:
        await bot.send_message(chat_id=OWNER_ID, text=text)
    except Exception:
        logger.exception("Could not DM owner (have you pressed Start on the bot in private?)")


async def send_startup_confirmation(bot, state: dict) -> None:
    """Proves the message-sending mechanism works every time the bot boots."""
    if not SEND_STARTUP_MESSAGE:
        return

    chat_id = state.get("group_chat_id")

    if not chat_id:
        await notify_owner(
            bot,
            "⚠️ Bot started, but NO group is set, so reminders will NOT be sent.\n"
            "Run /setgroup in the group chat. (If you already did, the host probably "
            "reset state.json.)",
        )
        return

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"{random.choice(STARTUP_MESSAGES)}\n\n"
                f"(Test OK. Reminders go out daily at {reminder_times_text()} "
                f"until the turn is accepted.)"
            ),
        )
        logger.info("Startup confirmation sent to chat %s", chat_id)
    except Exception as exc:
        logger.exception("Startup confirmation FAILED for chat %s", chat_id)
        await notify_owner(bot, f"❌ Could not send to the group ({chat_id}): {exc}")


async def post_init(application: Application) -> None:
    logger.info("Registering Telegram bot commands...")
    await application.bot.set_my_commands(BOT_COMMANDS, scope=BotCommandScopeDefault())
    await application.bot.set_my_commands(BOT_COMMANDS, scope=BotCommandScopeAllPrivateChats())
    await application.bot.set_my_commands(BOT_COMMANDS, scope=BotCommandScopeAllGroupChats())
    logger.info("Global Telegram command registration complete.")

    # Log current state on every startup - makes "why didn't it send"
    # questions answerable from the logs alone.
    state = load_state()
    logger.info(
        "Startup state: turn_index=%s (%s), group_chat_id=%s, acknowledged=%s, "
        "nag_count=%s, rating_open=%s",
        state["turn_index"],
        names_only(current_turn(state)),
        state.get("group_chat_id"),
        state["acknowledged"],
        state.get("nag_count", 0),
        bool(state.get("rating")),
    )
    logger.info("Reminder times (%s): %s", TIMEZONE, reminder_times_text())
    if not state.get("group_chat_id"):
        logger.warning(
            "No group_chat_id set - reminders will NOT be sent until "
            "someone runs /setgroup in the target chat."
        )

    await send_startup_confirmation(application.bot, state)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hi! I track whose turn it is to clean the house and take out the "
        "trash, and I nag every few hours until the person on duty accepts the "
        "job! 🧹\n\n"
        "Here is what I can do:\n"
        "/whoseturn - See whose turn it is right now\n"
        "/schedule - See the full weekly rotation\n\n"
        "Admin commands:\n"
        "/nextturn - Advance to the next turn\n"
        "/restart - Reset the schedule back to Week 1\n"
        "/setgroup - Set this chat for reminders\n"
        "/testreminder - Send a reminder right now, for testing",
        reply_markup=start_keyboard(),
    )


async def whoseturn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    text = build_whoseturn_text(state)

    if state.get("rating"):
        await update.message.reply_text(text)
    elif state["acknowledged"]:
        await update.message.reply_text(text, reply_markup=finish_keyboard())
    else:
        await update.message.reply_text(text, reply_markup=accept_keyboard())


async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    await update.message.reply_text(build_schedule_text(state), parse_mode=ParseMode.HTML)


async def nextturn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return

    state = load_state()

    # If a rating poll is open, close it first.
    rating = state.get("rating")
    if rating:
        try:
            await context.bot.stop_poll(
                chat_id=rating["chat_id"], message_id=rating["message_id"]
            )
        except Exception:
            logger.warning("Could not stop poll during /nextturn", exc_info=True)

    finished = names_only(current_turn(state))
    advance_turn(state)

    await update.message.reply_text(
        f"Manually advanced. Thanks {finished}!\n"
        f"Next up: {names_only(current_turn(state))}."
    )


async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return

    state = load_state()
    state["turn_index"] = 0
    state["acknowledged"] = False
    state["nag_count"] = 0
    state["rating"] = None
    state["last_reminder_id"] = None
    save_state(state)

    await update.message.reply_text(
        "🔄 <b>Rotation Restarted!</b>\n\n"
        "The schedule has been reset back to Week 1 (Danial &amp; Pourya).",
        parse_mode=ParseMode.HTML,
    )


async def setgroup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return

    chat = update.effective_chat
    if chat is None:
        await update.message.reply_text("I couldn't determine which chat this is.")
        return

    state = load_state()
    state["group_chat_id"] = chat.id
    save_state(state)

    try:
        await register_commands_for_chat(context.bot, chat.id)
    except Exception:
        logger.exception("Failed to register commands for chat %s", chat.id)
        await update.message.reply_text("Group saved, but I couldn't register the command menu. Check logs.")
        return

    await update.message.reply_text(
        "Got it — I'll send reminders to this chat from now on, "
        f"every few hours ({reminder_times_text()}) until someone accepts the turn.\n\n"
        "The command menu has also been registered specifically for this group."
    )


async def testreminder_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner-only: fires a reminder immediately, in THIS chat,
    regardless of the schedule or whether group_chat_id is set.
    Does not touch nag_count or acknowledged state."""
    if not await require_owner(update):
        return

    state = load_state()
    people = current_turn(state)
    text = reminder1_text(people) if state["nag_count"] == 0 else nag_text(people)

    await update.message.reply_text(
        f"[TEST — not counted, not affecting the real schedule]\n\n{text}",
        parse_mode=ParseMode.HTML,
        reply_markup=accept_keyboard(),
    )


# ---------------------------------------------------------------------------
# Rating completion (auto-advance)
# ---------------------------------------------------------------------------


async def complete_rating(bot, state: dict, reason: str) -> None:
    """Close the open rating poll and move to the next turn."""
    rating = state.get("rating")
    if not rating:
        return

    chat_id = rating["chat_id"]

    # Clear the rating first so the extra "poll closed" update is ignored
    # and this can never run twice for the same poll.
    state["rating"] = None
    save_state(state)

    try:
        await bot.stop_poll(chat_id=chat_id, message_id=rating["message_id"])
    except Exception:
        logger.warning("Could not stop poll in chat %s", chat_id, exc_info=True)

    finished = names_only(current_turn(state))
    advance_turn(state)

    msg = await bot.send_message(
        chat_id=chat_id,
        text=(
            f"{reason} Thanks {finished}! 🧹\n\n"
            f"Moving on to the next turn.\n\n"
            f"{build_whoseturn_text(state)}"
        ),
        reply_markup=accept_keyboard(),
    )

    # Remember this message so the next reminder can replace it.
    state["last_reminder_id"] = msg.message_id
    save_state(state)


async def poll_update_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Called by Telegram whenever the vote count of one of our polls changes."""
    poll = update.poll
    state = load_state()
    rating = state.get("rating")

    if not rating or poll.id != rating["poll_id"]:
        return

    logger.info(
        "Rating progress: %s/%s votes", poll.total_voter_count, rating["needed"]
    )

    if poll.total_voter_count >= rating["needed"]:
        await complete_rating(context.bot, state, "Everyone has rated!")


# ---------------------------------------------------------------------------
# Callback handlers
# ---------------------------------------------------------------------------


async def accept_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    clicker = query.from_user
    state = load_state()
    people = current_turn(state)

    if not is_person_in_turn(clicker.id, clicker.username, people):
        await query.answer("This isn't your turn to accept 😄", show_alert=True)
        return

    await query.answer()
    state["acknowledged"] = True
    save_state(state)

    await query.edit_message_text(
        text=f"✅ {clicker.first_name} accepted the cleaning duty.\n\nTap below when the house is sparkling.",
        reply_markup=finish_keyboard(),
    )


async def finish_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    clicker = query.from_user
    state = load_state()
    people = current_turn(state)

    if not is_person_in_turn(clicker.id, clicker.username, people):
        await query.answer("Only the person on duty can finish it!", show_alert=True)
        return

    if state.get("rating"):
        await query.answer("Already finished - waiting for everyone to rate.", show_alert=True)
        return

    await query.answer()
    await query.edit_message_text(text=f"🎉 {clicker.first_name} finished the cleaning! Great job.")

    group_id = state.get("group_chat_id") or query.message.chat_id

    # Everyone who is not on duty should rate.
    needed = max(1, TOTAL_HOUSEMATES - len(people))

    poll_msg = await context.bot.send_poll(
        chat_id=group_id,
        question=f"How did {names_only(people)} do on their cleaning duty this week?",
        options=POLL_OPTIONS,
        is_anonymous=True,
    )

    state["rating"] = {
        "poll_id": poll_msg.poll.id,
        "chat_id": group_id,
        "message_id": poll_msg.message_id,
        "needed": needed,
        "started_at": datetime.now(TIMEZONE).isoformat(),
    }
    save_state(state)

    await context.bot.send_message(
        chat_id=group_id,
        text=(
            f"Please rate above! As soon as {needed} people have voted, "
            f"I'll move on to the next turn."
        ),
    )


async def schedule_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    state = load_state()

    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=build_schedule_text(state),
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Reminder job
# ---------------------------------------------------------------------------


async def daily_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Runs at every time in REMINDER_TIMES. Sends a nag unless the turn has
    been accepted. While a rating poll is open it stays quiet (and closes the
    poll if it has timed out)."""
    state = load_state()
    chat_id = state.get("group_chat_id")

    if not chat_id:
        logger.warning("daily_reminder: no group_chat_id set - run /setgroup. Skipping.")
        return

    # A rating poll is open: no nagging, but close it if it has timed out.
    rating = state.get("rating")
    if rating:
        started = datetime.fromisoformat(rating["started_at"])
        if datetime.now(TIMEZONE) - started > timedelta(hours=RATING_TIMEOUT_HOURS):
            logger.info("daily_reminder: rating timed out - moving on.")
            try:
                await complete_rating(context.bot, state, "Voting time is over.")
            except Exception:
                logger.exception("daily_reminder: failed to complete rating")
        else:
            logger.info("daily_reminder: waiting for ratings - skipping.")
        return

    if state["acknowledged"]:
        logger.info("daily_reminder: turn already accepted - skipping nag.")
        return

    people = current_turn(state)
    text = reminder1_text(people) if state["nag_count"] == 0 else nag_text(people)

    # Delete the previous reminder so the chat doesn't fill up.
    last_id = state.get("last_reminder_id")
    if DELETE_OLD_REMINDERS and last_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=last_id)
        except Exception:
            pass  # already deleted, too old, or no permission - ignore

    try:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=accept_keyboard(),
        )
        state["nag_count"] = state.get("nag_count", 0) + 1
        state["last_reminder_id"] = msg.message_id
        save_state(state)
        logger.info("daily_reminder: sent nag #%s to chat %s", state["nag_count"], chat_id)
    except Exception:
        logger.exception("daily_reminder: failed to send message to chat %s", chat_id)


# ---------------------------------------------------------------------------
# Error handler
# ---------------------------------------------------------------------------


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Unhandled exception", exc_info=context.error)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    if not TOKEN:
        raise RuntimeError("HC_BOT_TOKEN environment variable is not set.")

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    if app.job_queue is None:
        raise RuntimeError(
            'JobQueue is missing. Install it with: pip install "python-telegram-bot[job-queue]"'
        )

    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("whoseturn", whoseturn_command))
    app.add_handler(CommandHandler("schedule", schedule_command))
    app.add_handler(CommandHandler("nextturn", nextturn_command))
    app.add_handler(CommandHandler("restart", restart_command))
    app.add_handler(CommandHandler("setgroup", setgroup_command))
    app.add_handler(CommandHandler("testreminder", testreminder_command))

    # Callbacks
    app.add_handler(CallbackQueryHandler(accept_button_handler, pattern=f"^{ACCEPT_CALLBACK}$"))
    app.add_handler(CallbackQueryHandler(finish_button_handler, pattern=f"^{FINISH_CALLBACK}$"))
    app.add_handler(CallbackQueryHandler(schedule_button_handler, pattern=f"^{SCHEDULE_CALLBACK}$"))

    # Poll updates (vote counts) - used to auto-advance once everyone rated.
    app.add_handler(PollHandler(poll_update_handler))

    app.add_error_handler(on_error)

    # Reminders: one job per time in REMINDER_TIMES, every day. Each job nags
    # until someone accepts, stays quiet while ratings are pending.
    for t in REMINDER_TIMES:
        app.job_queue.run_daily(
            daily_reminder,
            time=t,
            name=f"reminder_{t.hour:02d}{t.minute:02d}",
        )

    logger.info("House Cleaning Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
