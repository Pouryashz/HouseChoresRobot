"""
House Cleaning Bot
------------------

Rotates through a list of turns and reminds the group whose turn it is
to clean the house / take out the trash each weekend.

Reminder flow:
    1. Friday evening   - first reminder + accept button
    2. Saturday morning - second reminder if nobody accepted
    3. Saturday afternoon - final funny warning if nobody accepted

Commands:
    /start
    /whoseturn
    /schedule
    /nextturn
    /setgroup
    /review

Owner-only:
    /nextturn
    /setgroup
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

# Put your Telegram numeric user ID here. 
# 0 means everyone is treated as owner.
OWNER_ID = 0

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

# ---------------------------------------------------------------------------
# Time / reminders
# ---------------------------------------------------------------------------

TIMEZONE = ZoneInfo("Europe/Rome")

REMINDER_1 = {"weekday": 4, "hour": 19, "minute": 0}
REMINDER_2 = {"weekday": 5, "hour": 10, "minute": 0}
REMINDER_3 = {"weekday": 5, "hour": 16, "minute": 0}

# ---------------------------------------------------------------------------
# Callback identifiers
# ---------------------------------------------------------------------------

ACCEPT_CALLBACK = "accept_turn"
FINISH_CALLBACK = "finish_turn"
SCHEDULE_CALLBACK = "show_schedule"

# ---------------------------------------------------------------------------
# Funny messages
# ---------------------------------------------------------------------------

FUNNY_WARNINGS = [
    "The trash is filing a missing person's report on {names}.",
    "{names}, the broom has started a countdown. It is not a fan of being ignored.",
    "{names}, the dishes are staging a silent protest. Emphasis on silent, because you still haven't shown up.",
    "Breaking news: {names} has not been seen anywhere near a trash bag. Search parties are forming.",
    "{names}, the dust bunnies are unionizing. This is your last warning.",
]

DENIED_MESSAGES = [
    "I'm not working for you!",
    "Nice try. This button only listens to one person, and it's not you.",
    "Access denied. Please direct all complaints to management (Pourya).",
    "I only take orders from my one true boss. You are not him.",
    "Sorry, that command requires a level of authority you simply do not have.",
]

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

STATE_FILE = Path(__file__).parent / "state.json"

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read state.json: %s. Starting fresh.", exc)

    return {
        "turn_index": 0,
        "group_chat_id": None,
        "acknowledged": False, # Acts as "Accepted" status
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
    save_state(state)

# ---------------------------------------------------------------------------
# Mention helpers
# ---------------------------------------------------------------------------

def mention(person: dict) -> str:
    if person.get("username"):
        return f"@{person['username']}"
    if person.get("user_id"):
        return f'<a href="tg://user?id={person["user_id"]}">{person["name"]}</a>'
    return person["name"]

def names_only(people: list) -> str:
    return " & ".join(person["name"] for person in people)

def mentions_joined(people: list) -> str:
    return " & ".join(mention(person) for person in people)

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
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Accept Turn", callback_data=ACCEPT_CALLBACK)]
    ])

def finish_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧹 Mark as Finished", callback_data=FINISH_CALLBACK)]
    ])

def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Weekly Schedule", callback_data=SCHEDULE_CALLBACK)]
    ])

# ---------------------------------------------------------------------------
# Telegram command list
# ---------------------------------------------------------------------------

BOT_COMMANDS = [
    BotCommand("start", "Start the bot"),
    BotCommand("whoseturn", "See whose turn it is right now"),
    BotCommand("schedule", "See the full weekly rotation"),
    BotCommand("review", "Leave an anonymous comment (DM only)"),
    BotCommand("nextturn", "Advance to the next turn (owner only)"),
    BotCommand("setgroup", "Set this chat for reminders (owner only)"),
]

# ---------------------------------------------------------------------------
# Text builders
# ---------------------------------------------------------------------------

def build_whoseturn_text(state: dict) -> str:
    status = "accepted, cleaning in progress" if state["acknowledged"] else "not accepted yet"
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
    return f"Heads up {mentions_joined(people)} — you're on cleaning + trash duty this weekend! Tap below to accept the job."

def reminder2_text(people: list) -> str:
    return f"Morning reminder: {mentions_joined(people)}, it's still your turn to clean and take out the trash. Tap the button to accept!"

def reminder3_text(people: list) -> str:
    warning = random.choice(FUNNY_WARNINGS)
    return warning.format(names=mentions_joined(people))

# ---------------------------------------------------------------------------
# Command registration
# ---------------------------------------------------------------------------

async def register_commands_for_chat(bot, chat_id: int) -> list:
    chat_scope = BotCommandScopeChat(chat_id=chat_id)
    await bot.set_my_commands(BOT_COMMANDS, scope=chat_scope)
    return await bot.get_my_commands(scope=chat_scope)

async def post_init(application: Application) -> None:
    logger.info("Registering Telegram bot commands...")

    # The universal fallback to prevent empty menus
    await application.bot.set_my_commands(
        BOT_COMMANDS,
        scope=BotCommandScopeDefault(),
    )
    
    await application.bot.set_my_commands(
        BOT_COMMANDS,
        scope=BotCommandScopeAllPrivateChats(),
    )
    
    await application.bot.set_my_commands(
        BOT_COMMANDS,
        scope=BotCommandScopeAllGroupChats(),
    )

    logger.info("Global Telegram command registration complete.")

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hi! I track whose turn it is to clean the house and take out the trash each weekend.\n\n"
        "Type / to see my commands, or tap the button below for the schedule.",
        reply_markup=start_keyboard(),
    )

async def whoseturn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    text = build_whoseturn_text(state)

    if state["acknowledged"]:
        await update.message.reply_text(text, reply_markup=finish_keyboard())
    else:
        await update.message.reply_text(text, reply_markup=accept_keyboard())

async def schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    await update.message.reply_text(
        build_schedule_text(state),
        parse_mode=ParseMode.HTML,
    )

async def nextturn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_owner(update):
        return

    state = load_state()
    finished = names_only(current_turn(state))
    advance_turn(state)

    await update.message.reply_text(
        f"Manually advanced. Thanks {finished}!\n"
        f"Next up: {names_only(current_turn(state))}."
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
        "Got it — I'll send reminders to this chat from now on.\n\n"
        "The command menu has also been registered specifically for this group."
    )

async def review_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.chat.type != "private":
        await update.message.delete()
        await update.message.reply_text("Shh! Send reviews to me in a direct private chat to stay anonymous.")
        return

    review_text = " ".join(context.args)
    if not review_text:
        await update.message.reply_text("Usage: /review <your secret comment>")
        return

    state = load_state()
    group_id = state.get("group_chat_id")
    
    if not group_id:
        await update.message.reply_text("No group registered yet. Run /setgroup in the main chat first.")
        return

    await context.bot.send_message(
        chat_id=group_id,
        text=f"🕵️ <b>Anonymous House Review:</b>\n\n<i>\"{review_text}\"</i>",
        parse_mode=ParseMode.HTML
    )
    
    await update.message.reply_text("Your secret review has been sent to the group!")

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

    await query.answer()
    await query.edit_message_text(text=f"🎉 {clicker.first_name} finished the cleaning! Great job.")

    advance_turn(state)
    next_turn_text = build_whoseturn_text(state)
    group_id = state.get("group_chat_id") or query.message.chat_id
    
    await context.bot.send_message(
        chat_id=group_id,
        text=f"Moving on to next week!\n\n{next_turn_text}",
        reply_markup=accept_keyboard()
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
# Scheduled reminders
# ---------------------------------------------------------------------------

async def _send(context: ContextTypes.DEFAULT_TYPE, text: str, with_button: bool):
    state = load_state()
    chat_id = state.get("group_chat_id")

    if not chat_id:
        logger.warning("No group_chat_id set yet — run /setgroup in the group.")
        return

    if state["acknowledged"]:
        logger.info("Reminder skipped: already acknowledged.")
        return

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=accept_keyboard() if with_button else None,
    )

async def reminder_1(context: ContextTypes.DEFAULT_TYPE):
    await _send(context, reminder1_text(current_turn(load_state())), with_button=True)

async def reminder_2(context: ContextTypes.DEFAULT_TYPE):
    await _send(context, reminder2_text(current_turn(load_state())), with_button=True)

async def reminder_3(context: ContextTypes.DEFAULT_TYPE):
    await _send(context, reminder3_text(current_turn(load_state())), with_button=True)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not TOKEN:
        raise RuntimeError("HC_BOT_TOKEN environment variable is not set.")

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("whoseturn", whoseturn_command))
    app.add_handler(CommandHandler("schedule", schedule_command))
    app.add_handler(CommandHandler("review", review_command))
    app.add_handler(CommandHandler("nextturn", nextturn_command))
    app.add_handler(CommandHandler("setgroup", setgroup_command))

    # Callbacks
    app.add_handler(CallbackQueryHandler(accept_button_handler, pattern=f"^{ACCEPT_CALLBACK}$"))
    app.add_handler(CallbackQueryHandler(finish_button_handler, pattern=f"^{FINISH_CALLBACK}$"))
    app.add_handler(CallbackQueryHandler(schedule_button_handler, pattern=f"^{SCHEDULE_CALLBACK}$"))

    # Scheduled jobs
    for cfg, job in (
        (REMINDER_1, reminder_1),
        (REMINDER_2, reminder_2),
        (REMINDER_3, reminder_3),
    ):
        app.job_queue.run_daily(
            job,
            time=time(hour=cfg["hour"], minute=cfg["minute"], tzinfo=TIMEZONE),
            days=(cfg["weekday"],),
        )

    logger.info("House Cleaning Bot starting...")
    app.run_polling()

if __name__ == "__main__":
    main()"""
