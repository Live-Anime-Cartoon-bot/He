import os
import json
import gzip
import re
import requests
import base64
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from telegram.constants import ParseMode

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
BOT_OWNER_ID = os.environ.get("BOT_OWNER_ID")
DATA_FOLDER = "app/assets/data"
USER_DATA_FILE = "user_data.json"
ADMINS_FILE = "admins.json"
CHANNEL_DATA_URL = base64.b64decode("aHR0cHM6Ly9taXR0aHU3ODYuZ2l0aHViLmlvL3R2ZXBnL2ppb3R2L2ppb2RhdGEuanNvbg==").decode()
IST = ZoneInfo("Asia/Kolkata")

_channel_cache = None

def _load_json(path, default=None):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return default if default is not None else {}

def _save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def get_user_data():
    return _load_json(USER_DATA_FILE, {})

def save_user_data(data):
    _save_json(USER_DATA_FILE, data)

def get_admins():
    return _load_json(ADMINS_FILE, {})

def save_admins(data):
    _save_json(ADMINS_FILE, data)

def is_owner(user_id):
    if not BOT_OWNER_ID:
        return False
    return str(user_id) == str(BOT_OWNER_ID)

def is_admin(user_id):
    admins = get_admins()
    return str(user_id) in admins

def get_user_role(user_id):
    if is_owner(user_id):
        return "owner"
    if is_admin(user_id):
        return "admin"
    return "user"

# ── Credential helpers ─────────────────────────

def load_credentials():
    creds_path = os.path.join(DATA_FOLDER, "creds.jtv")
    key_path = os.path.join(DATA_FOLDER, "credskey.jtv")
    if not os.path.exists(creds_path) or not os.path.exists(key_path):
        return None
    with open(key_path, "r") as f:
        key = int(f.read().strip())
    with open(creds_path, "r") as f:
        enc = f.read().strip()
    decoded = base64.b64decode(enc).decode("latin-1")
    decrypted = "".join(chr(ord(c) - key) for c in decoded)
    return json.loads(decrypted)

def save_credentials(jio_data, mobile):
    u_name = encrypt_data(mobile, "TS-JIOTV")
    os.makedirs(DATA_FOLDER, exist_ok=True)
    with open(os.path.join(DATA_FOLDER, "creds.jtv"), "w") as f:
        f.write(encrypt_data(json.dumps(jio_data), u_name))
    with open(os.path.join(DATA_FOLDER, "credskey.jtv"), "w") as f:
        f.write(u_name)

def encrypt_data(data, key):
    key = int(key)
    enc = "".join(chr(ord(c) + key) for c in data)
    return base64.b64encode(enc.encode("latin-1")).decode()

# ── JioTV API helpers ──────────────────────────

def get_channels():
    global _channel_cache
    if _channel_cache:
        return _channel_cache
    resp = requests.get(CHANNEL_DATA_URL, timeout=10)
    resp.raise_for_status()
    _channel_cache = resp.json()
    return _channel_cache

def find_channel(name_query):
    channels = get_channels()
    q = name_query.lower().strip()
    for c in channels:
        if c["channel_name"].lower() == q:
            return c
    for c in channels:
        if q in c["channel_name"].lower():
            return c
    return None

def get_epg(channel_id, offset=0):
    url = f"https://jiotvapi.cdn.jio.com/apis/v1.3/getepg/get?offset={offset}&channel_id={channel_id}&langId=6"
    headers = {"user-agent": "okhttp/4.12.13", "Accept-Encoding": "gzip"}
    resp = requests.get(url, headers=headers, timeout=10)
    if resp.status_code == 200:
        try:
            data = gzip.decompress(resp.content)
            return json.loads(data)
        except Exception:
            try:
                return resp.json()
            except Exception:
                return None
    return None

def parse_time(time_str):
    time_str = time_str.strip().upper()
    for fmt in ["%I:%M%p", "%I:%M %p", "%H:%M"]:
        try:
            t = datetime.strptime(time_str, fmt)
            return t.hour, t.minute
        except ValueError:
            continue
    return None, None

