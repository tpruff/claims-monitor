import requests
import json
import os
import sys
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────
WEBHOOK_URL  = os.environ.get("DISCORD_WEBHOOK_URL", "")
STATE_FILE   = "state.json"

# BlueMap exposes marker data at /maps/<map-id>/markers.json
# Update MAP_ID if you are watching a different world
MAP_ID       = "abex"
BASE_URL     = "https://map.stoneworks.gg"
MARKERS_URL  = f"{BASE_URL}/maps/{MAP_ID}/markers.json"

# Only watch specific marker-set IDs (case-insensitive substring match).
# Leave empty [] to watch ALL marker sets.
WATCH_SETS   = []   # e.g. ["claims", "towny", "grief"]
# ──────────────────────────────────────────────────────────────────────────────


def fetch_markers() -> dict:
    headers = {"User-Agent": "ClaimMonitor/1.0"}
    r = requests.get(MARKERS_URL, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def extract_claims(data: dict) -> dict:
    """
    BlueMap markers.json structure:
      { "markerSets": { "<setId>": { "label": "...", "markers": { "<markerId>": {...} } } } }

    Returns a flat dict keyed by "<setId>::::<markerId>" for easy diffing.
    """
    claims = {}
    marker_sets = data.get("markerSets", {})

    for set_id, marker_set in marker_sets.items():
        # Filter by watched sets if configured
        if WATCH_SETS:
            if not any(w.lower() in set_id.lower() for w in WATCH_SETS):
                continue

        set_label = marker_set.get("label", set_id)
        markers   = marker_set.get("markers", {})

        for marker_id, marker in markers.items():
            key = f"{set_id}::::{marker_id}"
            claims[key] = {
                "marker_id":  marker_id,
                "set_id":     set_id,
                "set_label":  set_label,
                "label":      marker.get("label", marker_id),
                "detail":     marker.get("detail", ""),
                "position":   marker.get("position", {}),
            }
    return claims


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def pos_str(position: dict) -> str:
    if not position:
        return "unknown"
    x = position.get("x", "?")
    z = position.get("z", "?")
    return f"X:{x} Z:{z}"


def send_discord(embeds: list) -> None:
    if not WEBHOOK_URL:
        print("⚠  DISCORD_WEBHOOK_URL not set — skipping webhook.")
        return
    payload = {"embeds": embeds, "username": "Claim Monitor 🗺️"}
    r = requests.post(WEBHOOK_URL, json=payload, timeout=15)
    if r.status_code not in (200, 204):
        print(f"Webhook error {r.status_code}: {r.text}")
    else:
        print("  → Discord notified.")


def build_embed(claim: dict, action: str) -> dict:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if action == "removed":
        color  = 0xFF4444
        title  = f"🔴 Claim Removed"
    else:
        color  = 0x44FF88
        title  = f"🟢 New Claim"

    fields = [
        {"name": "Marker Set", "value": f"`{claim['set_label']}` (`{claim['set_id']}`)", "inline": True},
        {"name": "Marker ID",  "value": f"`{claim['marker_id']}`",                        "inline": True},
        {"name": "Position",   "value": pos_str(claim["position"]),                        "inline": True},
    ]
    if claim["detail"]:
        fields.append({"name": "Details", "value": claim["detail"][:1024], "inline": False})

    return {
        "title":       title,
        "description": f"**{claim['label']}**",
        "color":       color,
        "fields":      fields,
        "footer":      {"text": f"Stoneworks BlueMap • {now}"},
        "url":         BASE_URL,
    }


def main() -> None:
    print(f"Fetching {MARKERS_URL} …")
    try:
        data = fetch_markers()
    except Exception as e:
        print(f"❌ Fetch failed: {e}")
        sys.exit(1)

    current  = extract_claims(data)
    previous = load_state()

    print(f"  Current markers : {len(current)}")
    print(f"  Previous markers: {len(previous)}")

    if not previous:
        print("ℹ  No previous state found — saving baseline. No alerts sent.")
        save_state(current)
        return

    removed = {k: v for k, v in previous.items() if k not in current}
    added   = {k: v for k, v in current.items()  if k not in previous}

    if not removed and not added:
        print("✅ No changes detected.")
        save_state(current)
        return

    embeds = []
    for claim in removed.values():
        print(f"  🔴 REMOVED: {claim['label']} [{claim['set_id']}]")
        embeds.append(build_embed(claim, "removed"))

    for claim in added.values():
        print(f"  🟢 ADDED:   {claim['label']} [{claim['set_id']}]")
        embeds.append(build_embed(claim, "added"))

    # Discord allows max 10 embeds per request — chunk if needed
    for i in range(0, len(embeds), 10):
        send_discord(embeds[i:i + 10])

    save_state(current)
    print(f"Done. {len(removed)} removed, {len(added)} added.")


if __name__ == "__main__":
    main()