House Cleaning Bot
------------------

Rotates through a list of turns and reminds the group whose turn it is
to clean the house / take out the trash each weekend.

Reminder flow:
    1. Friday evening   - first reminder + confirmation button
    2. Saturday morning - second reminder if nobody confirmed
    3. Saturday afternoon - final funny warning if nobody confirmed

Commands:
    /start
    /whoseturn
    /schedule
    /nextturn
    /setgroup

Owner-only:
    /nextturn
    /setgroup

The bot registers commands with Telegram using:
    - all private chats
    - all group chats
    - the exact group where /setgroup is executed

The exact-chat scope is important because Telegram uses the most
specific command scope available for a chat.
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
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllGroupChats,
    BotCommandScopeChat,
)

from telegram.constants import ParseMode

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
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

# Put your Telegram numeric user ID here.
#
# For testing:
#     OWNER_ID = 0
#
# WARNING:
# When OWNER_ID = 0, everybody is treated as the owner.
#
# For real use, replace 0 with your Telegram numeric ID.
OWNER_ID = 0


# ---------------------------------------------------------------------------
# Cleaning rotation
# ---------------------------------------------------------------------------

TURNS = [
    [
        {
            "name": "Danial",
            "username": "D4NYAL_BK",
        },
        {
            "name": "Pourya",
            "username": "Pouryashahbazzadeh",
        },
    ],

    [
        {
            "name": "Alireza",
            "username": "Deartahmasebi",
        },
    ],

    [
        {
            "name": "Soroush",
            "username": "try38474727",
        },
        {
            "name": "Aydin",
            "username": "Aydin_Pouladvand",
        },
    ],

    [
        {
            "name": "Daniele",
        },
    ],
]


