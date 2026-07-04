from os import environ

# 🔧 Bot Configuration Settings
# ⚙️ Get values from environment variables or use defaults

API_ID = int(environ.get("29481626", ""))
API_HASH = environ.get("4892185769903521077c4cea97808b8c", "")
BOT_TOKEN = environ.get("8191916199:AAELZ8-fshGPId9LfTTj6oRcPz31quRu0MU", "")

# 👥 Authorized Users - Bot will work only with these users
AUTH_USERS = list(map(int, environ.get("AUTH_USERS", "12345678 87654321").split()))

# 👑 Owner/Admin ID - Multiple owners supported
OWNER_ID = list(map(int, environ.get("", "").split()))

# 📁 Download Directory - Where temporary files are stored
DOWNLOAD_DIRECTORY = environ.get("DOWNLOAD_DIRECTORY", "./downloads")

# 🏷️ Default Metadata - Video file metadata title
DEFAULT_METADATA = environ.get("DEFAULT_METADATA", "")

# 📄 Default Filename - Used when no filename is provided
DEFAULT_FILENAME = environ.get("DEFAULT_FILENAME", "Toonix")

# 🌍 Timezone Configuration
TIMEZONE = environ.get("TIMEZONE", "Asia/Kolkata")
