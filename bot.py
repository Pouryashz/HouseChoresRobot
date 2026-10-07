"""
House Cleaning Bot
------------------

Cleaning rotation:
    - Each turn lasts from Sunday to Saturday.
    - A new turn starts automatically every Sunday.
    - The person(s) whose turn it is are reminded 3 times per day.
    - Reminders stop immediately when someone on that turn accepts.
    - Finishing the cleaning does NOT change the turn.
    - The turn changes automatically on Sunday.

Requires:
    pip install "python-telegram-bot[job-queue]"

Environment:
    HC_BOT_TOKEN=your_bot_token

Optional:
    HC_GROUP_CHAT_ID=-100xxxxxxxxxx

Timezone:
    Europe/Rome
"""

import json
import logging
import os
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllGroupChats,
    BotCommandScopeDefault,
    BotCommandScopeChat,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)


# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================================
# CONFIG
# ============================================================================

TOKEN = os.environ.get("HC_BOT_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "HC_BOT_TOKEN environment variable is not set."
    )


# Your Telegram numeric ID.
OWNER_ID = 1738272640


# ============================================================================
# CLEANING ROTATION
# ============================================================================

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
            # IMPORTANT:
            # Add Daniele's username or Telegram user_id here.
            #
            # Example:
            # "username": "daniele123"
            #
            # or:
            # "user_id": 123456789
        },
    ],
]


# ============================================================================
# TIMEZONE / REMINDER TIMES
# ============================================================================

TIMEZONE = ZoneInfo("Europe/Rome")


# Three reminders every day.
#
# Change these times if you want.
REMINDER_TIMES = [
    time(hour=9, minute=0, tzinfo=TIMEZONE),
    time(hour=15, minute=0, tzinfo=TIMEZONE),
    time(hour=21, minute=0, tzinfo=TIMEZONE),
]


# ============================================================================
# GROUP CHAT
# ============================================================================

# You can optionally set:
#
# HC_GROUP_CHAT_ID=-1001234567890
#
# This avoids having to use /setgroup after restarting the bot.
ENV_GROUP_CHAT_ID = os.environ.get("HC_GROUP_CHAT_ID")

if ENV_GROUP_CHAT_ID:
    try:
        ENV_GROUP_CHAT_ID = int(ENV_GROUP_CHAT_ID)
    except ValueError:
        raise RuntimeError(
            "HC_GROUP_CHAT_ID must be a valid Telegram numeric chat ID."
        )


# ============================================================================
# CALLBACK IDENTIFIERS
# ============================================================================

ACCEPT_CALLBACK = "accept_turn"
FINISH_CALLBACK = "finish_turn"
SCHEDULE_CALLBACK = "show_schedule"


# ============================================================================
# FUNNY MESSAGES
# ============================================================================

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

    "{names}, your cleaning responsibilities would like to remind you "
    "that they still exist.",

    "🚨 CLEANING ALERT 🚨 {names} are currently wanted by the broom.",
]


DENIED_MESSAGES = [
    "I'm not working for you!",

    "Nice try. This button only listens to someone whose turn it is.",

    "Access denied. Please wait for your week.",

    "That's not your cleaning turn 😄",

    "You cannot escape the rotation by clicking someone else's button.",
]


POLL_OPTIONS = [
    "Poor 🤢",
    "Mehhh! 😒",
    "Acceptable 😐",
    "Good 🙂",
    "Perfect ✨",
]


# ============================================================================
# STATE
# ============================================================================

STATE_FILE = Path(__file__).parent / "state.json"


def default_state() -> dict:
    """
    Default persistent state.

    turn_index:
        Current person/group in the rotation.

    turn_start_date:
        Date on which the current turn started.

    group_chat_id:
        Telegram group where reminders are sent.

    acknowledged:
        True after someone on the current turn accepts.

    nag_count:
        Number of reminders sent for the current turn.
    """

    return {
        "turn_index": 0,
        "turn_start_date": None,
        "group_chat_id": ENV_GROUP_CHAT_ID,
        "acknowledged": False,
        "nag_count": 0,
    }


