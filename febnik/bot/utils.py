def norm_username(username: str | None) -> str | None:
    if not username:
        return None
    u = username.strip().lstrip("@").lower()
    return u if u else None
