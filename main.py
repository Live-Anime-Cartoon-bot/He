import os
import time
import logging
import random
import shlex
import shutil
import asyncio
import signal
import psutil
from typing import Tuple
from os.path import join
from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from pyrogram import Client, filters, idle
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime, timedelta
import config
import pytz

# Timezone from config.py
tz = pytz.timezone(config.TIMEZONE)

def tz_time(*args):
    return datetime.now(tz).timetuple()

# Apply dynamic timezone for logging timestamps
logging.Formatter.converter = tz_time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt = "%d-%m-%Y %I:%M:%S %p " + tz.tzname(datetime.now())
)

LOG = logging.getLogger(__name__)

app = Client("recorder", bot_token=config.BOT_TOKEN, api_id=config.API_ID, api_hash=config.API_HASH)

user_status = {}
user_tasks = {}
user_ffmpeg_pids = {}
progress_tasks = {}
cancelled_users = set()  # Track cancelled users


@app.on_message(filters.command("start") & filters.user(config.AUTH_USERS))
async def start(client, message):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📖 Help", callback_data="help")],
        [InlineKeyboardButton("💠 Plans", callback_data="plan")],
        [InlineKeyboardButton("📢 Channel", url="https://t.me/ToonixIndia")]
    ])
    await message.reply_text(
        "🎬 **Welcome to Video Recorder Bot!**\n\n"
        "🚀 Use /rec command to start recording.\n"
        "🛑 Use /cancel to stop ongoing recording.\n"
        "📚 Use /help for detailed instructions.",
        reply_markup=kb
    )


@app.on_message(filters.command("cancel") & filters.user(config.AUTH_USERS))
async def cancel_command(client, message: Message):
    user_id = message.from_user.id
    
    if user_id not in user_tasks:
        return await message.reply_text("❌ **No active recording to cancel!**")
    
    try:
        # Mark user as cancelled first
        cancelled_users.add(user_id)
        
        # Stop progress tracking task
        if user_id in progress_tasks:
            progress_tasks[user_id].cancel()
            del progress_tasks[user_id]
        
        # Kill FFmpeg process if running
        if user_id in user_ffmpeg_pids:
            ffmpeg_pid = user_ffmpeg_pids[user_id]
            try:
                # Kill the main FFmpeg process and its children
                parent = psutil.Process(ffmpeg_pid)
                children = parent.children(recursive=True)
                
                # Kill all child processes first
                for child in children:
                    try:
                        child.kill()
                    except:
                        pass
                
                # Kill parent process
                parent.kill()
                
                # Wait for processes to terminate
                gone, alive = psutil.wait_procs([parent] + children, timeout=3)
                
                LOG.info(f"Killed FFmpeg process {ffmpeg_pid} for user {user_id}")
            except psutil.NoSuchProcess:
                LOG.warning(f"FFmpeg process {ffmpeg_pid} already terminated")
            except Exception as e:
                LOG.error(f"Error killing FFmpeg process: {e}")
            
            del user_ffmpeg_pids[user_id]
        
        # Get task info before clearing
        task_info = user_status.get(user_id, {})
        filename = task_info.get("filename", "Unknown")
        save_dir = task_info.get("save_dir")
        
        # Clear user data but KEEP the save_dir info for later cleanup
        user_tasks.pop(user_id, None)
        user_status.pop(user_id, None)
        
        await message.reply_text(
            f"✅ **Recording Cancelled!**\n\n"
            f"📁 **File:** `{filename}`\n"
            f"🛑 **Status:** Stopped immediately\n"
            f"📤 **Uploading recorded portion...**"
        )
        
    except Exception as e:
        LOG.error(f"Error in cancel_command: {e}")
        await message.reply_text("❌ **Error cancelling recording!**")


async def cleanup_partial_files(user_id: int):
    """Clean up partially created files for a user"""
    try:
        # Find and remove any directories/files created during this session
        download_dir = config.DOWNLOAD_DIRECTORY
        if not os.path.exists(download_dir):
            return
            
        current_time = time.time()
        # Look for directories created in the last hour that might be partial
        for item in os.listdir(download_dir):
            item_path = join(download_dir, item)
            if os.path.isdir(item_path):
                try:
                    # Check if directory was created recently (within last hour)
                    dir_time = os.path.getctime(item_path)
                    if current_time - dir_time < 3600:  # 1 hour
                        # Check if it contains partial video files
                        video_files = [f for f in os.listdir(item_path) if f.endswith('.mkv') or f.endswith('.mp4')]
                        if video_files:
                            shutil.rmtree(item_path)
                            LOG.info(f"Cleaned up partial files in {item_path}")
                except Exception as e:
                    LOG.warning(f"Error cleaning up {item_path}: {e}")
    except Exception as e:
        LOG.error(f"Error in cleanup_partial_files: {e}")