def load_state() -> dict:
    """Load state safely from disk."""

    if not STATE_FILE.exists():
        logger.info("state.json does not exist. Creating fresh state.")
        return default_state()

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)

    except (json.JSONDecodeError, OSError) as exc:
        logger.exception(
            "Could not read state.json. Starting fresh: %s",
            exc,
        )
        return default_state()

    defaults = default_state()

    for key, value in defaults.items():
        state.setdefault(key, value)

    # Environment variable takes precedence if supplied.
    if ENV_GROUP_CHAT_ID is not None:
        state["group_chat_id"] = ENV_GROUP_CHAT_ID

    return state


def save_state(state: dict) -> None:
    """Save state safely."""

    temp_file = STATE_FILE.with_suffix(".tmp")

    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(
                state,
                f,
                indent=2,
                ensure_ascii=False,
            )

        temp_file.replace(STATE_FILE)

    except OSError as exc:
        logger.exception(
            "Could not save state.json: %s",
            exc,
        )


# ============================================================================
# DATE / SUNDAY ROTATION
# ============================================================================

def today_local() -> date:
    """Return today's date in Europe/Rome."""

    return datetime.now(TIMEZONE).date()


def days_since_sunday(d: date) -> int:
    """
    Python weekday:
        Monday = 0
        ...
        Saturday = 5
        Sunday = 6

    Convert this into:
        Sunday = 0
        Monday = 1
        ...
        Saturday = 6
    """

    return (d.weekday() + 1) % 7


def current_sunday(d: date | None = None) -> date:
    """Return the Sunday starting the current cleaning week."""

    if d is None:
        d = today_local()

    return d - timedelta(days=days_since_sunday(d))


def ensure_current_week(state: dict) -> bool:
    """
    Make sure the stored turn corresponds to the current Sunday.

    Returns:
        True if the turn changed.
        False otherwise.

    This means the rotation is controlled by the calendar,
    not by the Finish button.
    """

    today = today_local()
    this_sunday = current_sunday(today)

    stored_date = state.get("turn_start_date")

    # First time the bot runs.
    if not stored_date:
        state["turn_start_date"] = this_sunday.isoformat()
        state["acknowledged"] = False
        state["nag_count"] = 0

        save_state(state)

        logger.info(
            "Initialized cleaning week: %s",
            this_sunday,
        )

        return False

    try:
        stored_sunday = date.fromisoformat(stored_date)
    except ValueError:
        logger.warning(
            "Invalid turn_start_date=%r. Resetting to %s.",
            stored_date,
            this_sunday,
        )

        state["turn_start_date"] = this_sunday.isoformat()
        state["acknowledged"] = False
        state["nag_count"] = 0

        save_state(state)

        return False

    # Already on the correct week.
    if stored_sunday == this_sunday:
        return False

    # Calculate how many complete Sunday boundaries passed.
    weeks_passed = (this_sunday - stored_sunday).days // 7

    old_index = state["turn_index"]

    state["turn_index"] = (
        state["turn_index"] + weeks_passed
    ) % len(TURNS)

    state["turn_start_date"] = this_sunday.isoformat()

    # New week = reminders start again.
    state["acknowledged"] = False
    state["nag_count"] = 0

    save_state(state)

    logger.info(
        "NEW CLEANING WEEK: %s -> %s | weeks_passed=%s | turn %s -> %s",
        stored_sunday,
        this_sunday,
        weeks_passed,
        old_index,
        state["turn_index"],
    )

    return True


# ============================================================================
# TURN HELPERS
# ============================================================================

def current_turn(state: dict) -> list:
    """Return the people whose turn it currently is."""

    return TURNS[
        state["turn_index"] % len(TURNS)
    ]


def names_only(people: list) -> str:
    """Return names without Telegram mentions."""

    return " & ".join(
        person["name"]
        for person in people
    )


