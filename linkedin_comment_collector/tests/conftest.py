import os

# Tests must never touch the production database. An empty DATABASE_URL makes db_manager fall back to
# SQLite, and load_dotenv (which never overrides existing variables) cannot re-add the Neon URL from .env.local.
os.environ["DATABASE_URL"] = ""
os.environ["DATABASE_URL_UNPOOLED"] = ""
