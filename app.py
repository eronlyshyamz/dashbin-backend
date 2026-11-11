
import os
import json
import asyncio
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

app = FastAPI()

DEVICE_API_KEY = os.environ.get("DEVICE_API_KEY", "")

# Latest device state (kept in memory)
latest_data = {
    "fill_level": None,
    "distance": None,
    "full_alert": False,
    "lid_open": False,
    "buzzer_on": False,
    "last_seen": None,
    "online": False
}

dashboard_clients = set()
state_lock = asyncio.Lock()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def valid_bool(value):
    return isinstance(value, bool)


async def send_to_dashboards(payload):
    message = json.dumps(payload)
    disconnected = []

    for client in list(dashboard_clients):
        try:
            await client.send_text(message)
        except Exception:
            disconnected.append(client)

    for client in disconnected:
        dashboard_clients.discard(client)


@app.get("/health")
async def health():
    return {"ok": True, "server": "online", "transport": "websocket"}


@app.get("/api/status")
async def status():
    async with state_lock:
        result = dict(latest_data)

    if result["last_seen"]:
        last_seen = datetime.fromisoformat(result["last_seen"])
        elapsed = (
            datetime.now(timezone.utc) - last_seen
        ).total_seconds()
        result["online"] = elapsed <= 30

    return result


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return HTMLResponse(DASHBOARD_HTML)


# =====================================
# DEVICE WEBSOCKET
# =====================================

@app.websocket("/ws/device")
async def device_socket(websocket: WebSocket):

    await websocket.accept()

    if not DEVICE_API_KEY:
        await websocket.close(code=1011, reason="Device key not configured")
        return

    try:
        # First message must authenticate the device.
        auth_text = await asyncio.wait_for(
            websocket.receive_text(),
            timeout=10
        )

        auth = json.loads(auth_text)

        if (
            auth.get("type") != "auth"
            or auth.get("key") != DEVICE_API_KEY
        ):
            await websocket.close(code=1008, reason="Unauthorized")
            return

        await websocket.send_json({
            "type": "auth",
            "ok": True,
            "message": "Device authenticated"
        })

        while True:

            raw_text = await websocket.receive_text()
            data = json.loads(raw_text)

            message_type = data.get("type")

            if message_type == "heartbeat":

                async with state_lock:
                    latest_data["last_seen"] = utc_now()
                    latest_data["online"] = True
                    snapshot = dict(latest_data)

                await send_to_dashboards({
                    "type": "status",
                    "data": snapshot
                })

                continue

            if message_type != "data":
                continue

            fill = float(data["fill_level"])

            if not 0 <= fill <= 100:
                continue

            distance = data.get("distance")

            if distance is not None:
                distance = float(distance)
                if not 0 <= distance <= 400:
                    continue

            full_alert = data.get("full_alert")
            lid_open = data.get("lid_open")
            buzzer_on = data.get("buzzer_on")

            if not all(
                valid_bool(v)
                for v in [full_alert, lid_open, buzzer_on]
            ):
                continue

            received_time = utc_now()

            async with state_lock:
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
                    "last_seen": received_time,
                    "online": True
                })

                snapshot = dict(latest_data)

            # Push immediately to all connected dashboards.
            await send_to_dashboards({
                "type": "data",
                "data": snapshot
            })

            await websocket.send_json({
                "type": "ack",
                "ok": True,
                "received_at": received_time
            })

    except asyncio.TimeoutError:
        await websocket.close(code=1008, reason="Authentication timeout")

    except WebSocketDisconnect:
        pass

    except Exception:
        try:
            await websocket.close(code=1011)
        except Exception:
            pass


# =====================================
# DASHBOARD WEBSOCKET
# =====================================

@app.websocket("/ws/dashboard")
async def dashboard_socket(websocket: WebSocket):

    await websocket.accept()
    dashboard_clients.add(websocket)

    async with state_lock:
        snapshot = dict(latest_data)

    if snapshot["last_seen"]:
        last_seen = datetime.fromisoformat(snapshot["last_seen"])
        elapsed = (
            datetime.now(timezone.utc) - last_seen
        ).total_seconds()
        snapshot["online"] = elapsed <= 30

    await websocket.send_json({
        "type": "status",
        "data": snapshot
    })

    try:
        # Keep the connection alive and detect disconnects.
        while True:
            await websocket.receive_text()

    except WebSocketDisconnect:
        dashboard_clients.discard(websocket)

    except Exception:
        dashboard_clients.discard(websocket)