# ---------------------------------------------------------------------------
# Time / reminders
# ---------------------------------------------------------------------------

TIMEZONE = ZoneInfo("Europe/Rome")

# Python weekday:
# Monday    = 0
# Tuesday   = 1
# Wednesday = 2
# Thursday  = 3
# Friday    = 4
# Saturday  = 5
# Sunday    = 6

REMINDER_1 = {
    "weekday": 4,
    "hour": 19,
    "minute": 0,
}

REMINDER_2 = {
    "weekday": 5,
    "hour": 10,
    "minute": 0,
}

REMINDER_3 = {
    "weekday": 5,
    "hour": 16,
    "minute": 0,
}


# ---------------------------------------------------------------------------
# Callback identifiers
# ---------------------------------------------------------------------------

ACK_CALLBACK = "ack_turn"
SCHEDULE_CALLBACK = "show_schedule"


# ---------------------------------------------------------------------------
# Funny messages
# ---------------------------------------------------------------------------

FUNNY_WARNINGS = [
    "The trash is filing a missing person's report on {names}.",

    "{names}, the broom has started a countdown. "
    "It is not a fan of being ignored.",

    "{names}, the dishes are staging a silent protest. "
    "Emphasis on silent, because you still haven't shown up.",

    "Breaking news: {names} has not been seen anywhere near a trash bag. "
    "Search parties are forming.",

    "{names}, the dust bunnies are unionizing. "
    "This is your last warning.",
]


