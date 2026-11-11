
import os
from datetime import datetime, timezone
from threading import Lock

from flask import Flask, jsonify, request, render_template_string

app = Flask(__name__)

DEVICE_API_KEY = os.environ.get("DEVICE_API_KEY", "")

OFFLINE_AFTER_SECONDS = int(
    os.environ.get("OFFLINE_AFTER_SECONDS", "75")
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


# ======================================
# HTML DASHBOARD
# ======================================

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">

<title>Smart Dustbin Dashboard</title>

<style>
* {
    box-sizing: border-box;
}

body {
    margin: 0;
    padding: 20px;
    background: #0b1220;
    color: #ffffff;
    font-family: Arial, sans-serif;
}

.container {
    max-width: 950px;
    margin: auto;
}

h1 {
    margin-bottom: 5px;
}

.subtitle {
    color: #94a3b8;
    margin-bottom: 25px;
}

.status {
    padding: 15px;
    border-radius: 12px;
    background: #172337;
    margin-bottom: 20px;
}

.online {
    color: #22c55e;
}

.offline {
    color: #ef4444;
}

.grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 15px;
}

.card {
    background: #172337;
    padding: 22px;
    border-radius: 14px;
    border: 1px solid #26364f;
}

.card h3 {
    color: #94a3b8;
    margin: 0 0 15px;
    font-size: 15px;
}

.value {
    font-size: 30px;
    font-weight: bold;
}

.bar {
    height: 12px;
    background: #334155;
    border-radius: 20px;
    margin-top: 15px;
    overflow: hidden;
}

.fill {
    height: 100%;
    width: 0%;
    background: #22c55e;
    transition: width 0.3s;
}

.green {
    color: #22c55e;
}

.red {
    color: #ef4444;
}

.footer {
    color: #64748b;
    font-size: 13px;
    margin-top: 25px;
}
</style>
</head>

<body>

<div class="container">

<h1>Smart Dustbin</h1>

<p class="subtitle">Live IoT Monitoring Dashboard</p>

<div class="status">
    Device Status:
    <strong id="online" class="offline">Checking...</strong>
    <br><br>
    Last Update: <span id="lastSeen">Waiting...</span>
</div>

<div class="grid">

    <div class="card">
        <h3>Dustbin Fill Level</h3>
        <div class="value" id="fill">--%</div>
        <div class="bar">
            <div class="fill" id="fillBar"></div>
        </div>
    </div>

    <div class="card">
        <h3>Ultrasonic Distance</h3>
        <div class="value" id="distance">-- cm</div>
    </div>

    <div class="card">
        <h3>Lid Status</h3>
        <div class="value" id="lid">--</div>
    </div>

    <div class="card">
        <h3>Buzzer</h3>
        <div class="value" id="buzzer">--</div>
    </div>

    <div class="card">
        <h3>Full Alert</h3>
        <div class="value" id="alert">--</div>
    </div>

</div>

<div class="footer">
    Dashboard refreshes every 1 second.
</div>

</div>

<script>

function setState(id, value, isOn) {

    const element = document.getElementById(id);

    element.textContent = value;

    element.className =
        "value " + (isOn ? "green" : "red");
}

async function updateDashboard() {

    try {

        const response = await fetch(
            "/api/status?_=" + Date.now(),
            { cache: "no-store" }
        );

        if (!response.ok) {
            throw new Error("Server response error");
        }

        const data = await response.json();

        const online = document.getElementById("online");

        online.textContent =
            data.online ? "ONLINE" : "OFFLINE";

        online.className =
            data.online ? "online" : "offline";

        document.getElementById("fill").textContent =
            data.fill_level === null
            ? "--%"
            : Number(data.fill_level).toFixed(1) + "%";

        document.getElementById("fillBar").style.width =
            data.fill_level === null
            ? "0%"
            : Math.max(0, Math.min(100, data.fill_level)) + "%";

        document.getElementById("distance").textContent =
            data.distance === null
            ? "-- cm"
            : Number(data.distance).toFixed(1) + " cm";

        setState(
            "lid",
            data.lid_open ? "OPEN" : "CLOSED",
            data.lid_open
        );

        setState(
            "buzzer",
            data.buzzer_on ? "ON" : "OFF",
            data.buzzer_on
        );

        setState(
            "alert",
            data.full_alert ? "FULL" : "NORMAL",
            data.full_alert
        );

        document.getElementById("lastSeen").textContent =
            data.last_seen
            ? new Date(data.last_seen).toLocaleTimeString()
            : "No data received";

    }

    catch (error) {

        document.getElementById("online").textContent =
            "SERVER ERROR";

        document.getElementById("online").className =
            "offline";
    }
}

updateDashboard();
setInterval(updateDashboard, 1000);

</script>

</body>
</html>
"""


# ======================================
# HOME DASHBOARD
# ======================================

@app.route("/", methods=["GET"])
def home():
    return render_template_string(DASHBOARD_HTML)


# ======================================
# HEALTH CHECK
# ======================================

@app.route("/health", methods=["GET"])
def health():

    return jsonify({
        "ok": True,
        "server": "online"
    })


# ======================================
# RECEIVE DEVICE DATA
# ======================================

@app.route("/api/data", methods=["POST"])
def receive_data():

    if not DEVICE_API_KEY:
        return jsonify({
            "error": "Server API key is not configured"
        }), 503

    supplied_key = request.headers.get(
        "X-Device-Key", ""
    )

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

        if not 0 <= fill <= 100:
            raise ValueError("Fill level must be between 0 and 100")

        distance = data.get("distance")

        if distance is not None:
            distance = float(distance)

            if not 0 <= distance <= 400:
                raise ValueError("Invalid distance")

        full_alert = convert_bool(data["full_alert"])
        lid_open = convert_bool(data["lid_open"])
        buzzer_on = convert_bool(data["buzzer_on"])

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
                if distance is not None
                else None
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


# ======================================
# DASHBOARD STATUS API
# ======================================

@app.route("/api/status", methods=["GET"])
def get_status():

    with lock:
        result = dict(latest_data)

    online = False

    if result["last_seen"] is not None:

        last_seen = datetime.fromisoformat(
            result["last_seen"]
        )

        elapsed = (
            current_time() - last_seen
        ).total_seconds()

        online = elapsed <= OFFLINE_AFTER_SECONDS

    result["online"] = online

    return jsonify(result)


# ======================================
# START SERVER
# ======================================

if __name__ == "__main__":

    port = int(os.environ.get("PORT", 5000))

    app.run(
        host="0.0.0.0",
        port=port
    )
