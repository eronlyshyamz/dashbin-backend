import os
import json
import asyncio
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from pathlib import Path

app = FastAPI(title="Smart Dustbin Dashboard")

DEVICE_API_KEY = os.environ.get("DEVICE_API_KEY", "")
OFFLINE_AFTER_SECONDS = int(os.environ.get("OFFLINE_AFTER_SECONDS", "30"))

ASSETS_DIR = Path(__file__).parent / "assets"
ASSETS_DIR.mkdir(exist_ok=True)

latest_data = {
    "fill_level": 0.0,
    "full_alert": False,
    "lid_open": False,
    "buzzer_on": False,
    "last_seen": None,
    "online": False,
}

dashboard_clients = set()
state_lock = asyncio.Lock()


def utc_now():
    return datetime.now(timezone.utc)


def device_is_online():
    last_seen = latest_data.get("last_seen")
    if not last_seen:
        return False
    return (utc_now() - last_seen).total_seconds() <= OFFLINE_AFTER_SECONDS


def public_state():
    return {
        "fill_level": latest_data["fill_level"],
        "full_alert": latest_data["full_alert"],
        "lid_open": latest_data["lid_open"],
        "buzzer_on": latest_data["buzzer_on"],
        "last_seen": latest_data["last_seen"].isoformat() if latest_data["last_seen"] else None,
        "online": device_is_online(),
    }


async def broadcast_state():
    if not dashboard_clients:
        return

    message = json.dumps({"type": "status", "data": public_state()})
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
async def api_status():
    return public_state()


@app.get("/")
async def dashboard():
    return HTMLResponse(DASHBOARD_HTML)


@app.get("/assets/{filename}")
async def get_asset(filename: str):
    # Only serve the two image files used by this dashboard.
    if filename not in {"college.png", "photo.png"}:
        return JSONResponse({"error": "Not found"}, status_code=404)

    file_path = ASSETS_DIR / filename
    if not file_path.is_file():
        return JSONResponse(
            {"error": f"{filename} is missing. Upload it into the assets folder in GitHub."},
            status_code=404,
        )

    return FileResponse(file_path)


@app.websocket("/ws/device")
async def device_socket(websocket: WebSocket):
    await websocket.accept()

    try:
        auth_message = await websocket.receive_json()

        if (
            not isinstance(auth_message, dict)
            or auth_message.get("type") != "auth"
            or not DEVICE_API_KEY
            or auth_message.get("key") != DEVICE_API_KEY
        ):
            await websocket.send_json({"type": "auth", "ok": False})
            await websocket.close(code=1008)
            return

        await websocket.send_json({"type": "auth", "ok": True})

        async with state_lock:
            latest_data["last_seen"] = utc_now()
            await broadcast_state()

        while True:
            message = await websocket.receive_json()

            if not isinstance(message, dict):
                continue

            message_type = message.get("type")

            if message_type == "heartbeat":
                async with state_lock:
                    latest_data["last_seen"] = utc_now()
                    await broadcast_state()
                continue

            if message_type == "data":
                try:
                    fill = float(message.get("fill_level", 0))
                except (TypeError, ValueError):
                    continue

                fill = max(0.0, min(100.0, fill))

                async with state_lock:
                    latest_data["fill_level"] = round(fill, 1)
                    latest_data["full_alert"] = bool(message.get("full_alert", False))
                    latest_data["lid_open"] = bool(message.get("lid_open", False))
                    latest_data["buzzer_on"] = bool(message.get("buzzer_on", False))
                    latest_data["last_seen"] = utc_now()
                    await broadcast_state()

                await websocket.send_json({"type": "ack", "ok": True})

    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass


@app.websocket("/ws/dashboard")
async def dashboard_socket(websocket: WebSocket):
    await websocket.accept()
    dashboard_clients.add(websocket)

    try:
        await websocket.send_text(
            json.dumps({"type": "status", "data": public_state()})
        )

        while True:
            # The browser does not need to send data; this waits for disconnect.
            await websocket.receive_text()

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        dashboard_clients.discard(websocket)


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Smart Dustbin | Diamond Group</title>
<style>
:root {
  --ink: #101522;
  --muted: #657084;
  --green: #39e66b;
  --red: #ff3548;
  --paper: #f4f7fb;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 22px 14px 34px;
  color: var(--ink);
  background:
    radial-gradient(circle at 10% 0%, #e3efff 0, transparent 32%),
    linear-gradient(180deg, #f7faff, #edf1f7);
  font-family: Arial, Helvetica, sans-serif;
}
.page { width: min(940px, 100%); margin: 0 auto; }
.college-banner {
  width: 100%;
  display: block;
  max-height: 220px;
  object-fit: cover;
  object-position: center;
  border-radius: 15px;
  background: #dce8f5;
  box-shadow: 0 8px 24px rgba(28, 48, 80, .10);
}
.group-title {
  text-align: center;
  margin: 16px 0 8px;
  font-size: clamp(27px, 5vw, 42px);
  font-weight: 900;
  letter-spacing: -.8px;
  color: #101010;
}
.group-names {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px 26px;
  margin: 0 0 28px;
  font-size: clamp(14px, 2.5vw, 20px);
  font-weight: 800;
  color: #20242c;
}
.group-names span::before { content: "• "; }
.monitor {
  border: 7px solid #101010;
  border-radius: 27px;
  background: #fff;
  min-height: 535px;
  padding: 30px 26px 24px;
  display: grid;
  grid-template-columns: 1fr 1.4fr 1fr;
  grid-template-rows: 1fr auto;
  align-items: center;
  gap: 18px;
  box-shadow: 0 14px 32px rgba(22, 34, 54, .12);
}
.gauge-column {
  grid-column: 2;
  grid-row: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}
.gauge {
  --level: 0%;
  --fill-color: #39e66b;
  width: min(290px, 58vw);
  aspect-ratio: 1;
  border-radius: 50%;
  position: relative;
  overflow: hidden;
  background: radial-gradient(circle at 50% 34%, #1b1d22, #050607 70%);
  box-shadow: inset 0 0 0 9px #16191e, inset 0 0 28px #000,
              0 12px 25px rgba(0,0,0,.18);
  isolation: isolate;
}
.gauge::before {
  content: "";
  position: absolute;
  inset: 9px;
  border: 1px solid rgba(255,255,255,.14);
  border-radius: 50%;
  z-index: 3;
  pointer-events: none;
}
.gauge-ticks {
  position: absolute;
  inset: 17px;
  border-radius: 50%;
  border: 2px dashed rgba(255,255,255,.27);
  z-index: 3;
  pointer-events: none;
}
.liquid {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: var(--level);
  background: linear-gradient(0deg, var(--fill-color) 0%, color-mix(in srgb, var(--fill-color) 72%, white) 72%, rgba(255,255,255,.78) 100%);
  opacity: .98;
  transition: height 1.7s cubic-bezier(.2,.7,.2,1), background 1s ease;
  z-index: 1;
}
.liquid::before {
  content: "";
  position: absolute;
  left: -10%;
  top: -13px;
  width: 120%;
  height: 26px;
  border-radius: 50%;
  background: rgba(255,255,255,.38);
  filter: blur(7px);
}
.gauge-copy {
  position: absolute;
  inset: 0;
  z-index: 4;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  color: #fff;
  text-shadow: 0 2px 8px rgba(0,0,0,.35);
}
.gauge-kicker {
  font-size: clamp(13px, 2vw, 19px);
  font-weight: 800;
  letter-spacing: .4px;
  margin-bottom: 2px;
}
.gauge-value {
  font-size: clamp(66px, 12vw, 112px);
  line-height: .98;
  font-weight: 300;
  letter-spacing: -7px;
}
.gauge-value small { font-size: .28em; vertical-align: top; letter-spacing: 0; }
.gauge-caption {
  margin-top: 17px;
  text-align: center;
  font-size: clamp(18px, 2.5vw, 25px);
  font-weight: 900;
}
.status-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  min-width: 0;
}
.status-card.left { grid-column: 1; grid-row: 1; }
.status-card.right { grid-column: 3; grid-row: 1; }
.status-title {
  font-size: clamp(16px, 2.3vw, 23px);
  font-weight: 900;
  text-align: center;
}
.switch {
  width: 150px;
  max-width: 100%;
  height: 72px;
  border-radius: 50px;
  background: #d92639;
  box-shadow: inset 0 4px 10px rgba(0,0,0,.2), 0 5px 9px rgba(0,0,0,.12);
  position: relative;
  transition: background .45s ease;
}
.switch.on { background: #20bd61; }
.switch-knob {
  position: absolute;
  width: 62px;
  height: 62px;
  top: 5px;
  left: 5px;
  border-radius: 50%;
  background: linear-gradient(145deg, #fff, #dce2e9);
  box-shadow: 0 3px 9px rgba(0,0,0,.25);
  transition: transform .45s cubic-bezier(.2,.8,.2,1);
}
.switch.on .switch-knob { transform: translateX(78px); }
.switch-label {
  position: absolute;
  right: 17px;
  top: 24px;
  color: white;
  font-weight: 900;
  font-size: 17px;
}
.switch.on .switch-label { left: 18px; right: auto; }
.siren-wrap {
  width: 112px;
  height: 112px;
  display: grid;
  place-items: center;
  border-radius: 25px;
  background: #edf1f6;
  transition: background .35s ease, box-shadow .35s ease;
}
.siren-wrap svg { width: 90px; height: 90px; }
.siren-light { fill: #9da7b5; transition: fill .3s ease; }
.siren-rays { stroke: #8995a5; transition: stroke .3s ease; }
.siren-wrap.active {
  background: #fff0f1;
  box-shadow: 0 0 0 5px rgba(255,53,72,.13);
  animation: alertPulse .8s infinite alternate;
}
.siren-wrap.active .siren-light { fill: #ff263d; }
.siren-wrap.active .siren-rays { stroke: #ff263d; }
@keyframes alertPulse {
  from { box-shadow: 0 0 0 3px rgba(255,53,72,.10); }
  to { box-shadow: 0 0 0 11px rgba(255,53,72,.22); }
}
.alert-text {
  font-size: 14px;
  font-weight: 900;
  color: #687386;
  text-align: center;
}
.alert-text.active { color: #e51e36; }
.connection-row {
  grid-column: 1 / -1;
  grid-row: 2;
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 9px;
  color: #687386;
  font-size: 13px;
  font-weight: 800;
  padding-top: 12px;
  border-top: 1px solid #edf0f4;
}
.dot { width: 9px; height: 9px; border-radius: 50%; background: #e53d4d; }
.dot.online { background: #1dbb68; box-shadow: 0 0 0 4px rgba(29,187,104,.13); }
.profile {
  display: grid;
  grid-template-columns: 220px 1fr;
  gap: 28px;
  align-items: center;
  margin: 34px 8px 0;
  padding: 8px 10px;
}
.profile-photo {
  width: 210px;
  height: 210px;
  border-radius: 50%;
  object-fit: cover;
  background: #dce8f5;
  border: 6px solid #fff;
  box-shadow: 0 8px 22px rgba(22,34,54,.14);
}
.profile-name {
  font-size: clamp(32px, 6vw, 58px);
  font-weight: 950;
  line-height: 1.05;
  letter-spacing: -1.5px;
  margin: 0 0 8px;
}
.profile-branch {
  font-size: clamp(20px, 3vw, 30px);
  font-weight: 800;
  margin: 0 0 17px;
}
.profile-details {
  font-size: clamp(15px, 2.1vw, 20px);
  line-height: 1.55;
  font-weight: 800;
  color: #202633;
}
.footer-note {
  text-align: center;
  color: #8a94a4;
  font-size: 12px;
  margin-top: 26px;
}
@media (max-width: 650px) {
  body { padding: 12px 9px 25px; }
  .college-banner { max-height: 150px; border-radius: 10px; }
  .group-names { gap: 7px 14px; margin-bottom: 18px; }
  .monitor {
    min-height: 0;
    padding: 22px 12px 17px;
    border-width: 5px;
    border-radius: 20px;
    grid-template-columns: 1fr 1fr;
    grid-template-rows: auto auto auto;
    gap: 20px 8px;
  }
  .gauge-column { grid-column: 1 / -1; grid-row: 1; }
  .gauge { width: min(250px, 64vw); }
  .status-card.left { grid-column: 1; grid-row: 2; }
  .status-card.right { grid-column: 2; grid-row: 2; }
  .switch { width: 126px; height: 62px; }
  .switch-knob { width: 52px; height: 52px; }
  .switch.on .switch-knob { transform: translateX(64px); }
  .switch-label { top: 21px; right: 13px; font-size: 15px; }
  .switch.on .switch-label { left: 14px; }
  .siren-wrap { width: 94px; height: 94px; }
  .siren-wrap svg { width: 76px; height: 76px; }
  .connection-row { grid-column: 1 / -1; grid-row: 3; }
  .profile {
    grid-template-columns: 112px 1fr;
    gap: 14px;
    margin: 24px 0 0;
    padding: 0;
  }
  .profile-photo { width: 108px; height: 108px; border-width: 4px; }
  .profile-name { letter-spacing: -.8px; }
  .profile-details { font-size: 13px; }
}
</style>
</head>
<body>
<div class="page">
  <img class="college-banner" src="/assets/college.png" alt="College banner"
       onerror="this.style.display='none'">

  <h1 class="group-title">Diamond Group</h1>
  <div class="group-names">
    <span>Shayam Kumar</span>
    <span>Ankit Kumar</span>
    <span>Amar Kumar</span>
    <span>Sudhir Kumar</span>
  </div>

  <main class="monitor">
    <section class="gauge-column">
      <div class="gauge" id="gauge" style="--level:0%;--fill-color:#39e66b">
        <div class="gauge-ticks"></div>
        <div class="liquid" id="liquid"></div>
        <div class="gauge-copy">
          <div class="gauge-kicker">DUSTBIN FILL LEVEL</div>
          <div class="gauge-value"><span id="fillValue">0</span><small>%</small></div>
        </div>
      </div>
      <div class="gauge-caption">Dustbin Storage</div>
    </section>

    <section class="status-card left">
      <div class="switch" id="lidSwitch">
        <div class="switch-knob"></div>
        <span class="switch-label" id="lidSwitchLabel">OFF</span>
      </div>
      <div class="status-title">Lid Status</div>
      <div class="alert-text" id="lidText">CLOSED</div>
    </section>

    <section class="status-card right">
      <div class="siren-wrap" id="siren">
        <svg viewBox="0 0 100 100" aria-hidden="true">
          <path class="siren-rays" d="M20 19 L13 8 M50 12 L50 0 M80 19 L87 8"
                stroke-width="7" stroke-linecap="round" fill="none"/>
          <path d="M19 76 Q50 91 81 76 L75 88 Q50 99 25 88 Z" fill="#344154"/>
          <path d="M28 72 L34 48 Q50 29 66 48 L72 72 Z" fill="#e75b38"/>
          <path class="siren-light" d="M39 72 L41 48 Q50 36 59 48 L61 72 Z"/>
          <rect x="23" y="72" width="54" height="11" rx="5" fill="#536176"/>
          <ellipse cx="50" cy="72" rx="27" ry="8" fill="#263448"/>
          <ellipse cx="50" cy="49" rx="10" ry="5" fill="#ff9a79" opacity=".8"/>
        </svg>
      </div>
      <div class="status-title">Full Storage</div>
      <div class="alert-text" id="fullText">NORMAL</div>
    </section>

    <div class="connection-row">
      <span class="dot" id="connectionDot"></span>
      <span id="connectionText">CONNECTING TO DEVICE...</span>
    </div>
  </main>

  <section class="profile">
    <img class="profile-photo" src="/assets/photo.png" alt="Shayam Kumar"
         onerror="this.style.visibility='hidden'">
    <div>
      <h2 class="profile-name">SHAYAM KUMAR</h2>
      <p class="profile-branch">Civil Engineering</p>
      <div class="profile-details">
        Roll No. 24C43<br>
        Semester 5th<br>
        Session 2024–27
      </div>
    </div>
  </section>

  <div class="footer-note">SMART DUSTBIN PROJECT · DIAMOND GROUP</div>
</div>

<script>
const $ = (id) => document.getElementById(id);
let socket;
let reconnectDelay = 1500;
let latestLastSeen = null;

function setFill(value) {
  const fill = Math.max(0, Math.min(100, Number(value) || 0));
  const isFull = fill >= 70;
  $("fillValue").textContent = Math.round(fill);
  $("gauge").style.setProperty("--level", fill + "%");
  $("gauge").style.setProperty("--fill-color", isFull ? "#ff263d" : "#39e66b");
}

function setLid(open) {
  $("lidSwitch").classList.toggle("on", !!open);
  $("lidSwitchLabel").textContent = open ? "ON" : "OFF";
  $("lidText").textContent = open ? "OPEN" : "CLOSED";
}

function setFull(alertOn, buzzerOn) {
  const active = !!alertOn || !!buzzerOn;
  $("siren").classList.toggle("active", active);
  $("fullText").textContent = active ? "BIN FULL · ALERT ON" : "NORMAL";
  $("fullText").classList.toggle("active", active);
}

function setConnection(online) {
  $("connectionDot").classList.toggle("online", !!online);
  $("connectionText").textContent = online ? "DEVICE ONLINE" : "DEVICE OFFLINE";
}

function updateDashboard(data) {
  if (!data) return;
  setFill(data.fill_level);
  setLid(data.lid_open);
  setFull(data.full_alert, data.buzzer_on);
  latestLastSeen = data.last_seen ? new Date(data.last_seen) : null;
  setConnection(!!data.online);
}

function connect() {
  const scheme = location.protocol === "https:" ? "wss://" : "ws://";
  socket = new WebSocket(scheme + location.host + "/ws/dashboard");

  socket.onopen = () => {
    reconnectDelay = 1500;
    $("connectionText").textContent = "DASHBOARD CONNECTED · WAITING FOR DEVICE";
  };

  socket.onmessage = (event) => {
    try {
      const message = JSON.parse(event.data);
      if (message.type === "status") updateDashboard(message.data);
    } catch (error) {
      console.error("Invalid dashboard message", error);
    }
  };

  socket.onclose = () => {
    setConnection(false);
    $("connectionText").textContent = "RECONNECTING...";
    setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 1.7, 12000);
  };

  socket.onerror = () => socket.close();
}

setInterval(() => {
  if (!latestLastSeen) {
    setConnection(false);
    return;
  }
  const ageSeconds = (Date.now() - latestLastSeen.getTime()) / 1000;
  setConnection(ageSeconds <= 30);
}, 1000);

connect();
</script>
</body>
</html>"""