DENIED_MESSAGES = [
    "I'm not working for you!",

    "Nice try. This button only listens to one person, and it's not you.",

    "Access denied. Please direct all complaints to management (Pourya).",

    "I only take orders from my one true boss. You are not him.",

    "Sorry, that command requires a level of authority you simply do not have.",
]


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

STATE_FILE = Path(__file__).parent / "state.json"


def load_state() -> dict:
    """
    Load persistent state from state.json.
    """

    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "Could not read state.json: %s. Starting with fresh state.",
                exc,
            )

    return {
        "turn_index": 0,
        "group_chat_id": None,
        "acknowledged": False,
    }


def save_state(state: dict) -> None:
    """
    Save persistent state to state.json.
    """

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Turn helpers
# ---------------------------------------------------------------------------

def current_turn(state: dict) -> list:
    """
    Return the current cleaning turn.
    """

    return TURNS[state["turn_index"] % len(TURNS)]


def advance_turn(state: dict) -> None:
    """
    Move to the next turn and reset confirmation.
    """

    state["turn_index"] = (
        state["turn_index"] + 1
    ) % len(TURNS)

    state["acknowledged"] = False

    save_state(state)


# ---------------------------------------------------------------------------
# Mention helpers
# ---------------------------------------------------------------------------

def mention(person: dict) -> str:
    """
    Create an HTML-safe Telegram mention.

    Priority:
        1. @username
        2. clickable tg://user?id=...
        3. plain name
    """

    if person.get("username"):
        return f"@{person['username']}"

    if person.get("user_id"):
        return (
            f'<a href="tg://user?id={person["user_id"]}">'
            f'{person["name"]}'
            f"</a>"
        )

    return person["name"]


def names_only(people: list) -> str:
    """
    Return names without Telegram mentions.
    """

    return " & ".join(
        person["name"]
        for person in people
    )


def mentions_joined(people: list) -> str:
    """
    Return Telegram mentions joined together.
    """

    return " & ".join(
        mention(person)
        for person in people
    )


def is_person_in_turn(
    user_id: int,
    username: str,
    people: list,
) -> bool:
    """
    Check whether a Telegram user belongs to the current turn.
    """

    username = (username or "").lower()

    for person in people:

        if person.get("user_id"):
            if person["user_id"] == user_id:
                return True

        if person.get("username"):
            if person["username"].lower() == username:
                return True

    return False


# ---------------------------------------------------------------------------
# Owner
# ---------------------------------------------------------------------------

def is_owner(user_id: int) -> bool:
    """
    Check whether a Telegram user is the owner.

    OWNER_ID = 0 means everyone is treated as owner.
    """

    if OWNER_ID == 0:
        return True

    return user_id == OWNER_ID


async def require_owner(update: Update) -> bool:
    """
    Check owner permission.

    Returns:
        True  -> allowed
        False -> denied
    """

    user = update.effective_user

    if user is None:
        return False

    if is_owner(user.id):
        return True

    await update.message.reply_text(
        denied_text()
    )

    return False


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

