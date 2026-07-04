from os import environ

# 🔧 Bot Configuration Settings
# ⚙️ Get values from environment variables or use defaults

API_ID = int(environ.get("API_ID", ""))
API_HASH = environ.get("API_HASH", "")
BOT_TOKEN = environ.get("BOT_TOKEN", "")

# 👥 Authorized Users - Bot will work only with these users
AUTH_USERS = list(map(int, environ.get("AUTH_USERS", "12345678 87654321").split()))

# 👑 Owner/Admin ID - Multiple owners supported
OWNER_ID = list(map(int, environ.get("OWNER_IDS", "").split()))

# 📁 Download Directory - Where temporary files are stored
DOWNLOAD_DIRECTORY = environ.get("DOWNLOAD_DIRECTORY", "./downloads")

# 🏷️ Default Metadata - Video file metadata title
DEFAULT_METADATA = environ.get("DEFAULT_METADATA", "")

# 📄 Default Filename - Used when no filename is provided
DEFAULT_FILENAME = environ.get("DEFAULT_FILENAME", "Toonix")

# 🌍 Timezone Configuration
TIMEZONE = environ.get("TIMEZONE", "Asia/Kolkata")
