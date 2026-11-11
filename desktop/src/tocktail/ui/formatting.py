def duration_text(milliseconds: int, *, seconds: bool = False) -> str:
    total_seconds = max(0, milliseconds) // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02}m"
    if seconds:
        return f"{minutes}m {secs:02}s" if minutes else f"{secs}s"
    return f"{minutes}m" if minutes else (f"{secs}s" if secs else "0m")


def application_name(process_name: str) -> str:
    known = {"code.exe": "Visual Studio Code", "chrome.exe": "Google Chrome",
             "msedge.exe": "Microsoft Edge", "explorer.exe": "File Explorer",
             "windowsterminal.exe": "Windows Terminal", "firefox.exe": "Firefox"}
    return known.get(process_name.casefold(), process_name.removesuffix(".exe"))
