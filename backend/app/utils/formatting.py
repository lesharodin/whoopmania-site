def format_ms(ms: int | None) -> str:
    if ms is None:
        return "—"

    total_ms = int(ms)

    minutes = total_ms // 60000
    seconds = (total_ms % 60000) // 1000
    milliseconds = total_ms % 1000

    if minutes > 0:
        # M:SS.mmm
        return f"{minutes}:{seconds:02d}.{milliseconds:03d}"
    else:
        # SS.mmm
        return f"{seconds}.{milliseconds:03d}"