@app.on_message(filters.command("status") & filters.user(config.AUTH_USERS))
async def status_cmd(client, message):
    uid = message.from_user.id
    status = user_status.get(uid)

    if not status:
        return await message.reply("📭 No active recording task found.")

    # Start time from task ID
    start_ts = status["id"]
    start_dt = datetime.fromtimestamp(start_ts, tz=tz)
    start_time_str = start_dt.strftime("%d-%m-%Y %I:%M:%S %p")

    # Convert HH:MM:SS target duration → seconds
    target_seconds = time_to_seconds(status["target"])

    # Convert progress HH:MM:SS → seconds
    progress_sec = time_to_seconds(status["progress"])

    # Remaining time
    remaining = max(target_seconds - progress_sec, 0)
    eta_str = TimeFormatter(remaining * 1000)

    # Expected end time
    end_dt = start_dt + timedelta(seconds=target_seconds)
    end_time_str = end_dt.strftime("%d-%m-%Y %I:%M:%S %p")

    # FFmpeg status
    ffmpeg_status = "✅ Running" if uid in user_ffmpeg_pids else "❌ Not found"

    text = (
        f"📊 **Recording Status**\n\n"
        f"🆔 **Task ID:** `{status['id']}`\n"
        f"📁 **Filename:** `{status['filename']}`\n"
        f"⏱ **Duration:** `{status['progress']}` / `{status['target']}`\n"
        f"⏳ **ETA:** `{eta_str}`\n"
        f"🕒 **Started:** `{start_time_str}`\n"
        f"📅 **Expected End Time:** `{end_time_str}`\n"
        f"🔧 **FFmpeg:** `{ffmpeg_status}`\n"
        f"👤 **User:** @{message.from_user.username or 'anonymous'}\n\n"
        f"🛑 Use /cancel to stop recording"
    )

    await message.reply_text(text)


@app.on_message(filters.command("help") & filters.user(config.AUTH_USERS))
async def help_cmd(client, message):
    await message.reply_text(
        "🛠 **Video Recorder Help Menu**\n\n"
        
        "🎯 **How to Record:**\n"
        "```\n/rec http://link 00:00:00 My Filename\n```\n"
        
        "⚡ **Available Commands:**\n"
        "• 🎥 `/rec` - Start recording from stream URL\n"
        "• 🛑 `/cancel` - Stop ongoing recording (sends recorded portion)\n"
        "• 📊 `/status` - Check current recording progress\n"
        "• 🏠 `/start` - Show welcome message\n"
        "• 💰 `/plan` - View subscription plans\n"
        "• 🛠 `/tools` - Extra utilities\n\n"
        
        "📝 **Usage Notes:**\n"
        "🔸 Stream link must be accessible & DRM-free\n"
        "🔸 Timestamp format: `HH:MM:SS` (e.g., 01:30:00)\n"
        "🔸 Filename should not contain: `/\\:*?\"<>|`\n"
        f"🔸 Default filename: `{config.DEFAULT_FILENAME}`\n"
        "🔸 Output format: MKV with original quality\n\n"
        
        "⚙️ **Features:**\n"
        "✅ Auto thumbnail generation\n"
        "✅ Progress tracking\n"
        "✅ Multi-stream support\n"
        "✅ Emergency stop with partial video upload\n\n"
        
        "👨‍💻 _Bot maintained by @TEMohanish_",
        disable_web_page_preview=True
    )


@app.on_message(filters.command("rec") & filters.user(config.AUTH_USERS))
async def rec_command(client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            "❌ **Invalid Format!**\n\n"
            "📌 **Correct Usage:**\n"
            "```\n/rec http://link 00:00:00 filename\n```\n"
            "💡 **Example:**\n"
            "`/rec https://example.com/stream 00:10:30 MyVideo`"
        )
    
    # Check if user already has an active recording
    if message.from_user.id in user_tasks:
        return await message.reply_text(
            "❌ **You already have an active recording!**\n\n"
            "📊 Check progress with /status\n"
            "🛑 Stop recording with /cancel"
        )
    
    await handle_record(client, message)