def ack_keyboard() -> InlineKeyboardMarkup:
    """
    Confirmation button.
    """

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "OK - I'll do it",
                    callback_data=ACK_CALLBACK,
                )
            ]
        ]
    )


def start_keyboard() -> InlineKeyboardMarkup:
    """
    Schedule button shown by /start.
    """

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📅 Weekly Schedule",
                    callback_data=SCHEDULE_CALLBACK,
                )
            ]
        ]
    )


# ---------------------------------------------------------------------------
# Telegram command list
# ---------------------------------------------------------------------------

BOT_COMMANDS = [
    BotCommand(
        "start",
        "Start the bot",
    ),

    BotCommand(
        "whoseturn",
        "See whose turn it is right now",
    ),

    BotCommand(
        "schedule",
        "See the full weekly rotation",
    ),

    BotCommand(
        "nextturn",
        "Advance to the next turn (owner only)",
    ),

    BotCommand(
        "setgroup",
        "Set this chat for reminders (owner only)",
    ),
]


# ---------------------------------------------------------------------------
# Text builders
# ---------------------------------------------------------------------------

def build_whoseturn_text(state: dict) -> str:
    """
    Build the /whoseturn response.
    """

    status = (
        "already confirmed"
        if state["acknowledged"]
        else "not confirmed yet"
    )

    return (
        f"This weekend it's "
        f"{names_only(current_turn(state))}'s turn "
        f"({status})."
    )


def build_schedule_text(state: dict) -> str:
    """
    Build the weekly schedule.
    """

    lines = [
        "📅 <b>Weekly Cleaning Schedule</b>",
        "",
    ]

    current_idx = (
        state["turn_index"] % len(TURNS)
    )

    for i, people in enumerate(TURNS):

        marker = (
            "👉"
            if i == current_idx
            else "  "
        )

        lines.append(
            f"{marker} Week {i + 1}: "
            f"{names_only(people)}"
        )

    lines.append("")
    lines.append(
        "(Rotation repeats after the last week.)"
    )

    return "\n".join(lines)


def reminder1_text(people: list) -> str:
    """
    Friday reminder.
    """

    return (
        f"Heads up {mentions_joined(people)} — "
        f"you're on cleaning + trash duty this weekend! "
        f"Tap below once you're planning to handle it."
    )


def reminder2_text(people: list) -> str:
    """
    Saturday morning reminder.
    """

    return (
        f"Morning reminder: {mentions_joined(people)}, "
        f"it's still your turn to clean and take out "
        f"the trash this weekend. "
        f"Tap the button once it's sorted!"
    )


def reminder3_text(
    people: list,
    warning_index: int = None,
) -> str:
    """
    Saturday afternoon final warning.
    """

    if warning_index is not None:
        warning = FUNNY_WARNINGS[
            warning_index
        ]
    else:
        warning = random.choice(
            FUNNY_WARNINGS
        )

    return warning.format(
        names=mentions_joined(people)
    )


def denied_text(index: int = None) -> str:
    """
    Funny owner-only denial.
    """

    if index is not None:
        return DENIED_MESSAGES[index]

    return random.choice(
        DENIED_MESSAGES
    )


# ---------------------------------------------------------------------------
# Command registration
# ---------------------------------------------------------------------------

async def register_commands_for_chat(
    bot,
    chat_id: int,
) -> list:
    """
    Register BOT_COMMANDS specifically for one chat.

    This is the important part for the group command menu.

    Telegram's command resolution gives a specific-chat scope
    precedence over the broader all-group scope.
    """

    chat_scope = BotCommandScopeChat(
        chat_id=chat_id
    )

    await bot.set_my_commands(
        BOT_COMMANDS,
        scope=chat_scope,
    )

    # Read them back immediately.
    registered = await bot.get_my_commands(
        scope=chat_scope
    )

    logger.info(
        "Commands registered for chat %s: %s",
        chat_id,
        registered,
    )

    return registered


