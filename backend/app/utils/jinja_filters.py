def format_float_clean(value):
    """
    Показывает:
      9.0 → 9
      8.2 → 8.2
      None → —
    """
    if value is None:
        return "—"

    try:
        f = float(value)
    except Exception:
        return value

    if f.is_integer():
        return str(int(f))
    else:
        # уберём хвост .0 но оставим .1 .2 ...
        # round на всякий случай, можно убрать
        return str(round(f, 3)).rstrip("0").rstrip(".")
