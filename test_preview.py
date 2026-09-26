"""
Local preview script - NOT part of the running bot.

Prints exactly what every bot message looks like, without needing a
token, network access, or Telegram at all. Run it directly:

    python test_preview.py

Useful whenever you tweak TURNS, FUNNY_WARNINGS, DENIED_MESSAGES, or
any of the text-building functions in bot.py, and want to see the
result before deploying.
"""

import bot  # imports TURNS, FUNNY_WARNINGS, DENIED_MESSAGES, and text builders


def divider(title: str):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def fake_state(turn_index: int = 0, acknowledged: bool = False) -> dict:
    return {"turn_index": turn_index, "group_chat_id": -1001234567890, "acknowledged": acknowledged}


def main():
    divider("Configured turns")
    for i, people in enumerate(bot.TURNS):
        print(f"Week {i + 1}: {bot.names_only(people)}  ->  mentions: {bot.mentions_joined(people)}")

    divider("/whoseturn output (turn 1, not confirmed)")
    print(bot.build_whoseturn_text(fake_state(0, acknowledged=False)))

    divider("/whoseturn output (turn 1, confirmed)")
    print(bot.build_whoseturn_text(fake_state(0, acknowledged=True)))

    divider("/schedule (or Weekly Schedule button) output — turn 3 active")
    print(bot.build_schedule_text(fake_state(2)))

    for idx in range(len(bot.TURNS)):
        people = bot.TURNS[idx]
        divider(f"Reminder 1 (Friday evening) — turn {idx + 1}: {bot.names_only(people)}")
        print(bot.reminder1_text(people))

        divider(f"Reminder 2 (Saturday morning) — turn {idx + 1}: {bot.names_only(people)}")
        print(bot.reminder2_text(people))

        divider(f"Reminder 3 (Saturday afternoon, all possible warnings) — turn {idx + 1}")
        for w in range(len(bot.FUNNY_WARNINGS)):
            print(f"  [{w}] " + bot.reminder3_text(people, warning_index=w))

    divider("Owner-only denial messages (all 5)")
    for i in range(len(bot.DENIED_MESSAGES)):
        print(f"  [{i}] " + bot.denied_text(index=i))

    divider("is_owner() behavior")
    print(f"OWNER_ID is currently set to: {bot.OWNER_ID}")
    if bot.OWNER_ID == 0:
        print("No owner configured yet -> owner-only commands are OPEN to everyone.")
    else:
        print(f"Only user id {bot.OWNER_ID} can run /nextturn and /setgroup.")
        print(f"is_owner({bot.OWNER_ID}) = {bot.is_owner(bot.OWNER_ID)}")
        print(f"is_owner(999999) = {bot.is_owner(999999)}")


if __name__ == "__main__":
    main()
