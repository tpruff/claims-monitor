import requests
import json
import os
import re
import sys
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────
WEBHOOK_URL  = os.environ.get("DISCORD_WEBHOOK_URL", "")
STATE_FILE   = "state.json"

BASE_URL     = "https://map.stoneworks.gg"
MARKERS_URL  = f"{BASE_URL}/abex//maps/abexilas/live/markers.json"

# Only watch specific marker-set IDs (case-insensitive substring match).
# Leave as [] to watch ALL sets.
WATCH_SETS   = []
# ──────────────────────────────────────────────────────────────────────────────


def fetch_markers() -> dict:
    headers = {"User-Agent": "ClaimMonitor/1.0"}
    r = requests.get(MARKERS_URL, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def parse_detail(raw_html: str) -> dict:
    """
    Extracts human-readable fields from the HTML-encoded detail string.

    Example decoded detail:
      >Deadcat</span><br/></span>Village of Antarrhes.</div>
      <ul>
        <li>Level: Village</li>
        <li>Balance: $7,600.00</li>
        <li>Chunks: 9</li>
        <li>Created at: 04/25/2026 11:45</li>
        <li>Players (6): Antarrhes, Camoninja3600, ...</li>
      </ul>
    """
    result = {}

    # Town name appears between </span> and .</div>
    town_match = re.search(r'\</span\>(.*?)\.\</div\>', raw_html)
    if town_match:
        result["town"] = town_match.group(1).strip()

    # Pull every <li>...</li> pair into key/value
    for li in re.findall(r'<li>(.*?)</li>', raw_html):
        if ':' in li:
            key, _, val = li.partition(':')
            result[key.strip().lower()] = val.strip()
        else:
            result[li.strip().lower()] = True

    return result


def extract_claims(data: dict) -> dict:
    """
    Returns a flat dict keyed by "<setId>::::<markerId>".
    """
    claims = {}
    marker_sets = data

    for set_id, marker_set in marker_sets.items():
        if WATCH_SETS:
            if not any(w.lower() in set_id.lower() for w in WATCH_SETS):
                continue

        set_label = marker_set.get("label", set_id)
        markers   = marker_set.get("markers", {})

        for marker_id, marker in markers.items():
            key        = f"{set_id}::::{marker_id}"
            raw_detail = marker.get("detail", "") or ""
            parsed     = parse_detail(raw_detail)

            claims[key] = {
                "marker_id": marker_id,
                "set_id":    set_id,
                "set_label": set_label,
                "label":     marker.get("label", marker_id),
                "position":  marker.get("position", {}),
                # Parsed detail fields (empty string if not found)
                "town":      parsed.get("town", ""),
                "level":     parsed.get("level", ""),
                "balance":   parsed.get("balance", ""),
                "chunks":    parsed.get("chunks", ""),
                "created":   parsed.get("created at", ""),
                "players":   parsed.get(
                                 next((k for k in parsed if k.startswith("players")), ""),
                                 ""
                             ),
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
    y = position.get("y", "?")
    z = position.get("z", "?")
    return f"X: {x},  Y: {y},  Z: {z}"


def map_link(position: dict) -> str:
    """Generate a direct BlueMap link to the claim's coordinates."""
    if not position:
        return BASE_URL
    x = position.get("x", 0)
    y = position.get("y", 64)
    z = position.get("z", 0)
    return f"{BASE_URL}/abex//#abexilas:{x}:{y}:{z}:200:0:0:0:0:perspective"


def build_embed(claim: dict, action: str) -> dict:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if action == "removed":
        color = 0xFF4444
        title = "🔴 Claim Removed"
    else:
        color = 0x44FF88
        title = "🟢 New Claim Added"

    pos  = claim["position"]
    desc = f"**{claim['label']}**"
    if claim["town"]:
        desc += f"\n{claim['town']}"

    fields = [
        {
            "name":   "📍 Coordinates",
            "value":  f"`{pos_str(pos)}`",
            "inline": False,
        },
        {
            "name":   "🗺️ Map Link",
            "value":  f"[Jump to location]({map_link(pos)})",
            "inline": True,
        },
        {
            "name":   "Marker Set",
            "value":  f"`{claim['set_label']}`",
            "inline": True,
        },
    ]

    # Only add detail fields if they were parsed successfully
    detail_pairs = [
        ("⚖️ Level",   claim["level"]),
        ("💰 Balance",  claim["balance"]),
        ("🧱 Chunks",   claim["chunks"]),
        ("📅 Created",  claim["created"]),
        ("👥 Players",  claim["players"]),
    ]
    for name, value in detail_pairs:
        if value:
            fields.append({"name": name, "value": value, "inline": True})

    return {
        "title":       title,
        "description": desc,
        "color":       color,
        "fields":      fields,
        "footer":      {"text": f"Stoneworks BlueMap • {now}"},
        "url":         map_link(pos),
    }


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


def main() -> None:
    print(f"Fetching {MARKERS_URL} …")
    try:
        data = fetch_markers()
    except Exception as e:
        print(f"❌ Fetch failed: {e}")
        sys.exit(1)

    current  = extract_claims(data)
    previous = load_state()

    print(f"  Current claims  : {len(current)}")
    print(f"  Previous claims : {len(previous)}")

    if not previous:
        print("ℹ  No previous state — saving baseline. No alerts sent.")
        save_state(current)
        return

    removed = {k: v for k, v in previous.items() if k not in current}
    added   = {k: v for k, v in current.items()  if k not in previous}

    if not removed:
        print("✅ No changes detected.")
        save_state(current)
        return

    embeds = []
    for claim in removed.values():
        pos = claim["position"]
        print(f"  🔴 REMOVED: {claim['label']}  |  {pos_str(pos)}")
        embeds.append(build_embed(claim, "removed"))

    # Discord allows max 10 embeds per message
    for i in range(0, len(embeds), 10):
        send_discord(embeds[i:i + 10])

    save_state(current)
    print(f"Done — {len(removed)} removed, {len(added)} added.")


if __name__ == "__main__":
    main()