def find_program_in_epg(channel_id, start_h, start_m, end_h, end_m):
    for offset in [0, -1, 1]:
        epg = get_epg(channel_id, offset)
        if not epg:
            continue
        for program in epg.get("epg", []):
            try:
                start_ts = int(program.get("startEpoch", 0))
                end_ts = int(program.get("endEpoch", 0))
                p_start = datetime.fromtimestamp(start_ts, tz=IST)
                p_end = datetime.fromtimestamp(end_ts, tz=IST)
                if p_start.hour == start_h and p_start.minute == start_m:
                    return program, p_start, p_end
                if p_end.hour == end_h and p_end.minute == end_m:
                    return program, p_start, p_end
            except Exception:
                continue
    return None, None, None

def jio_headers_from_creds(creds):
    return {
        "appname": "RJIL_JioTV",
        "os": "android",
        "devicetype": "phone",
        "content-type": "application/json",
        "user-agent": "okhttp/3.14.9"
    }

def send_jio_otp_api(mobile):
    url = "https://jiotvapi.media.jio.com/userservice/apis/v1/loginotp/send"
    headers = {
        "appname": "RJIL_JioTV",
        "os": "android",
        "devicetype": "phone",
        "content-type": "application/json",
        "user-agent": "okhttp/3.14.9"
    }
    payload = {"number": base64.b64encode(f"+91{mobile}".encode()).decode()}
    resp = requests.post(url, json=payload, headers=headers, timeout=10)
    if resp.status_code == 204:
        return {"status": "success", "message": "OTP sent successfully"}
    try:
        data = resp.json()
        return {"status": "error", "message": data.get("message", f"Error code {resp.status_code}")}
    except Exception:
        return {"status": "error", "message": f"Unknown error: {resp.status_code}"}

def verify_jio_otp_api(mobile, otp):
    url = "https://jiotvapi.media.jio.com/userservice/apis/v1/loginotp/verify"
    headers = {
        "appname": "RJIL_JioTV",
        "os": "android",
        "devicetype": "phone",
        "content-type": "application/json",
        "user-agent": "okhttp/3.14.9"
    }
    payload = {
        "number": base64.b64encode(f"+91{mobile}".encode()).decode(),
        "otp": otp,
        "deviceInfo": {
            "consumptionDeviceName": "RMX1945",
            "info": {
                "type": "android",
                "platform": {"name": "RMX1945"},
                "androidId": "tsjiotvbot123456"
            }
        }
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=10)
    try:
        data = resp.json()
    except Exception:
        return {"status": "error", "message": f"Parse error: {resp.status_code}"}
    if data.get("ssoToken"):
        save_credentials(data, mobile)
        return {"status": "success", "message": "Login successful!"}
    msg = data.get("message", "")
    if not msg and "errors" in data and data["errors"]:
        msg = data["errors"][-1].get("message", "")
    return {"status": "error", "message": msg or f"Verify failed: {resp.status_code}"}

# ── Stream URL builders ────────────────────────

def get_stream_url(channel_id, creds):
    access_token = creds.get("authToken", "")
    crm = creds.get("sessionAttributes", {}).get("user", {}).get("subscriberId", "")
    unique_id = creds.get("sessionAttributes", {}).get("user", {}).get("unique", "")
    device_id = creds.get("deviceId", "")
    post_data = f"stream_type=Seek&channel_id={channel_id}"
    headers = {
        "Host": "jiotvapi.media.jio.com",
        "Content-Type": "application/x-www-form-urlencoded",
        "appkey": "NzNiMDhlYzQyNjJm",
        "channel_id": str(channel_id),
        "userid": crm,
        "crmid": crm,
        "deviceId": device_id,
        "devicetype": "phone",
        "isott": "true",
        "languageId": "6",
        "lbcookie": "1",
        "os": "android",
        "dm": "Xiaomi 22101316UP",
        "osversion": "14",
        "srno": "250918144000",
        "accesstoken": access_token,
        "subscriberid": crm,
        "uniqueId": unique_id,
        "usergroup": "tvYR7NSNn7rymo3F",
        "User-Agent": "okhttp/4.12.13",
        "versionCode": "452",
    }
    resp = requests.post(
        "https://jiotvapi.media.jio.com/playback/apis/v1/geturl?langId=6",
        data=post_data, headers=headers, timeout=10
    )
    data = resp.json()
    if data.get("code") == 200:
        return data.get("result")
    return None