def mention(person: dict) -> str:
    """
    Create a Telegram mention.

    Username:
        @username

    User ID:
        clickable Telegram mention

    No identifier:
        plain name
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


def mentions_joined(people: list) -> str:
    """Return Telegram mentions for all people in the turn."""

    return " & ".join(
        mention(person)
        for person in people
    )


def is_person_in_turn(
    user_id: int,
    username: str | None,
    people: list,
) -> bool:
    """Check whether a Telegram user belongs to the current turn."""

    username = (username or "").lower().lstrip("@")

    for person in people:

        if (
            person.get("user_id")
            and person["user_id"] == user_id
        ):
            return True

        configured_username = (
            person.get("username") or ""
        ).lower().lstrip("@")

        if (
            configured_username
            and configured_username == username
        ):
            return True

    return False


# ============================================================================
# OWNER
# ============================================================================

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


async def require_owner(update: Update) -> bool:

    user = update.effective_user

    if user is None:
        return False

    if is_owner(user.id):
        return True

    if update.message:
        await update.message.reply_text(
            random.choice(DENIED_MESSAGES)
        )

    return False


# ============================================================================
# KEYBOARDS
# ============================================================================

def accept_keyboard() -> InlineKeyboardMarkup:

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Accept Turn",
                    callback_data=ACCEPT_CALLBACK,
                )
            ]
        ]
    )


def finish_keyboard() -> InlineKeyboardMarkup:

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🧹 Mark as Finished",
                    callback_data=FINISH_CALLBACK,
                )
            ]
        ]
    )


def start_keyboard() -> InlineKeyboardMarkup:

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


# ============================================================================
# TEXT
# ============================================================================

def build_whoseturn_text(state: dict) -> str:

    ensure_current_week(state)

    status = (
        "accepted, cleaning in progress"
        if state["acknowledged"]
        else "not accepted yet"
    )

    week_start = state.get("turn_start_date", "?")

    return (
        f"This week's turn is <b>{names_only(current_turn(state))}</b>.\n"
        f"Week started: <b>{week_start}</b>\n"
        f"Status: <b>{status}</b>."
    )


def build_schedule_text(state: dict) -> str:

    ensure_current_week(state)

    current_idx = (
        state["turn_index"] % len(TURNS)
    )

    lines = [
        "📅 <b>Weekly Cleaning Schedule</b>\n"
    ]

    for i, people in enumerate(TURNS):

        marker = (
            "👉"
            if i == current_idx
            else "🔹"
        )

        lines.append(
            f"{marker} <b>Week {i + 1}:</b> "
            f"{names_only(people)}"
        )

    lines.append(
        "\n<i>The rotation changes automatically every Sunday.</i>"
    )

    return "\n".join(lines)


def reminder_text(
    state: dict,
) -> str:

    people = current_turn(state)

    if state["nag_count"] == 0:

        return (
            f"🚨 <b>Cleaning duty!</b>\n\n"
            f"Heads up {mentions_joined(people)} — "
            f"you're on cleaning + trash duty this week!\n\n"
            f"Tap the button below to accept the job."
        )

    warning = random.choice(FUNNY_WARNINGS)

    return (
        f"🧹 <b>Cleaning reminder #{state['nag_count'] + 1}</b>\n\n"
        f"{warning}\n\n"
        f"Please accept your turn 👇"
    )


# ============================================================================
# TELEGRAM COMMAND REGISTRATION
# ============================================================================

BOT_COMMANDS = [
    BotCommand("start", "Start the bot"),
    BotCommand("whoseturn", "See whose turn it is"),
    BotCommand("schedule", "See the weekly rotation"),
    BotCommand("nextturn", "Manually advance turn (owner only)"),
    BotCommand("restart", "Restart rotation (owner only)"),
    BotCommand("setgroup", "Set this group for reminders"),
    BotCommand(
        "testreminder",
        "Send a reminder immediately",
    ),
]


async def register_commands_for_chat(
    bot,
    chat_id: int,
) -> None:

    chat_scope = BotCommandScopeChat(
        chat_id=chat_id
    )

    await bot.set_my_commands(
        BOT_COMMANDS,
        scope=chat_scope,
    )


async def post_init(
    application: Application,
) -> None:

    logger.info("=" * 70)
    logger.info("HOUSE CLEANING BOT STARTING")
    logger.info("=" * 70)

    # Register commands globally.
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

    state = load_state()

    ensure_current_week(state)

    logger.info(
        "Startup state:"
    )

    logger.info(
        "  turn_index      = %s",
        state["turn_index"],
    )

    logger.info(
        "  current_turn    = %s",
        names_only(current_turn(state)),
    )

    logger.info(
        "  turn_start_date = %s",
        state.get("turn_start_date"),
    )

    logger.info(
        "  group_chat_id   = %s",
        state.get("group_chat_id"),
    )

    logger.info(
        "  acknowledged    = %s",
        state["acknowledged"],
    )

    logger.info(
        "  nag_count       = %s",
        state["nag_count"],
    )

    logger.info(
        "  timezone        = %s",
        TIMEZONE,
    )

    logger.info(
        "  reminder times  = %s",
        REMINDER_TIMES,
    )

    # Very important diagnostic.
    if application.job_queue is None:
        raise RuntimeError(
            "\n\n"
            "JobQueue is NOT available!\n"
            "Install the required dependency with:\n\n"
            'pip install "python-telegram-bot[job-queue]"\n'
        )

    logger.info(
        "JobQueue is available."
    )

    if not state.get("group_chat_id"):
        logger.warning(
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        )

        logger.warning(
            "NO GROUP CHAT ID IS CONFIGURED!"
        )

        logger.warning(
            "Run /setgroup in the target Telegram group."
        )

        logger.warning(
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        )

    else:
        logger.info(
            "Reminders will be sent to chat %s",
            state["group_chat_id"],
        )


# ============================================================================
# COMMANDS
# ============================================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    # If /start is used in a group by the owner,
    # automatically remember that group.
    if (
        update.effective_chat
        and update.effective_chat.type in (
            "group",
            "supergroup",
        )
        and update.effective_user
        and is_owner(update.effective_user.id)
    ):

        state = load_state()

        state["group_chat_id"] = (
            update.effective_chat.id
        )

        save_state(state)

        logger.info(
            "Group automatically registered from /start: %s",
            update.effective_chat.id,
        )

    await update.message.reply_text(
        "Hi! 🧹\n\n"
        "I manage the house cleaning rotation.\n\n"
        "• Each turn lasts Sunday → Saturday\n"
        "• I remind the current person 3 times every day\n"
        "• Reminders stop when they accept\n"
        "• The next turn starts automatically on Sunday\n\n"
        "Commands:\n"
        "/whoseturn - See whose turn it is\n"
        "/schedule - See the weekly schedule\n\n"
        "Admin:\n"
        "/setgroup - Set this chat for reminders\n"
        "/testreminder - Send a reminder immediately\n"
        "/nextturn - Manually advance\n"
        "/restart - Restart rotation",
        reply_markup=start_keyboard(),
    )


async def whoseturn_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    state = load_state()

    ensure_current_week(state)

    text = build_whoseturn_text(state)

    if state["acknowledged"]:

        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=finish_keyboard(),
        )

    else:

        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=accept_keyboard(),
        )


async def schedule_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    state = load_state()

    ensure_current_week(state)

    await update.message.reply_text(
        build_schedule_text(state),
        parse_mode=ParseMode.HTML,
    )


async def setgroup_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_owner(update):
        return

    chat = update.effective_chat

    if chat is None:
        await update.message.reply_text(
            "I couldn't determine which chat this is."
        )
        return

    # Only allow group chats.
    if chat.type not in ("group", "supergroup"):

        await update.message.reply_text(
            "Run /setgroup inside the Telegram group "
            "where you want the reminders."
        )

        return

    state = load_state()

    state["group_chat_id"] = chat.id

    save_state(state)

    try:

        await register_commands_for_chat(
            context.bot,
            chat.id,
        )

    except Exception as exc:

        logger.exception(
            "Failed to register group commands: %s",
            exc,
        )

        await update.message.reply_text(
            "The group was saved, but command registration failed. "
            "Check the bot logs."
        )

        return

    logger.info(
        "GROUP CONFIGURED: %s (%s)",
        chat.id,
        chat.title,
    )

    await update.message.reply_text(
        "✅ Group configured!\n\n"
        f"Chat ID: <code>{chat.id}</code>\n\n"
        "I will now send cleaning reminders here "
        "3 times every day until the current person accepts.",
        parse_mode=ParseMode.HTML,
    )


async def testreminder_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_owner(update):
        return

    state = load_state()

    ensure_current_week(state)

    people = current_turn(state)

    text = reminder_text(state)

    logger.info(
        "TEST REMINDER requested by owner in chat %s",
        update.effective_chat.id,
    )

    await update.message.reply_text(
        "🧪 <b>TEST REMINDER</b>\n\n"
        + text,
        parse_mode=ParseMode.HTML,
        reply_markup=accept_keyboard(),
    )


async def nextturn_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_owner(update):
        return

    state = load_state()

    ensure_current_week(state)

    old_people = current_turn(state)

    state["turn_index"] = (
        state["turn_index"] + 1
    ) % len(TURNS)

    state["turn_start_date"] = (
        current_sunday(today_local()).isoformat()
    )

    state["acknowledged"] = False
    state["nag_count"] = 0

    save_state(state)

    new_people = current_turn(state)

    logger.info(
        "OWNER manually advanced turn: %s -> %s",
        names_only(old_people),
        names_only(new_people),
    )

    await update.message.reply_text(
        "⏭️ <b>Turn manually advanced.</b>\n\n"
        f"Previous: {names_only(old_people)}\n"
        f"Next: {names_only(new_people)}",
        parse_mode=ParseMode.HTML,
    )


async def restart_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not await require_owner(update):
        return

    state = load_state()

    state["turn_index"] = 0

    state["turn_start_date"] = (
        current_sunday(today_local()).isoformat()
    )

    state["acknowledged"] = False
    state["nag_count"] = 0

    save_state(state)

    logger.info(
        "Rotation manually restarted."
    )

    await update.message.reply_text(
        "🔄 <b>Rotation restarted!</b>\n\n"
        "Current turn:\n"
        f"{names_only(current_turn(state))}\n\n"
        "The next automatic rotation will happen on Sunday.",
        parse_mode=ParseMode.HTML,
    )


# ============================================================================
# ACCEPT BUTTON
# ============================================================================

async def accept_button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    if query is None:
        return

    clicker = query.from_user

    state = load_state()

    # IMPORTANT:
    # If Sunday happened while the bot was running,
    # update the turn before checking who is allowed to accept.
    ensure_current_week(state)

    people = current_turn(state)

    if not is_person_in_turn(
        clicker.id,
        clicker.username,
        people,
    ):

        await query.answer(
            "This isn't your turn to accept 😄",
            show_alert=True,
        )

        logger.info(
            "Unauthorized accept attempt by %s (%s)",
            clicker.first_name,
            clicker.id,
        )

        return

    await query.answer()

    state["acknowledged"] = True

    save_state(state)

    logger.info(
        "TURN ACCEPTED by %s (%s). Reminders stopped.",
        clicker.first_name,
        clicker.id,
    )

    await query.edit_message_text(
        text=(
            f"✅ <b>{clicker.first_name}</b> accepted "
            f"the cleaning duty!\n\n"
            "The reminders are now stopped for this week.\n\n"
            "Tap below when the house is sparkling."
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=finish_keyboard(),
    )


# ============================================================================
# FINISH BUTTON
# ============================================================================

async def finish_button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    if query is None:
        return

    clicker = query.from_user

    state = load_state()

    ensure_current_week(state)

    people = current_turn(state)

    if not is_person_in_turn(
        clicker.id,
        clicker.username,
        people,
    ):

        await query.answer(
            "Only the person on duty can finish it!",
            show_alert=True,
        )

        return

    await query.answer()

    await query.edit_message_text(
        text=(
            f"🎉 <b>{clicker.first_name}</b> finished "
            "the cleaning!\n\n"
            "Great job. 🧹"
        ),
        parse_mode=ParseMode.HTML,
    )

    group_id = (
        state.get("group_chat_id")
        or query.message.chat_id
    )

    try:

        await context.bot.send_poll(
            chat_id=group_id,
            question=(
                f"How did {names_only(people)} "
                "do on their cleaning duty this week?"
            ),
            options=POLL_OPTIONS,
            is_anonymous=True,
        )

    except Exception:
        logger.exception(
            "Could not send cleaning poll."
        )

    # IMPORTANT:
    # We DO NOT advance the turn here.
    #
    # The turn changes automatically on Sunday.
    logger.info(
        "Cleaning marked finished by %s. "
        "Turn remains unchanged until Sunday.",
        clicker.first_name,
    )


# ============================================================================
# SCHEDULE BUTTON
# ============================================================================

async def schedule_button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    if query is None:
        return

    await query.answer()

    state = load_state()

    ensure_current_week(state)

    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=build_schedule_text(state),
        parse_mode=ParseMode.HTML,
    )


# ============================================================================
# DAILY REMINDER
# ============================================================================

async def daily_reminder(
    context: ContextTypes.DEFAULT_TYPE,
):

    logger.info("=" * 60)
    logger.info("REMINDER JOB FIRED")
    logger.info(
        "Current Rome time: %s",
        datetime.now(TIMEZONE).isoformat(),
    )

    state = load_state()

    # Check whether Sunday changed the turn.
    turn_changed = ensure_current_week(state)

    if turn_changed:

        logger.info(
            "Sunday rotation detected. New turn: %s",
            names_only(current_turn(state)),
        )

    chat_id = state.get("group_chat_id")

    if not chat_id:

        logger.error(
            "REMINDER NOT SENT: group_chat_id is missing!"
        )

        logger.error(
            "Run /setgroup in the target Telegram group."
        )

        return

    if state["acknowledged"]:

        logger.info(
            "REMINDER NOT SENT: current turn already accepted."
        )

        return

    people = current_turn(state)

    text = reminder_text(state)

    logger.info(
        "Attempting reminder #%s for %s -> chat %s",
        state["nag_count"] + 1,
        names_only(people),
        chat_id,
    )

    try:

        message = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=accept_keyboard(),
        )

        state["nag_count"] = (
            state.get("nag_count", 0) + 1
        )

        save_state(state)

        logger.info(
            "✅ REMINDER SENT SUCCESSFULLY"
        )

        logger.info(
            "Telegram message_id=%s",
            message.message_id,
        )

        logger.info(
            "New nag_count=%s",
            state["nag_count"],
        )

    except Exception as exc:

        logger.exception(
            "❌ REMINDER FAILED TO SEND"
        )

        logger.error(
            "Chat ID was: %s",
            chat_id,
        )

        logger.error(
            "Current turn was: %s",
            names_only(people),
        )

        logger.error(
            "Telegram error: %s",
            exc,
        )


# ============================================================================
# MAIN
# ============================================================================

def main():

    logger.info(
        "Building Telegram application..."
    )

    app = (
        Application
        .builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    # ------------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------------

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
            "restart",
            restart_command,
        )
    )

    app.add_handler(
        CommandHandler(
            "setgroup",
            setgroup_command,
        )
    )

    app.add_handler(
        CommandHandler(
            "testreminder",
            testreminder_command,
        )
    )

    # ------------------------------------------------------------------------
    # Callback buttons
    # ------------------------------------------------------------------------

    app.add_handler(
        CallbackQueryHandler(
            accept_button_handler,
            pattern=f"^{ACCEPT_CALLBACK}$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            finish_button_handler,
            pattern=f"^{FINISH_CALLBACK}$",
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            schedule_button_handler,
            pattern=f"^{SCHEDULE_CALLBACK}$",
        )
    )

    # ------------------------------------------------------------------------
    # THREE DAILY REMINDER JOBS
    # ------------------------------------------------------------------------

    if app.job_queue is None:

        raise RuntimeError(
            "\n"
            "Telegram JobQueue is unavailable.\n\n"
            "Install it with:\n"
            'pip install "python-telegram-bot[job-queue]"\n'
        )

    for reminder_time in REMINDER_TIMES:

        app.job_queue.run_daily(
            daily_reminder,
            time=reminder_time,
            name=f"cleaning_reminder_{reminder_time.hour:02d}_{reminder_time.minute:02d}",
        )

        logger.info(
            "Scheduled cleaning reminder at %s",
            reminder_time,
        )

    # ------------------------------------------------------------------------
    # Start
    # ------------------------------------------------------------------------

    logger.info(
        "Starting Telegram polling..."
    )

    logger.info(
        "Reminder schedule:"
    )

    for reminder_time in REMINDER_TIMES:
        logger.info(
            "  -> %s Europe/Rome",
            reminder_time,
        )

    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
    )


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    main()