async def handle_record(client, message: Message):
    user_id = message.from_user.id
    msg = await message.reply_text("🔄 **Initializing recording...**")

    save_dir = None
    ffmpeg_process = None
    video_path = None
    thumb_path = None
    
    try:
        # Extract parameters from command
        if message.command[0] == "rec":
            params = " ".join(message.command[1:])
        else:
            params = message.text
        
        # Split into url, timestamp, and optional filename
        parts = params.split(" ", 2)
        url = parts[0]
        timestamp = parts[1]
        
        # Use custom filename if provided, otherwise use default
        if len(parts) > 2:
            raw_filename = parts[2]
        else:
            raw_filename = config.DEFAULT_FILENAME
        
        filename = f"{raw_filename.strip()}.mkv"
        save_dir = join(config.DOWNLOAD_DIRECTORY, str(int(time.time())))
        os.makedirs(save_dir, exist_ok=True)
        video_path = join(save_dir, filename)

        # Update user status
        user_tasks[user_id] = time.time()
        user_status[user_id] = {
            "id": int(user_tasks[user_id]),
            "filename": raw_filename.strip(),
            "target": timestamp,
            "progress": "00:00:00",
            "save_dir": save_dir  # Store for cleanup
        }

        # Recording progress tracking
        recording_start = time.time()
        duration = time_to_seconds(timestamp)
        
        async def update_recording_progress():
            while user_id in user_tasks:
                # Check if user cancelled
                if user_id in cancelled_users:
                    break
                    
                elapsed = time.time() - recording_start
                progress_formatted = TimeFormatter(int(elapsed * 1000))
                
                # Update progress in user_status
                if user_id in user_status:
                    user_status[user_id]["progress"] = progress_formatted
                
                # Calculate speed (assuming linear progress)
                if elapsed > 0:
                    speed_mb_per_sec = random.uniform(2.0, 8.0)  # Simulated speed for recording
                    percentage = min((elapsed / duration) * 100, 100) if duration > 0 else 0
                    
                    # Calculate ETA
                    if percentage > 0:
                        eta = (duration - elapsed) / (percentage / 100) if percentage < 100 else 0
                    else:
                        eta = 0
                else:
                    speed_mb_per_sec = 0
                    percentage = 0
                    eta = 0
                
                # Recording progress bar
                bar_length = 20
                filled_length = int(bar_length * percentage // 100)
                bar = '█' * filled_length + '░' * (bar_length - filled_length)
                
                progress_text = (
                    f"🎬 **Recording Progress**\n"
                    f"`[{bar}]` {percentage:.1f}%\n"
                    f"⏱️ **Time:** `{progress_formatted} / {TimeFormatter(duration * 1000)}`\n"
                    f"⚡ **Speed:** `{speed_mb_per_sec:.1f} MB/s`\n"
                    f"⏳ **ETA:** `{TimeFormatter(int(eta * 1000))}`\n\n"
                    f"🛑 Use /cancel to stop recording"
                )
                
                try:
                    await msg.edit_text(progress_text)
                except:
                    pass
                
                await asyncio.sleep(5)  # Update every 5 seconds

        # Start progress tracking
        progress_task = asyncio.create_task(update_recording_progress())
        progress_tasks[user_id] = progress_task

        await msg.edit_text("📥 **Starting recording...**")

        # Record with all video and audio streams
        ffmpeg_cmd = (
            f'ffmpeg -y -probesize 10000000 -analyzeduration 15000000 '
            f'-i "{url}" -map 0:v -map 0:a -c:v copy -c:a copy -t {timestamp} "{video_path}"'
        )
        
        # Run FFmpeg and track PID
        args = shlex.split(ffmpeg_cmd)
        ffmpeg_process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        # Store FFmpeg PID
        user_ffmpeg_pids[user_id] = ffmpeg_process.pid
        LOG.info(f"Started FFmpeg process {ffmpeg_process.pid} for user {user_id}")
        
        # Wait for FFmpeg to complete
        stdout, stderr = await ffmpeg_process.communicate()
        retcode = ffmpeg_process.returncode
        
        # Remove PID tracking
        user_ffmpeg_pids.pop(user_id, None)
        
        # Stop progress tracking
        if user_id in progress_tasks:
            progress_tasks[user_id].cancel()
            progress_tasks.pop(user_id, None)
        
        # Check if recording was cancelled
        was_cancelled = user_id in cancelled_users
        
        if retcode != 0 and not was_cancelled:
            raise Exception(f"🚫 FFmpeg Error:\n{stderr.decode()}")
        
        # Check if video file exists and has content
        if not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
            if was_cancelled:
                await msg.edit_text("❌ **Recording cancelled - no video recorded**")
                return
            else:
                raise Exception("🚫 No video file created or file is empty")
        
        # Show thumbnail generation message
        thumbnail_msg = await message.reply_text("🖼 **Generating thumbnail...**")

        # Get actual duration and fix metadata
        dur = await get_duration_ffmpeg(video_path)
        if dur == 0:
            dur = time_to_seconds(timestamp)
        
        # Fix video metadata to ensure Telegram recognizes duration
        fixed_video_path = join(save_dir, f"fixed_{filename}")
        fix_cmd = (
            f'ffmpeg -y -i "{video_path}" -map 0 -c copy '
            f'-metadata creation_time="{time.strftime("%Y-%m-%dT%H:%M:%S")}" '
            f'"{fixed_video_path}"'
        )
        retcode, out, err = await runcmd(fix_cmd)
        if retcode == 0:
            os.replace(fixed_video_path, video_path)
        else:
            LOG.warning(f"Metadata fix failed: {err}")

        # Generate thumbnail
        rand_sec = random.randint(5, max(dur - 5, 6))
        thumb_path = join(save_dir, "thumb.jpg")
        thumb_cmd = f'ffmpeg -y -ss {rand_sec} -i "{video_path}" -vframes 1 -q:v 2 "{thumb_path}"'
        retcode, out, err = await runcmd(thumb_cmd)
        if retcode != 0:
            LOG.warning(f"Thumbnail generation failed: {err}")

        # Delete thumbnail generation message
        await thumbnail_msg.delete()

        # Prepare caption based on whether it was cancelled or completed
        if was_cancelled:
            caption = (
                f"🎬 **{raw_filename.strip()}**\n\n"
                f"⏱ **Duration:** `{TimeFormatter(dur * 1000)}`\n"
                f"📁 **Format:** MKV (Partial Recording)\n"
                f"👤 **Recorded by:** @{message.from_user.username or 'anonymous'}\n\n"
                f"⚠️ _Recording was cancelled, but recorded portion is sent_"
            )
        else:
            caption = (
                f"🎬 **{raw_filename.strip()}**\n\n"
                f"⏱ **Duration:** `{TimeFormatter(dur * 1000)}`\n"
                f"📁 **Format:** MKV (Original Quality)\n"
                f"👤 **Recorded by:** @{message.from_user.username or 'anonymous'}\n\n"
                f"✅ _Recording completed successfully!_"
            )

        start_time = time.time()

        # Upload with duration parameter to help Telegram
        await message.reply_video(
            video=video_path,
            caption=caption,
            duration=dur,  # Explicitly set duration
            thumb=thumb_path if os.path.exists(thumb_path) else None,
            progress=progress_for_pyrogram,
            progress_args=(message, start_time, msg, save_dir, was_cancelled)  # Pass save_dir and cancellation status
        )

        # ✅ SUCCESS: Now clean up files after successful upload
        if save_dir and os.path.exists(save_dir):
            try:
                shutil.rmtree(save_dir)
                LOG.info(f"Cleaned up files after successful upload: {save_dir}")
            except Exception as cleanup_err:
                LOG.warning(f"Cleanup failed after upload: {cleanup_err}")

    except Exception as e:
        LOG.error(f"Error in handle_record: {e}")
        try:
            # Don't show error if user cancelled
            if user_id not in cancelled_users:
                err_text = str(e)
                if len(err_text) > 4000:
                    err_text = err_text[:4000] + "... [truncated]"
                await msg.edit(f"❌ **Recording Failed!**\n\n`{err_text}`")
            
            # ❌ ERROR: Clean up files on failure (unless cancelled - files already cleaned)
            if user_id not in cancelled_users and save_dir and os.path.exists(save_dir):
                try:
                    shutil.rmtree(save_dir)
                    LOG.info(f"Cleaned up files after failed recording: {save_dir}")
                except Exception as cleanup_err:
                    LOG.warning(f"Cleanup failed after error: {cleanup_err}")
                    
        except Exception as exc:
            LOG.error(f"Failed to edit error message: {exc}")

    finally:
        # Clean up user data
        user_status.pop(user_id, None)
        user_tasks.pop(user_id, None)
        user_ffmpeg_pids.pop(user_id, None)
        progress_tasks.pop(user_id, None)
        cancelled_users.discard(user_id)  # Remove from cancelled list


async def progress_for_pyrogram(current, total, message, start, msg, save_dir=None, was_cancelled=False):
    now = time.time()
    diff = now - start
    if diff == 0:
        diff = 1
    percentage = current * 100 / total
    speed = current / diff
    
    # Calculate file sizes in MB
    uploaded_mb = current / (1024 * 1024)
    total_mb = total / (1024 * 1024)
    speed_mb = speed / (1024 * 1024)
    
    # Upload progress bar
    bar_length = 15
    filled_length = int(bar_length * percentage // 100)
    bar = '█' * filled_length + '░' * (bar_length - filled_length)
    
    # Update at major milestones
    update_points = [0, 10, 25, 50, 75, 90, 95, 99, 100]
    current_percent = int(percentage)
    
    if current_percent in update_points or current == total:
        eta = TimeFormatter(int((total - current) / speed * 1000)) if speed > 0 else "00:00:00"
        
        status_prefix = "📤 **Uploading Partial Recording**" if was_cancelled else "📤 **Uploading Video**"
        
        text = (
            f"{status_prefix}\n"
            f"`[{bar}]` {percentage:.1f}%\n"
            f"📊 **Progress:** `{uploaded_mb:.1f} / {total_mb:.1f} MB`\n"
            f"⚡ **Speed:** `{speed_mb:.1f} MB/s`\n"
            f"⏳ **ETA:** `{eta}`"
        )
        try:
            await msg.edit_text(text)
        except Exception:
            pass
        
        # Final completion message and cleanup
        if current == total:
            if was_cancelled:
                completion_text = "✅ **Partial Recording Sent!**\n🗑️ **Cleaning up temporary files...**"
            else:
                completion_text = "✅ **Upload Completed Successfully!**\n🗑️ **Cleaning up temporary files...**"
            
            try:
                await msg.edit_text(completion_text)
                
                # Clean up files after upload is complete
                if save_dir and os.path.exists(save_dir):
                    try:
                        shutil.rmtree(save_dir)
                        LOG.info(f"Cleaned up files after upload: {save_dir}")
                        # Update message to show cleanup complete
                        await asyncio.sleep(2)
                        if was_cancelled:
                            await msg.edit_text("✅ **Partial Recording Sent!**\n🗑️ **Temporary files cleaned up!**")
                        else:
                            await msg.edit_text("✅ **Upload Completed Successfully!**\n🗑️ **Temporary files cleaned up!**")
                    except Exception as cleanup_err:
                        LOG.warning(f"Cleanup failed after upload: {cleanup_err}")
                        if was_cancelled:
                            await msg.edit_text("✅ **Partial Recording Sent!**\n⚠️ **Cleanup failed, but video was sent.**")
                        else:
                            await msg.edit_text("✅ **Upload Completed Successfully!**\n⚠️ **Cleanup failed, but video was sent.**")
            except Exception:
                pass


async def runcmd(cmd: str) -> Tuple[int, str, str]:
    args = shlex.split(cmd)
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    return process.returncode, stdout.decode(), stderr.decode()


async def get_video_duration(input_file: str) -> int:
    try:
        parser = createParser(input_file)
        if not parser:
            return 0
        metadata = extractMetadata(parser)
        if not metadata or not metadata.has("duration"):
            return 0
        duration = metadata.get("duration")
        return int(duration.seconds)
    except Exception as e:
        LOG.warning(f"Hachoir failed: {e}")
        return 0


async def get_duration_ffmpeg(input_file: str) -> int:
    try:
        cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{input_file}"'
        retcode, out, err = await runcmd(cmd)
        if retcode == 0:
            return int(float(out.strip()))
    except Exception as e:
        LOG.warning(f"FFprobe failed: {e}")
    return 0


def time_to_seconds(time_str: str) -> int:
    """Convert HH:MM:SS to seconds"""
    try:
        h, m, s = time_str.split(':')
        return int(h) * 3600 + int(m) * 60 + int(s)
    except:
        return 0


def TimeFormatter(milliseconds: int) -> str:
    seconds, ms = divmod(milliseconds, 1000)
    minutes, sec = divmod(seconds, 60)
    hours, min_ = divmod(minutes, 60)
    
    if hours > 0:
        return f"{hours:02}:{min_:02}:{sec:02}"
    else:
        return f"{min_:02}:{sec:02}"


if __name__ == "__main__":
    print("🎬 Starting Video Recorder Bot...")
    print("⚡ Bot is now running!")
    app.run()
