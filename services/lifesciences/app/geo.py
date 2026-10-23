import math


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def geofence_decision(distance_m: float, radius_m: float, accuracy_m: float | None) -> tuple[bool, str]:
    """Accept only when the whole accuracy circle sits inside the fence, and reject vague fixes."""
    acc = accuracy_m if accuracy_m is not None else 0.0
    if acc > 100:
        return False, f"location accuracy {acc:.0f} m is too coarse (max 100 m)"
    if distance_m + acc <= radius_m:
        return True, "inside verified clinic zone"
    return False, f"{distance_m:.0f} m from clinic (fence radius {radius_m:.0f} m, accuracy {acc:.0f} m)"


def relevance(asset_tags: list[str], interests: list[str], topics: list[str], specialty: str, practice_mix: dict) -> float:
    """Non-PHI relevance: overlap of asset tags with specialty, stated interests, publication topics and aggregate practice mix."""
    tags = {t.lower() for t in asset_tags}
    score = 0.0
    if specialty.lower() in tags:
        score += 2.0
    score += 1.5 * len(tags & {i.lower() for i in interests})
    score += 1.0 * sum(1 for t in topics for tag in tags if tag in t.lower())
    for k, share in practice_mix.items():
        key = k.replace("_", " ").lower()
        if any(key in tag or tag in key for tag in tags):
            score += 3.0 * float(share)
    return round(score, 2)