def get_catchup_url(channel_id, srno, begin, end, creds):
    access_token = creds.get("authToken", "")
    crm = creds.get("sessionAttributes", {}).get("user", {}).get("subscriberId", "")
    unique_id = creds.get("sessionAttributes", {}).get("user", {}).get("unique", "")
    device_id = creds.get("deviceId", "")
    post_data = f"stream_type=Catchup&channel_id={channel_id}&programId={srno}&showtime=000000&srno={srno}&begin={begin}&end={end}"
    headers = {
        "Host": "jiotvapi.media.jio.com",
        "Content-Type": "application/x-www-form-urlencoded",
        "appkey": "NzNiMDhlYzQyNjJm",
        "channel_id": str(channel_id),
        "userid": crm,
        "crmid": crm,
        "deviceId": device_id,
        "devicetype": "phone",
        "isott": "true",
        "languageId": "6",
        "lbcookie": "1",
        "os": "android",
        "dm": "Xiaomi 22101316UP",
        "osversion": "14",
        "srno": str(srno),
        "accesstoken": access_token,
        "subscriberid": crm,
        "uniqueId": unique_id,
        "usergroup": "tvYR7NSNn7rymo3F",
        "User-Agent": "okhttp/4.12.13",
        "versionCode": "452",
    }
    resp = requests.post(
        "https://jiotvapi.media.jio.com/playback/apis/v1/geturl?langId=6",
        data=post_data, headers=headers, timeout=10
    )
    data = resp.json()
    if data.get("code") == 200:
        return data.get("result")
    return None

# ── Role decorators ────────────────────────────

def owner_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if not is_owner(uid):
            await update.message.reply_text("❌ Sirf Owner yeh command use kar sakta hai.")
            return
        return await func(update, context)
    return wrapper

def owner_admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if not is_owner(uid) and not is_admin(uid):
            await update.message.reply_text("❌ Sirf Owner ya Admin yeh command use kar sakte hain.")
            return
        return await func(update, context)
    return wrapper