async def post_init(
    application: Application,
) -> None:
    """
    Register command lists when the bot starts.
    """

    logger.info(
        "Registering Telegram bot commands..."
    )

    # ---------------------------------------------------------------
    # Private chats
    # ---------------------------------------------------------------

    await application.bot.set_my_commands(
        BOT_COMMANDS,
        scope=BotCommandScopeAllPrivateChats(),
    )

    # ---------------------------------------------------------------
    # All groups
    # ---------------------------------------------------------------

    await application.bot.set_my_commands(
        BOT_COMMANDS,
        scope=BotCommandScopeAllGroupChats(),
    )

    # ---------------------------------------------------------------
    # Verify global scopes
    # ---------------------------------------------------------------

    private_commands = (
        await application.bot.get_my_commands(
            scope=BotCommandScopeAllPrivateChats()
        )
    )

    group_commands = (
        await application.bot.get_my_commands(
            scope=BotCommandScopeAllGroupChats()
        )
    )

    logger.info(
        "REGISTERED PRIVATE COMMANDS: %s",
        private_commands,
    )

    logger.info(
        "REGISTERED GROUP COMMANDS: %s",
        group_commands,
    )

    logger.info(
        "Global Telegram command registration complete."
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    /start
    """

    await update.message.reply_text(
        "Hi! I track whose turn it is to clean the house "
        "and take out the trash each weekend, and I nag "
        "until someone on that turn confirms.\n\n"
        "Type / to see my commands, or tap the button "
        "below for the schedule.",
        reply_markup=start_keyboard(),
    )


async def whoseturn_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    /whoseturn
    """

    state = load_state()

    text = build_whoseturn_text(
        state
    )

    if state["acknowledged"]:

        await update.message.reply_text(
            text
        )

    else:

        await update.message.reply_text(
            text,
            reply_markup=ack_keyboard(),
        )


async def schedule_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    /schedule
    """

    state = load_state()

    await update.message.reply_text(
        build_schedule_text(state),
        parse_mode=ParseMode.HTML,
    )


async def schedule_button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Handle the Weekly Schedule button.
    """

    query = update.callback_query

    await query.answer()

    state = load_state()

    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=build_schedule_text(state),
        parse_mode=ParseMode.HTML,
    )


async def nextturn_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    /nextturn

    Owner only.
    """

    if not await require_owner(update):
        return

    state = load_state()

    finished = names_only(
        current_turn(state)
    )

    advance_turn(state)

    await update.message.reply_text(
        f"Thanks {finished}! "
        f"Next up: "
        f"{names_only(current_turn(state))}."
    )


async def setgroup_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    /setgroup

    Owner only.

    Saves the group ID AND registers the command list
    specifically for this exact Telegram chat.
    """

    if not await require_owner(update):
        return

    chat = update.effective_chat

    if chat is None:
        await update.message.reply_text(
            "I couldn't determine which chat this is."
        )
        return

    chat_id = chat.id

    # ---------------------------------------------------------------
    # Save the group ID for reminders
    # ---------------------------------------------------------------

    state = load_state()

    state["group_chat_id"] = chat_id

    save_state(state)

    # ---------------------------------------------------------------
    # IMPORTANT:
    #
    # Register commands specifically for this exact group.
    # ---------------------------------------------------------------

    try:

        registered_commands = (
            await register_commands_for_chat(
                context.bot,
                chat_id,
            )
        )

        logger.info(
            "Exact chat command registration succeeded."
        )

        logger.info(
            "Chat ID: %s",
            chat_id,
        )

        logger.info(
            "Commands: %s",
            registered_commands,
        )

    except Exception:

        logger.exception(
            "Failed to register commands for chat %s",
            chat_id,
        )

        await update.message.reply_text(
            "I saved this group, but I couldn't "
            "register the Telegram command menu. "
            "Check the bot logs."
        )

        return

    # ---------------------------------------------------------------
    # Tell the user exactly what happened
    # ---------------------------------------------------------------

    await update.message.reply_text(
        "Got it — I'll send reminders to this chat "
        "from now on.\n\n"
        "The command menu has also been registered "
        "for this group. Type / to see the commands."
    )


