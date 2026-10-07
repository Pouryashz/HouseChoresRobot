"""
HOUSE CLEANING BOT
==================

Cleaning rotation:
    - Each turn lasts Sunday -> Saturday.
    - A new person/group gets the turn every Sunday.
    - Reminders are sent 3 times per day:
        09:00
        15:00
        21:00
      Europe/Rome time.
    - Reminders stop when the person whose turn it is accepts.
    - Finishing the cleaning does NOT advance the rotation.
    - The next Sunday advances the rotation automatically.

IMPORTANT:
    The reminder system intentionally does NOT rely on run_daily().
    Instead, a watchdog runs every 20 seconds and checks the
    Europe/Rome clock.

Install:
    pip install -U "python-telegram-bot[job-queue]"

Environment variables:
    HC_BOT_TOKEN=your_token

Optional:
    HC_GROUP_CHAT_ID=-100xxxxxxxxxx
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
    level=logging.INFO,
    format=(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(name)s | "
        "%(message)s"
    ),
)

logger = logging.getLogger("HouseCleaningBot")


# ============================================================================
# CONFIG
# ============================================================================

TOKEN = os.environ.get("HC_BOT_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "HC_BOT_TOKEN is not set."
    )


OWNER_ID = 1738272640


TIMEZONE = ZoneInfo("Europe/Rome")


# ============================================================================
# REMINDER TIMES
# ============================================================================

# IMPORTANT:
# These are interpreted as EUROPE/ROME times.
#
# 09:00
# 15:00
# 21:00

REMINDER_SLOTS = {
    "09:00",
    "15:00",
    "21:00",
}


# How often the watchdog checks the clock.
#
# 20 seconds is more than enough.
WATCHDOG_INTERVAL_SECONDS = 20


# ============================================================================
# GROUP
# ============================================================================

ENV_GROUP_CHAT_ID = os.environ.get(
    "HC_GROUP_CHAT_ID"
)

if ENV_GROUP_CHAT_ID:

    try:
        ENV_GROUP_CHAT_ID = int(
            ENV_GROUP_CHAT_ID
        )

    except ValueError:

        raise RuntimeError(
            "HC_GROUP_CHAT_ID must be a Telegram numeric chat ID."
        )


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

            # ADD DANIELE'S USERNAME OR USER ID HERE.
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
# CALLBACKS
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
    "Unfortunately, the protest is against YOU.",

    "Breaking news: {names} has not been seen anywhere near a trash bag. "
    "Search parties are forming.",

    "{names}, the dust bunnies are unionizing.",

    "{names}, your cleaning responsibilities would like to remind you "
    "that they still exist.",

    "🚨 CLEANING POLICE 🚨 "
    "{names} have been reported for suspicious levels of uncleanliness.",

    "The house called. "
    "It wants {names} to stop pretending they don't live here.",

    "🧹 {names}, this is your final warning before the broom develops "
    "sentience and comes looking for you.",
]


DENIED_MESSAGES = [

    "I'm not working for you!",

    "Nice try. That's not your turn 😈",

    "Access denied. Wait for your cleaning week.",

    "You cannot steal somebody else's cleaning turn.",

    "Wrong victim. The broom has selected somebody else.",
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

STATE_FILE = (
    Path(__file__).parent / "state.json"
)


def default_state():

    return {

        "turn_index": 0,

        "turn_start_date": None,

        "group_chat_id": ENV_GROUP_CHAT_ID,

        "acknowledged": False,

        "nag_count": 0,

        # Example:
        #
        # "2026-10-07|09:00"
        #
        # This prevents the watchdog from sending
        # the same reminder repeatedly.
        "sent_slots": [],

    }


def load_state():

    if not STATE_FILE.exists():

        logger.info(
            "state.json does not exist. "
            "Creating new state."
        )

        return default_state()

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8",
        ) as f:

            state = json.load(f)

    except Exception as exc:

        logger.exception(
            "Could not read state.json: %s",
            exc,
        )

        return default_state()

    defaults = default_state()

    for key, value in defaults.items():

        state.setdefault(
            key,
            value,
        )

    # Environment variable wins.
    if ENV_GROUP_CHAT_ID is not None:

        state["group_chat_id"] = (
            ENV_GROUP_CHAT_ID
        )

    return state


def save_state(state):

    temp_file = STATE_FILE.with_suffix(
        ".tmp"
    )

    try:

        with open(
            temp_file,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                state,
                f,
                indent=2,
                ensure_ascii=False,
            )

        temp_file.replace(
            STATE_FILE
        )

    except Exception as exc:

        logger.exception(
            "Could not save state: %s",
            exc,
        )


# ============================================================================
# DATE / WEEK
# ============================================================================

def now_rome():

    return datetime.now(
        TIMEZONE
    )


def today_rome():

    return now_rome().date()


def current_sunday(
    d=None,
):

    if d is None:
        d = today_rome()

    # Python:
    #
    # Monday = 0
    # Tuesday = 1
    # ...
    # Saturday = 5
    # Sunday = 6

    days_since_sunday = (
        d.weekday() + 1
    ) % 7

    return (
        d
        - timedelta(
            days=days_since_sunday
        )
    )


def ensure_current_week(
    state,
):

    this_sunday = current_sunday()

    stored = state.get(
        "turn_start_date"
    )

    # First startup.
    if not stored:

        state["turn_start_date"] = (
            this_sunday.isoformat()
        )

        state["acknowledged"] = False

        state["nag_count"] = 0

        state["sent_slots"] = []

        save_state(state)

        logger.info(
            "Initialized first cleaning week: %s",
            this_sunday,
        )

        return False


    try:

        stored_sunday = date.fromisoformat(
            stored
        )

    except ValueError:

        logger.warning(
            "Invalid turn_start_date: %s",
            stored,
        )

        state["turn_start_date"] = (
            this_sunday.isoformat()
        )

        state["acknowledged"] = False

        state["nag_count"] = 0

        state["sent_slots"] = []

        save_state(state)

        return False


    # Same week.
    if stored_sunday == this_sunday:

        return False


    # One or more Sundays passed.
    weeks_passed = (
        this_sunday - stored_sunday
    ).days // 7


    old_index = state[
        "turn_index"
    ]


    state["turn_index"] = (
        old_index + weeks_passed
    ) % len(TURNS)


    state["turn_start_date"] = (
        this_sunday.isoformat()
    )


    # New week means:
    # reminders are active again.
    state["acknowledged"] = False

    state["nag_count"] = 0

    state["sent_slots"] = []


    save_state(state)


    logger.info(
        "============================================"
    )

    logger.info(
        "NEW CLEANING WEEK"
    )

    logger.info(
        "Previous Sunday: %s",
        stored_sunday,
    )

    logger.info(
        "New Sunday: %s",
        this_sunday,
    )

    logger.info(
        "Weeks passed: %s",
        weeks_passed,
    )

    logger.info(
        "Turn index: %s -> %s",
        old_index,
        state["turn_index"],
    )

    logger.info(
        "New turn: %s",
        names_only(
            current_turn(state)
        ),
    )

    logger.info(
        "============================================"
    )

    return True


# ============================================================================
# TURN HELPERS
# ============================================================================

def current_turn(
    state,
):

    return TURNS[
        state["turn_index"]
        % len(TURNS)
    ]


def names_only(
    people,
):

    return " & ".join(
        person["name"]
        for person in people
    )


def mention(
    person,
):

    if person.get("username"):

        return (
            "@"
            + person["username"]
        )

    if person.get("user_id"):

        return (
            f'<a href="tg://user?id='
            f'{person["user_id"]}">'
            f'{person["name"]}'
            f"</a>"
        )

    return person["name"]


def mentions_joined(
    people,
):

    return " & ".join(
        mention(person)
        for person in people
    )


def is_person_in_turn(
    user_id,
    username,
    people,
):

    username = (
        username or ""
    ).lower().lstrip("@")

    for person in people:

        if (
            person.get("user_id")
            and person["user_id"]
            == user_id
        ):

            return True

        configured_username = (
            person.get("username")
            or ""
        ).lower().lstrip("@")

        if (
            configured_username
            and configured_username
            == username
        ):

            return True

    return False


# ============================================================================
# OWNER
# ============================================================================

def is_owner(
    user_id,
):

    return (
        user_id == OWNER_ID
    )


async def require_owner(
    update,
):

    user = update.effective_user

    if not user:
        return False

    if is_owner(user.id):
        return True

    if update.message:

        await update.message.reply_text(
            random.choice(
                DENIED_MESSAGES
            )
        )

    return False


# ============================================================================
# KEYBOARDS
# ============================================================================

def accept_keyboard():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Accept Turn",
                    callback_data=(
                        ACCEPT_CALLBACK
                    ),
                )
            ]
        ]
    )


def finish_keyboard():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🧹 Mark as Finished",
                    callback_data=(
                        FINISH_CALLBACK
                    ),
                )
            ]
        ]
    )


def start_keyboard():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📅 Weekly Schedule",
                    callback_data=(
                        SCHEDULE_CALLBACK
                    ),
                )
            ]
        ]
    )


# ============================================================================
# TEXT
# ============================================================================

def reminder_text(
    state,
):

    people = current_turn(
        state
    )

    if state["nag_count"] == 0:

        return (
            "🚨 <b>CLEANING DUTY!</b>\n\n"
            f"Heads up "
            f"{mentions_joined(people)} — "
            "you're on cleaning + trash duty "
            "this week!\n\n"
            "Tap below to accept your fate. 😈"
        )


    warning = random.choice(
        FUNNY_WARNINGS
    )

    return (
        "🧹 <b>CLEANING REMINDER</b>\n\n"
        f"{warning}\n\n"
        "Accept your turn before the broom "
        "comes for you. 👇"
    )


def build_whoseturn_text(
    state,
):

    ensure_current_week(
        state
    )

    status = (
        "accepted — cleaning in progress"
        if state["acknowledged"]
        else "NOT accepted yet"
    )

    return (
        "🧹 <b>Current Cleaning Turn</b>\n\n"
        f"<b>{names_only(current_turn(state))}</b>\n\n"
        f"Week started: "
        f"<b>{state['turn_start_date']}</b>\n"
        f"Status: <b>{status}</b>"
    )


def build_schedule_text(
    state,
):

    ensure_current_week(
        state
    )

    current_index = (
        state["turn_index"]
        % len(TURNS)
    )

    lines = [
        "📅 <b>Weekly Cleaning Schedule</b>\n"
    ]

    for i, people in enumerate(
        TURNS
    ):

        marker = (
            "👉"
            if i == current_index
            else "🔹"
        )

        lines.append(
            f"{marker} "
            f"<b>Week {i + 1}:</b> "
            f"{names_only(people)}"
        )

    lines.append(
        "\n<i>Rotation changes automatically "
        "every Sunday.</i>"
    )

    return "\n".join(
        lines
    )


# ============================================================================
# COMMANDS
# ============================================================================

BOT_COMMANDS = [

    BotCommand(
        "start",
        "Start the bot",
    ),

    BotCommand(
        "whoseturn",
        "See whose turn it is",
    ),

    BotCommand(
        "schedule",
        "See the cleaning schedule",
    ),

    BotCommand(
        "setgroup",
        "Set this group for reminders",
    ),

    BotCommand(
        "testreminder",
        "Send a reminder now",
    ),

    BotCommand(
        "testtimer",
        "Test scheduled sending",
    ),

    BotCommand(
        "nextturn",
        "Advance turn manually",
    ),

    BotCommand(
        "restart",
        "Restart rotation",
    ),
]


# ============================================================================
# START
# ============================================================================

async def start_command(
    update,
    context,
):

    if (
        update.effective_chat
        and update.effective_chat.type
        in ("group", "supergroup")
        and update.effective_user
        and is_owner(
            update.effective_user.id
        )
    ):

        state = load_state()

        state["group_chat_id"] = (
            update.effective_chat.id
        )

        save_state(state)

        logger.info(
            "Group automatically registered: %s",
            update.effective_chat.id,
        )


    await update.message.reply_text(

        "🧹 <b>House Cleaning Bot</b>\n\n"

        "I manage the cleaning rotation.\n\n"

        "• Sunday → new cleaning turn\n"
        "• 09:00 → reminder\n"
        "• 15:00 → reminder\n"
        "• 21:00 → reminder\n"
        "• Accept → reminders stop\n\n"

        "/whoseturn\n"
        "/schedule\n\n"

        "Admin:\n"
        "/setgroup\n"
        "/testreminder\n"
        "/testtimer\n"
        "/nextturn\n"
        "/restart",

        parse_mode=ParseMode.HTML,

        reply_markup=start_keyboard(),
    )


# ============================================================================
# SET GROUP
# ============================================================================

async def setgroup_command(
    update,
    context,
):

    if not await require_owner(
        update
    ):
        return

    chat = update.effective_chat

    if not chat:

        await update.message.reply_text(
            "I can't determine this chat."
        )

        return


    if chat.type not in (
        "group",
        "supergroup",
    ):

        await update.message.reply_text(
            "Run /setgroup inside the group."
        )

        return


    state = load_state()

    state["group_chat_id"] = (
        chat.id
    )

    save_state(state)


    try:

        await context.bot.set_my_commands(
            BOT_COMMANDS,
            scope=BotCommandScopeChat(
                chat_id=chat.id
            ),
        )

    except Exception:

        logger.exception(
            "Could not register group commands."
        )


    logger.info(
        "GROUP SET: %s",
        chat.id,
    )


    await update.message.reply_text(

        "✅ <b>Group configured!</b>\n\n"

        f"Chat ID: "
        f"<code>{chat.id}</code>\n\n"

        "Reminder system is armed.\n"
        "It checks the Europe/Rome clock "
        "every 20 seconds.",

        parse_mode=ParseMode.HTML,
    )


# ============================================================================
# WHOSE TURN
# ============================================================================

async def whoseturn_command(
    update,
    context,
):

    state = load_state()

    ensure_current_week(
        state
    )

    if state["acknowledged"]:

        await update.message.reply_text(
            build_whoseturn_text(
                state
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=finish_keyboard(),
        )

    else:

        await update.message.reply_text(
            build_whoseturn_text(
                state
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=accept_keyboard(),
        )


# ============================================================================
# SCHEDULE
# ============================================================================

async def schedule_command(
    update,
    context,
):

    state = load_state()

    ensure_current_week(
        state
    )

    await update.message.reply_text(
        build_schedule_text(
            state
        ),
        parse_mode=ParseMode.HTML,
    )


# ============================================================================
# TEST REMINDER
# ============================================================================

async def testreminder_command(
    update,
    context,
):

    if not await require_owner(
        update
    ):
        return

    state = load_state()

    ensure_current_week(
        state
    )

    await update.message.reply_text(

        "🧪 <b>TEST REMINDER</b>\n\n"
        + reminder_text(state),

        parse_mode=ParseMode.HTML,

        reply_markup=accept_keyboard(),
    )


# ============================================================================
# TEST TIMER
# ============================================================================

async def testtimer_command(
    update,
    context,
):

    if not await require_owner(
        update
    ):
        return


    chat_id = (
        update.effective_chat.id
    )


    # Schedule a one-off test exactly
    # 60 seconds from now.
    when = datetime.now(
        TIMEZONE
    ) + timedelta(
        seconds=60
    )


    context.job_queue.run_once(

        scheduled_test_message,

        when=when,

        chat_id=chat_id,

        name=(
            "TEST_TIMER_"
            + str(chat_id)
        ),
    )


    now = now_rome()


    await update.message.reply_text(

        "⏱️ <b>Timer test armed.</b>\n\n"

        f"Current Rome time:\n"
        f"<code>{now.strftime('%Y-%m-%d %H:%M:%S')}</code>\n\n"

        f"I will send the test message at:\n"
        f"<code>{when.strftime('%Y-%m-%d %H:%M:%S')}</code>\n\n"

        "If you receive it, the hosting scheduler "
        "is working correctly.",

        parse_mode=ParseMode.HTML,
    )


async def scheduled_test_message(
    context,
):

    chat_id = (
        context.job.chat_id
    )

    now = now_rome()

    logger.info(
        "================================================"
    )

    logger.info(
        "⏰ TEST TIMER FIRED"
    )

    logger.info(
        "Rome time: %s",
        now.isoformat(),
    )

    logger.info(
        "Sending test message to %s",
        chat_id,
    )

    try:

        await context.bot.send_message(

            chat_id=chat_id,

            text=(
                "🧪🔥 <b>SCHEDULED MESSAGE TEST PASSED!</b>\n\n"

                "I'm gonna teach you how to be clean! "
                "Whether you like it or not. 😈🧹\n\n"

                "The timer actually fucking worked."
            ),

            parse_mode=ParseMode.HTML,
        )

        logger.info(
            "✅ TEST TIMER MESSAGE SENT"
        )

    except Exception:

        logger.exception(
            "❌ TEST TIMER MESSAGE FAILED"
        )


# ============================================================================
# ACCEPT
# ============================================================================

async def accept_button_handler(
    update,
    context,
):

    query = (
        update.callback_query
    )

    if not query:
        return


    clicker = query.from_user

    state = load_state()

    ensure_current_week(
        state
    )


    people = current_turn(
        state
    )


    if not is_person_in_turn(
        clicker.id,
        clicker.username,
        people,
    ):

        await query.answer(
            "This isn't your turn 😄",
            show_alert=True,
        )

        return


    await query.answer()


    state["acknowledged"] = True

    save_state(state)


    logger.info(
        "TURN ACCEPTED by %s (%s)",
        clicker.first_name,
        clicker.id,
    )


    await query.edit_message_text(

        text=(
            f"✅ <b>{clicker.first_name}</b> "
            "accepted the cleaning duty!\n\n"
            "Congratulations. "
            "The bot will shut the fuck up "
            "until next Sunday. 🧹"
        ),

        parse_mode=ParseMode.HTML,

        reply_markup=finish_keyboard(),
    )


# ============================================================================
# FINISH
# ============================================================================

async def finish_button_handler(
    update,
    context,
):

    query = (
        update.callback_query
    )

    if not query:
        return


    clicker = query.from_user

    state = load_state()

    ensure_current_week(
        state
    )


    people = current_turn(
        state
    )


    if not is_person_in_turn(
        clicker.id,
        clicker.username,
        people,
    ):

        await query.answer(
            "Only the person on duty can finish!",
            show_alert=True,
        )

        return


    await query.answer()


    await query.edit_message_text(

        text=(
            f"🎉 <b>{clicker.first_name}</b> "
            "finished the cleaning!\n\n"
            "The house is apparently clean. "
            "Miracles do happen. 🧹"
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
                "do this week?"
            ),

            options=POLL_OPTIONS,

            is_anonymous=True,
        )

    except Exception:

        logger.exception(
            "Could not send poll."
        )


    # IMPORTANT:
    #
    # DO NOT advance here.
    #
    # Sunday advances the rotation.

    logger.info(
        "Cleaning finished. "
        "Rotation remains unchanged until Sunday."
    )


# ============================================================================
# SCHEDULE BUTTON
# ============================================================================

async def schedule_button_handler(
    update,
    context,
):

    query = (
        update.callback_query
    )

    if not query:
        return


    await query.answer()


    state = load_state()

    ensure_current_week(
        state
    )


    await context.bot.send_message(

        chat_id=query.message.chat_id,

        text=build_schedule_text(
            state
        ),

        parse_mode=ParseMode.HTML,
    )


# ============================================================================
# REMINDER SENDING
# ============================================================================

async def send_cleaning_reminder(
    context,
    slot,
):

    state = load_state()

    ensure_current_week(
        state
    )


    chat_id = state.get(
        "group_chat_id"
    )


    if not chat_id:

        logger.error(
            "❌ Cannot send reminder: "
            "group_chat_id is missing."
        )

        return


    if state["acknowledged"]:

        logger.info(
            "Reminder skipped: "
            "turn already accepted."
        )

        return


    now = now_rome()

    today_string = (
        now.date().isoformat()
    )


    slot_key = (
        f"{today_string}|{slot}"
    )


    # ------------------------------------------------------------
    # DUPLICATE PROTECTION
    # ------------------------------------------------------------

    if slot_key in state.get(
        "sent_slots",
        [],
    ):

        logger.info(
            "Reminder already sent for %s",
            slot_key,
        )

        return


    people = current_turn(
        state
    )


    logger.info(
        "================================================"
    )

    logger.info(
        "🚨 SENDING CLEANING REMINDER"
    )

    logger.info(
        "Rome time: %s",
        now.strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
    )

    logger.info(
        "Configured slot: %s",
        slot,
    )

    logger.info(
        "Current turn: %s",
        names_only(people),
    )

    logger.info(
        "Chat ID: %s",
        chat_id,
    )


    try:

        message = await context.bot.send_message(

            chat_id=chat_id,

            text=reminder_text(
                state
            ),

            parse_mode=ParseMode.HTML,

            reply_markup=accept_keyboard(),
        )


        # Only mark as sent AFTER Telegram
        # successfully accepts the message.

        state.setdefault(
            "sent_slots",
            []
        ).append(
            slot_key
        )


        state["nag_count"] = (
            state.get(
                "nag_count",
                0,
            ) + 1
        )


        # Keep state file small.
        state["sent_slots"] = (
            state["sent_slots"][-20:]
        )


        save_state(
            state
        )


        logger.info(
            "✅ REMINDER SENT!"
        )

        logger.info(
            "Telegram message ID: %s",
            message.message_id,
        )

        logger.info(
            "Reminder count: %s",
            state["nag_count"],
        )

        logger.info(
            "================================================"
        )


    except Exception as exc:

        logger.exception(
            "❌ FAILED TO SEND REMINDER"
        )

        logger.error(
            "Telegram error: %s",
            exc,
        )


# ============================================================================
# WATCHDOG
# ============================================================================

async def reminder_watchdog(
    context,
):

    now = now_rome()

    current_time = (
        now.strftime("%H:%M")
    )


    logger.info(
        "⏱️ Watchdog: Rome time = %s",
        now.strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
    )


    # ------------------------------------------------------------
    # UPDATE SUNDAY ROTATION
    # ------------------------------------------------------------

    state = load_state()

    turn_changed = (
        ensure_current_week(
            state
        )
    )


    if turn_changed:

        logger.info(
            "Sunday rotation applied."
        )


    # ------------------------------------------------------------
    # CHECK REMINDER SLOT
    # ------------------------------------------------------------

    if current_time not in REMINDER_SLOTS:

        return


    logger.info(
        "🎯 CURRENT TIME MATCHES REMINDER SLOT: %s",
        current_time,
    )


    await send_cleaning_reminder(
        context,
        current_time,
    )


# ============================================================================
# POST INIT
# ============================================================================

async def post_init(
    application,
):

    logger.info(
        "================================================"
    )

    logger.info(
        "HOUSE CLEANING BOT STARTING"
    )

    logger.info(
        "================================================"
    )


    # ------------------------------------------------------------
    # Telegram commands
    # ------------------------------------------------------------

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


    # ------------------------------------------------------------
    # JobQueue check
    # ------------------------------------------------------------

    if application.job_queue is None:

        raise RuntimeError(

            "JobQueue is unavailable.\n\n"

            'Run:\n'
            'pip install -U '
            '"python-telegram-bot[job-queue]"'
        )


    logger.info(
        "✅ JobQueue available."
    )


    # ------------------------------------------------------------
    # State
    # ------------------------------------------------------------

    state = load_state()

    ensure_current_week(
        state
    )


    logger.info(
        "Group chat ID: %s",
        state.get(
            "group_chat_id"
        ),
    )

    logger.info(
        "Current turn: %s",
        names_only(
            current_turn(state)
        ),
    )

    logger.info(
        "Turn started: %s",
        state.get(
            "turn_start_date"
        ),
    )

    logger.info(
        "Accepted: %s",
        state["acknowledged"],
    )

    logger.info(
        "Nag count: %s",
        state["nag_count"],
    )


    # ------------------------------------------------------------
    # WATCHDOG
    # ------------------------------------------------------------

    application.job_queue.run_repeating(

        reminder_watchdog,

        interval=WATCHDOG_INTERVAL_SECONDS,

        first=5,

        name="cleaning_reminder_watchdog",
    )


    logger.info(
        "================================================"
    )

    logger.info(
        "WATCHDOG STARTED"
    )

    logger.info(
        "Checks every %s seconds.",
        WATCHDOG_INTERVAL_SECONDS,
    )

    logger.info(
        "Reminder slots: %s",
        sorted(
            REMINDER_SLOTS
        ),
    )

    logger.info(
        "Timezone: Europe/Rome"
    )

    logger.info(
        "================================================"
    )


    # ------------------------------------------------------------
    # STARTUP MESSAGE
    # ------------------------------------------------------------

    chat_id = state.get(
        "group_chat_id"
    )


    if chat_id:

        try:

            await application.bot.send_message(

                chat_id=chat_id,

                text=(

                    "🧹😈 <b>MESSAGE SYSTEM ONLINE.</b>\n\n"

                    "I'm gonna teach you how to be clean!\n\n"

                    "The cleaning police are watching. 👀\n\n"

                    "⏰ Reminder watchdog: ONLINE\n"
                    "🇮🇹 Timezone: Europe/Rome\n"
                    "🕘 09:00 / 15:00 / 21:00"

                ),

                parse_mode=ParseMode.HTML,
            )


            logger.info(
                "✅ STARTUP MESSAGE SENT SUCCESSFULLY."
            )


        except Exception:

            logger.exception(
                "❌ STARTUP MESSAGE FAILED."
            )

    else:

        logger.warning(
            "⚠️ NO GROUP CHAT ID. "
            "Run /setgroup."
        )


# ============================================================================
# MAIN
# ============================================================================

def main():

    logger.info(
        "Creating Telegram application..."
    )


    application = (

        Application
        .builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )


    # ------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            start_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "whoseturn",
            whoseturn_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "schedule",
            schedule_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "setgroup",
            setgroup_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "testreminder",
            testreminder_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "testtimer",
            testtimer_command,
        )
    )


    # ------------------------------------------------------------
    # Owner commands
    # ------------------------------------------------------------

    application.add_handler(
        CommandHandler(
            "nextturn",
            nextturn_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "restart",
            restart_command,
        )
    )


    # ------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            accept_button_handler,
            pattern=(
                f"^{ACCEPT_CALLBACK}$"
            ),
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            finish_button_handler,
            pattern=(
                f"^{FINISH_CALLBACK}$"
            ),
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            schedule_button_handler,
            pattern=(
                f"^{SCHEDULE_CALLBACK}$"
            ),
        )
    )


    # ------------------------------------------------------------
    # START
    # ------------------------------------------------------------

    logger.info(
        "Starting Telegram polling..."
    )


    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":

    main()
