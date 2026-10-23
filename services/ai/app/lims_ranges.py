def classify(value: float, ref: dict) -> str:
    pl, ph, lo, hi = ref.get("panic_low"), ref.get("panic_high"), ref.get("low"), ref.get("high")
    if pl is not None and value <= pl:
        return "LL"
    if ph is not None and value >= ph:
        return "HH"
    if lo is not None and value < lo:
        return "L"
    if hi is not None and value > hi:
        return "H"
    return "N"