# ---------------------------------------------------------------------------
# Confirmation button
# ---------------------------------------------------------------------------

async def ack_button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Handle the "OK - I'll do it" button.
    """

    query = update.callback_query

    clicker = query.from_user

    state = load_state()

    people = current_turn(state)

    # ---------------------------------------------------------------
    # Only people on the current turn can confirm.
    # ---------------------------------------------------------------

    if not is_person_in_turn(
        clicker.id,
        clicker.username,
        people,
    ):

        await query.answer(
            "This isn't your turn to confirm 😄",
            show_alert=True,
        )

        return

    # ---------------------------------------------------------------
    # Confirm
    # ---------------------------------------------------------------

    await query.answer()

    state["acknowledged"] = True

    save_state(state)

    await query.edit_message_text(
        f"Confirmed by {clicker.first_name} — "
        f"thanks! No more nagging this week.",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Scheduled reminders
# ---------------------------------------------------------------------------

async def _send(
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    with_button: bool,
):
    """
    Send a reminder to the configured group.
    """

    state = load_state()

    chat_id = state.get(
        "group_chat_id"
    )

    if not chat_id:

        logger.warning(
            "No group_chat_id set yet — "
            "run /setgroup in the group."
        )

        return

    if state["acknowledged"]:

        logger.info(
            "Reminder skipped: already acknowledged."
        )

        return

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=(
            ack_keyboard()
            if with_button
            else None
        ),
    )


async def reminder_1(
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Friday evening.
    """

    state = load_state()

    await _send(
        context,
        reminder1_text(
            current_turn(state)
        ),
        with_button=True,
    )


async def reminder_2(
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Saturday morning.
    """

    state = load_state()

    await _send(
        context,
        reminder2_text(
            current_turn(state)
        ),
        with_button=True,
    )


async def reminder_3(
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Saturday afternoon.
    """

    state = load_state()

    await _send(
        context,
        reminder3_text(
            current_turn(state)
        ),
        with_button=True,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():

    # ---------------------------------------------------------------
    # Token check
    # ---------------------------------------------------------------

    if not TOKEN:

        raise RuntimeError(
            "HC_BOT_TOKEN environment variable is not set."
        )

    # ---------------------------------------------------------------
    # Build application
    # ---------------------------------------------------------------

    app = (
        Application
        .builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    # ---------------------------------------------------------------
    # Command handlers
    # ---------------------------------------------------------------

    app.add_handler(
        CommandHandler(
            "start",
            start_command,
        )
    )

    app.add_handler(
        CommandHandler(
            "whoseturn",
            whoseturn_command,
        )
    )

    app.add_handler(
        CommandHandler(
            "schedule",
            schedule_command,
        )
    )

    app.add_handler(
        CommandHandler(
            "nextturn",
            nextturn_command,
        )
    )

    app.add_handler(
        CommandHandler(
            "setgroup",
            setgroup_command,
        )
    )

    # ---------------------------------------------------------------
    # Callback handlers
    # ---------------------------------------------------------------

    app.add_handler(
        CallbackQueryHandler(
            ack_button_handler,
            pattern=f"^{ACK_CALLBACK}$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            schedule_button_handler,
            pattern=f"^{SCHEDULE_CALLBACK}$",
        )
    )

    # ---------------------------------------------------------------
    # Scheduled jobs
    # ---------------------------------------------------------------

    for cfg, job in (
        (REMINDER_1, reminder_1),
        (REMINDER_2, reminder_2),
        (REMINDER_3, reminder_3),
    ):

        app.job_queue.run_daily(
            job,
            time=time(
                hour=cfg["hour"],
                minute=cfg["minute"],
                tzinfo=TIMEZONE,
            ),
            days=(cfg["weekday"],),
        )

    # ---------------------------------------------------------------
    # Start
    # ---------------------------------------------------------------

    logger.info(
        "House Cleaning Bot starting..."
    )

    app.run_polling()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
