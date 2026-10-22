"""One time per matching day, represented by an ordinary five-field cron."""
from datetime import time


def simple_schedule(value):
    try:
        minute, hour, day, month, weekday = value.split()
        if month != "*" or not minute.isdigit() or not hour.isdigit():
            raise ValueError
        at = time(int(hour), int(minute))
        if day == "*" and weekday == "*":
            return {"mode": "daily", "time": at}
        if day == "*":
            days = [int(part) for part in weekday.split(",")]
            if not days or any(d < 0 or d > 6 for d in days):
                raise ValueError
            return {"mode": "weekly", "time": at, "weekdays": days}
        if weekday == "*" and day.isdigit() and 1 <= int(day) <= 31:
            return {"mode": "monthly", "time": at, "day": int(day)}
    except (ValueError, AttributeError):
        pass
    raise ValueError("Выберите ежедневное, еженедельное или ежемесячное расписание и время.")


def due_today(value, now):
    schedule = simple_schedule(value)
    if now.time().replace(tzinfo=None) < schedule["time"]:
        return False
    if schedule["mode"] == "weekly":
        return now.weekday() in schedule["weekdays"]
    if schedule["mode"] == "monthly":
        return now.day == schedule["day"]
    return True


def schedule_label(value):
    try:
        schedule = simple_schedule(value)
    except ValueError:
        return "Расширенное расписание"
    at = schedule["time"].strftime("%H:%M")
    if schedule["mode"] == "weekly":
        names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        return ", ".join(names[d] for d in sorted(set(schedule["weekdays"]))) + " в " + at
    if schedule["mode"] == "monthly":
        return f"Ежемесячно, {schedule['day']} числа в {at}"
    return "Ежедневно в " + at