# =====================================
# HTML DASHBOARD
# =====================================

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Smart Dustbin Live Dashboard</title>

<style>
* { box-sizing: border-box; }

body {
    margin: 0;
    padding: 22px;
    background: #0b1220;
    color: white;
    font-family: Arial, sans-serif;
}

.container {
    max-width: 950px;
    margin: auto;
}

h1 { margin-bottom: 5px; }

.subtitle {
    color: #94a3b8;
    margin-bottom: 22px;
}

.status, .card {
    background: #172337;
    border: 1px solid #26364f;
    border-radius: 14px;
    padding: 20px;
}

.status { margin-bottom: 16px; }

.grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
    gap: 14px;
}

.card h3 {
    color: #94a3b8;
    font-size: 14px;
    margin: 0 0 14px;
}

.value {
    font-size: 29px;
    font-weight: bold;
}

.online { color: #22c55e; }
.offline { color: #ef4444; }
.muted { color: #94a3b8; }

.bar {
    height: 12px;
    background: #334155;
    border-radius: 20px;
    margin-top: 14px;
    overflow: hidden;
}

.fill {
    height: 100%;
    width: 0%;
    background: #22c55e;
    transition: width .12s linear;
}

.small {
    color: #94a3b8;
    font-size: 13px;
    margin-top: 15px;
}
</style>
</head>

<body>
<div class="container">

<h1>Smart Dustbin</h1>
<p class="subtitle">Real-time IoT Dashboard</p>

<div class="status">
    Device: <strong id="device" class="offline">CONNECTING</strong>
    <br><br>
    Dashboard: <strong id="connection" class="muted">CONNECTING</strong>
    <br><br>
    Last device update: <span id="lastSeen">Waiting for data</span>
</div>

<div class="grid">

    <div class="card">
        <h3>Fill Level</h3>
        <div class="value" id="fill">--%</div>
        <div class="bar"><div class="fill" id="fillBar"></div></div>
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

<p class="small">
    Values update when the device sends a new WebSocket message.
</p>

</div>

<script>
let socket;
let reconnectDelay = 1000;
let lastDeviceMessage = 0;

function setValue(id, text, active) {
    const el = document.getElementById(id);
    el.textContent = text;
    el.className = "value " + (active ? "online" : "offline");
}

function showData(data) {
    document.getElementById("fill").textContent =
        data.fill_level === null ? "--%" :
        Number(data.fill_level).toFixed(1) + "%";

    document.getElementById("fillBar").style.width =
        data.fill_level === null ? "0%" :
        Math.max(0, Math.min(100, Number(data.fill_level))) + "%";

    document.getElementById("distance").textContent =
        data.distance === null ? "-- cm" :
        Number(data.distance).toFixed(1) + " cm";

    setValue("lid", data.lid_open ? "OPEN" : "CLOSED", data.lid_open);
    setValue("buzzer", data.buzzer_on ? "ON" : "OFF", data.buzzer_on);
    setValue("alert", data.full_alert ? "FULL" : "NORMAL", data.full_alert);

    if (data.last_seen) {
        document.getElementById("lastSeen").textContent =
            new Date(data.last_seen).toLocaleTimeString();
    }

    lastDeviceMessage = Date.now();
    updateDeviceStatus();
}

function updateDeviceStatus() {
    const el = document.getElementById("device");
    const online = lastDeviceMessage &&
                   (Date.now() - lastDeviceMessage < 30000);

    el.textContent = online ? "ONLINE" : "OFFLINE";
    el.className = online ? "online" : "offline";
}

function connect() {
    const scheme = location.protocol === "https:" ? "wss://" : "ws://";
    socket = new WebSocket(scheme + location.host + "/ws/dashboard");

    socket.onopen = () => {
        reconnectDelay = 1000;
        document.getElementById("connection").textContent = "CONNECTED";
        document.getElementById("connection").className = "online";
    };

    socket.onmessage = (event) => {
        try {
            const message = JSON.parse(event.data);
            if (message.data) showData(message.data);
        } catch (e) {
            console.error("Invalid dashboard message", e);
        }
    };

    socket.onclose = () => {
        document.getElementById("connection").textContent = "DISCONNECTED";
        document.getElementById("connection").className = "offline";
        setTimeout(connect, reconnectDelay);
        reconnectDelay = Math.min(reconnectDelay * 2, 15000);
    };

    socket.onerror = () => socket.close();
}

setInterval(updateDeviceStatus, 1000);
connect();
</script>
</body>
</html>
"""