def require_login(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not load_credentials():
            await update.message.reply_text(
                "❌ JioTV login nahi hai.\nPehle `/login <mobile>` se OTP verify karo.",
                parse_mode=ParseMode.MARKDOWN
            )
            return
        return await func(update, context)
    return wrapper

# ── Bot commands ───────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    role = get_user_role(user.id)
    role_icon = {"owner": "👑", "admin": "👨\u200d✈\ufe0f", "user": "👤"}[role]

    text = (
        f"{role_icon} *JioTV+ ReBorn Bot*\n"
        f"Role: `{role.upper()}` | User: `{user.first_name}`\n\n"
        "*Commands:*\n"
        "📺 `/live <channel>` — Live stream link\n"
        "📼 `/rec <channel> -t HH:MMAM - HH:MMPM`\n"
        "📋 `/channels` — Channels list\n"
        "🔍 `/search <name>` — Channel search\n"
        "ℹ️ `/myinfo` — Apna info dekho\n"
    )
    if role == "owner":
        text += "\n*Owner Only:*\n"
        text += "🔑 `/login <mobile>` — OTP bhejo\n"
        text += "🔐 `/otp <code>` — OTP verify karo\n"
    if role in ("owner", "admin"):
        text += "📢 `/broadcast <msg>` — Sabko message bhejo\n"
    if role == "owner":
        text += (
            "\n*Owner Commands:*\n"
            "🔢 `/addadmin <user_id>` — Admin add\n"
            "🗑 `/removeadmin <user_id>` — Admin remove\n"
            "👥 `/adminlist` — Admin list\n"
            "🌐 `/proxy` — Proxy URL (hidden)\n"
            "💾 `/setowner <user_id>` — Owner set\n"
        )
    text += (
        "\n*Example:*\n"
        "`/rec Pogo -t 12:00PM - 01:00PM`\n"
        "`/live Star Sports 1`"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def myinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    role = get_user_role(user.id)
    creds = load_credentials()

    jio_mobile = "❌ Not logged in"
    expiry = "N/A"
    if creds:
        try:
            mobile = creds.get("sessionAttributes", {}).get("user", {}).get("mobile", "")
            name = creds.get("sessionAttributes", {}).get("user", {}).get("commonName", "")
            jio_mobile = f"{name} ({mobile})"
            jwt = creds.get("authToken", "")
            if jwt:
                parts = jwt.split(".")
                if len(parts) > 1:
                    payload = json.loads(base64.b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
                    exp = payload.get("exp", 0)
                    exp_dt = datetime.fromtimestamp(exp, tz=IST)
                    expiry = exp_dt.strftime("%d-%b-%Y %I:%M %p")
        except Exception:
            pass

    text = (
        f"👤 *User Info*\n"
        f"Name: `{user.first_name}`\n"
        f"ID: `{user.id}`\n"
        f"Role: `{role.upper()}`\n"
        f"Username: @{user.username or 'N/A'}\n\n"
        f"📱 *JioTV Status*\n"
        f"Mobile: `{jio_mobile}`\n"
        f"Token Expiry: `{expiry}`"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


@owner_only
async def login_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usage: `/login <10-digit mobile>`", parse_mode=ParseMode.MARKDOWN)
        return

    mobile = context.args[0].strip()
    if not re.match(r"^\d{10}$", mobile):
        await update.message.reply_text("❌ 10-digit mobile number daliye. Example: `/login 9876543210`", parse_mode=ParseMode.MARKDOWN)
        return

    msg = await update.message.reply_text(f"🔑 *{mobile}* pe OTP bhej rahe hain...", parse_mode=ParseMode.MARKDOWN)

    result = send_jio_otp_api(mobile)

    if result["status"] == "success":
        user_data = get_user_data()
        user_data[str(update.effective_user.id)] = {
            "mobile": mobile,
            "pending": True,
            "login_time": datetime.now(IST).isoformat()
        }
        save_user_data(user_data)
        await msg.edit_text(
            f"✅ OTP *{mobile}* pe bhej diya!\n"
            f"Ab `/otp <6-digit code>` se verify karo.",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await msg.edit_text(f"❌ OTP fail: {result['message']}")


@owner_only
async def otp_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usage: `/otp <6-digit code>`", parse_mode=ParseMode.MARKDOWN)
        return

    otp = context.args[0].strip()
    if not re.match(r"^\d{6}$", otp):
        await update.message.reply_text("❌ 6-digit OTP daliye. Example: `/otp 123456`", parse_mode=ParseMode.MARKDOWN)
        return

    user_data = get_user_data()
    user_entry = user_data.get(str(update.effective_user.id))
    if not user_entry or not user_entry.get("pending"):
        await update.message.reply_text("❌ Pehle `/login <mobile>` se OTP request karo.", parse_mode=ParseMode.MARKDOWN)
        return

    mobile = user_entry["mobile"]
    msg = await update.message.reply_text("🔐 OTP verify ho raha hai...")

    result = verify_jio_otp_api(mobile, otp)

    if result["status"] == "success":
        user_entry["pending"] = False
        user_entry["verified"] = True
        save_user_data(user_data)
        await msg.edit_text("✅ *JioTV Login Successful!*\nAb `/live` ya `/rec` commands use kar sakte ho.", parse_mode=ParseMode.MARKDOWN)
    else:
        await msg.edit_text(f"❌ Verify fail: {result['message']}\nDobara try karo: `/otp <code>`", parse_mode=ParseMode.MARKDOWN)


@require_login
async def live_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usage: `/live <channel name>`", parse_mode=ParseMode.MARKDOWN)
        return

    channel_name = " ".join(context.args)
    msg = await update.message.reply_text(f"🔍 *{channel_name}* dhundh raha hoon...", parse_mode=ParseMode.MARKDOWN)

    creds = load_credentials()
    channel = find_channel(channel_name)
    if not channel:
        await msg.edit_text(f"❌ Channel *{channel_name}* nahi mila.\n`/search {channel_name}` try karo.", parse_mode=ParseMode.MARKDOWN)
        return

    stream_url = get_stream_url(channel["channel_id"], creds)
    if not stream_url:
        await msg.edit_text("❌ Stream URL nahi mili. Token expire ho sakta hai.")
        return

    text = (
        f"📺 *{channel['channel_name']}* — Live Stream\n\n"
        f"`{stream_url}`\n\n"
        f"🎬 VLC ya kisi bhi player mein paste karo."
    )
    await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)


@require_login
async def rec_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    full_text = " ".join(context.args)

    match = re.match(
        r"^(.+?)\s+-t\s+(\d{1,2}:\d{2}\s*[APap][Mm])\s*-\s*(\d{1,2}:\d{2}\s*[APap][Mm])$",
        full_text.strip()
    )
    if not match:
        await update.message.reply_text(
            "❌ *Format sahi nahi hai.*\n\n"
            "Sahi format:\n`/rec <channel> -t HH:MMAM - HH:MMPM`\n\n"
            "Example:\n`/rec Pogo -t 12:00PM - 01:00PM`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    channel_name = match.group(1).strip()
    start_time_str = match.group(2).strip()
    end_time_str = match.group(3).strip()

    start_h, start_m = parse_time(start_time_str)
    end_h, end_m = parse_time(end_time_str)

    if start_h is None or end_h is None:
        await update.message.reply_text("❌ Time format sahi nahi. Example: `12:00PM`", parse_mode=ParseMode.MARKDOWN)
        return

    msg = await update.message.reply_text(
        f"🔍 *{channel_name}* mein `{start_time_str} - {end_time_str}` ka program dhundh raha hoon...",
        parse_mode=ParseMode.MARKDOWN
    )

    creds = load_credentials()
    channel = find_channel(channel_name)
    if not channel:
        await msg.edit_text(f"❌ Channel *{channel_name}* nahi mila.\n`/search {channel_name}` try karo.", parse_mode=ParseMode.MARKDOWN)
        return

    channel_id = channel["channel_id"]

    if channel.get("isCatchupAvailable") != "True":
        await msg.edit_text(f"❌ *{channel['channel_name']}* par catchup available nahi hai.", parse_mode=ParseMode.MARKDOWN)
        return

    program, p_start, p_end = find_program_in_epg(channel_id, start_h, start_m, end_h, end_m)

    if not program:
        await msg.edit_text(
            f"❌ *{channel['channel_name']}* par `{start_time_str}` ka koi program nahi mila.\n\n"
            f"EPG mein woh show nahi hai ya time galat hai.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    srno = program.get("srno") or program.get("programId", "")
    begin = program.get("startEpoch", "")
    end_epoch = program.get("endEpoch", "")
    show_name = program.get("showName", "Unknown Show")
    show_desc = program.get("showDesc", "")

    stream_url = get_catchup_url(channel_id, srno, begin, end_epoch, creds)
    if not stream_url:
        await msg.edit_text("❌ Catchup stream URL nahi mili. Token ya program issue ho sakta hai.")
        return

    time_range = ""
    if p_start and p_end:
        time_range = f"{p_start.strftime('%I:%M %p')} - {p_end.strftime('%I:%M %p')} IST"

    text = (
        f"📼 *{show_name}*\n"
        f"📺 Channel: {channel['channel_name']}\n"
        f"🕐 Time: {time_range}\n"
    )
    if show_desc:
        text += f"📝 {show_desc[:150]}...\n" if len(show_desc) > 150 else f"📝 {show_desc}\n"

    text += f"\n🔗 Stream URL:\n`{stream_url}`\n\n🎬 VLC ya kisi bhi player mein paste karo."

    await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)


async def channels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("📋 Channels list load ho rahi hai...")
    try:
        channels = get_channels()
    except Exception:
        await msg.edit_text("❌ Channels load nahi ho sake. Baad mein try karo.")
        return

    categories = {}
    for ch in channels:
        cat = ch.get("channelCategoryId", "Other")
        categories.setdefault(cat, []).append(ch["channel_name"])

    keyboard = [[InlineKeyboardButton(cat, callback_data=f"cat_{cat}")] for cat in sorted(categories.keys())]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await msg.edit_text(
        f"📺 *JioTV Channels* ({len(channels)} total)\n\nCategory choose karo:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )


async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    cat = query.data.replace("cat_", "")
    channels = get_channels()
    cat_channels = [c for c in channels if c.get("channelCategoryId") == cat]

    lines = [f"📺 *{cat}* ({len(cat_channels)} channels)\n"]
    for ch in cat_channels:
        catchup = " 📼" if ch.get("isCatchupAvailable") == "True" else ""
        lines.append(f"• {ch['channel_name']}{catchup}")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n..."

    keyboard = [[InlineKeyboardButton("« Back", callback_data="back_categories")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)


async def back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    channels = get_channels()
    categories = {}
    for ch in channels:
        cat = ch.get("channelCategoryId", "Other")
        categories.setdefault(cat, []).append(ch["channel_name"])

    keyboard = [[InlineKeyboardButton(cat, callback_data=f"cat_{cat}")] for cat in sorted(categories.keys())]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        f"📺 *JioTV Channels* ({len(channels)} total)\n\nCategory choose karo:",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )


async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usage: `/search <channel name>`", parse_mode=ParseMode.MARKDOWN)
        return

    query = " ".join(context.args).lower()
    channels = get_channels()
    results = [c for c in channels if query in c["channel_name"].lower()]

    if not results:
        await update.message.reply_text(f"❌ `{query}` se koi channel nahi mila.", parse_mode=ParseMode.MARKDOWN)
        return

    lines = [f"🔍 *Search: {query}* ({len(results)} results)\n"]
    for ch in results[:20]:
        catchup = " 📼" if ch.get("isCatchupAvailable") == "True" else ""
        lines.append(f"• `{ch['channel_name']}`{catchup}")

    if len(results) > 20:
        lines.append(f"\n...aur {len(results) - 20} aur channels hain.")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ── Owner-only commands ────────────────────────

@owner_only
async def setowner_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usage: `/setowner <user_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    new_owner = context.args[0].strip()
    os.environ["BOT_OWNER_ID"] = new_owner
    await update.message.reply_text(f"✅ Owner set to `{new_owner}`", parse_mode=ParseMode.MARKDOWN)


@owner_only
async def addadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usage: `/addadmin <user_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    uid = context.args[0].strip()
    admins = get_admins()
    admins[uid] = {"added_by": update.effective_user.id, "time": datetime.now(IST).isoformat()}
    save_admins(admins)
    await update.message.reply_text(f"✅ Admin added: `{uid}`", parse_mode=ParseMode.MARKDOWN)


@owner_only
async def removeadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usage: `/removeadmin <user_id>`", parse_mode=ParseMode.MARKDOWN)
        return
    uid = context.args[0].strip()
    admins = get_admins()
    if uid in admins:
        del admins[uid]
        save_admins(admins)
        await update.message.reply_text(f"✅ Admin removed: `{uid}`", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"❌ `{uid}` admin list mein nahi hai.", parse_mode=ParseMode.MARKDOWN)


@owner_only
async def adminlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admins = get_admins()
    if not admins:
        await update.message.reply_text("📌 Koi admin nahi hai.")
        return
    lines = ["👥 *Admin List*\n"]
    for uid, info in admins.items():
        lines.append(f"• `{uid}` (Added: {info.get('time', 'N/A')[:10]})")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


@owner_only
async def proxy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    proxy = os.environ.get("JIOTV_PROXY_URL", "only_owner")
    await update.message.reply_text(f"🌐 Proxy URL: `{proxy}`\n\n(Sirf owner ko visible)", parse_mode=ParseMode.MARKDOWN)


# ── Owner + Admin commands ─────────────────────

@owner_admin_only
async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Usage: `/broadcast <message>`", parse_mode=ParseMode.MARKDOWN)
        return
    message = " ".join(context.args)
    user_data = get_user_data()
    sent = 0
    failed = 0
    for uid in user_data:
        try:
            await context.bot.send_message(chat_id=uid, text=f"📢 *Broadcast*\n\n{message}", parse_mode=ParseMode.MARKDOWN)
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(f"✅ Broadcast sent: {sent} users\n❌ Failed: {failed} users")


# ── Main ───────────────────────────────────────

def main():
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN set nahi hai!")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("login", login_cmd))
    app.add_handler(CommandHandler("otp", otp_cmd))
    app.add_handler(CommandHandler("live", live_cmd))
    app.add_handler(CommandHandler("rec", rec_cmd))
    app.add_handler(CommandHandler("channels", channels_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("myinfo", myinfo))

    # Owner + Admin
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))

    # Owner only
    app.add_handler(CommandHandler("setowner", setowner_cmd))
    app.add_handler(CommandHandler("addadmin", addadmin_cmd))
    app.add_handler(CommandHandler("removeadmin", removeadmin_cmd))
    app.add_handler(CommandHandler("adminlist", adminlist_cmd))
    app.add_handler(CommandHandler("proxy", proxy_cmd))

    app.add_handler(CallbackQueryHandler(category_callback, pattern="^cat_"))
    app.add_handler(CallbackQueryHandler(back_callback, pattern="^back_"))

    logger.info("JioTV Telegram Bot start ho raha hai...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
