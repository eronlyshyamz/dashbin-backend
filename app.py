
import os
import math
from datetime import datetime, timezone
from threading import Lock

from flask import Flask, jsonify, request, render_template

app = Flask(__name__)

DEVICE_API_KEY = os.environ.get("DEVICE_API_KEY", "")

OFFLINE_AFTER_SECONDS = int(
    os.environ.get("OFFLINE_AFTER_SECONDS", "15")
)

lock = Lock()

latest_data = {
    "fill_level": None,
    "distance": None,
    "full_alert": None,
    "lid_open": None,
    "buzzer_on": None,
    "last_seen": None
}


def current_time():
    return datetime.now(timezone.utc)


def convert_bool(value):
    if isinstance(value, bool):
        return value

    if value == 0:
        return False

    if value == 1:
        return True

    raise ValueError("Invalid boolean value")


# DASHBOARD
@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")


# HEALTH CHECK
@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "server": "online"
    })


# RECEIVE ESP8266 DATA
@app.route("/api/data", methods=["POST"])
def receive_data():

    if not DEVICE_API_KEY:
        return jsonify({
            "error": "Server API key is not configured"
        }), 503

    supplied_key = request.headers.get("X-Device-Key", "")

    if supplied_key != DEVICE_API_KEY:
        return jsonify({
            "error": "Unauthorized"
        }), 401

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "error": "Invalid JSON data"
        }), 400

    try:
        fill = float(data["fill_level"])

        if not math.isfinite(fill) or not 0 <= fill <= 100:
            raise ValueError("Fill level must be between 0 and 100")

        full_alert = convert_bool(data["full_alert"])
        lid_open = convert_bool(data["lid_open"])
        buzzer_on = convert_bool(data["buzzer_on"])

        # Distance is optional until Arduino code is updated.
        distance = data.get("distance")

        if distance is not None:
            distance = float(distance)

            if not math.isfinite(distance) or not 0 <= distance <= 400:
                raise ValueError("Invalid distance value")

    except KeyError as error:
        return jsonify({
            "error": "Missing field: " + str(error)
        }), 400

    except (ValueError, TypeError) as error:
        return jsonify({
            "error": str(error)
        }), 400

    received_time = current_time().isoformat()

    with lock:
        latest_data.update({
            "fill_level": round(fill, 2),
            "distance": (
                round(distance, 2)
                if distance is not None else None
            ),
            "full_alert": full_alert,
            "lid_open": lid_open,
            "buzzer_on": buzzer_on,
            "last_seen": received_time
        })

    return jsonify({
        "success": True,
        "message": "Data received successfully",
        "received_at": received_time
    }), 200


# SEND DATA TO DASHBOARD
@app.route("/api/status", methods=["GET"])
def get_status():

    with lock:
        result = dict(latest_data)

    online = False

    if result["last_seen"] is not None:
        last_seen = datetime.fromisoformat(result["last_seen"])

        elapsed = (
            current_time() - last_seen
        ).total_seconds()

        online = elapsed <= OFFLINE_AFTER_SECONDS

    result["online"] = online

    return jsonify(result)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port
    )
