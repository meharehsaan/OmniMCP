from pathlib import Path
from environs import Env

env = Env()
env.read_env()


class DevConfig:
	BASE_DIR = Path(__file__).resolve().parent

	GEMINI_API_KEY = env.str("GEMINI_API_KEY")
	GEMINI_MODEL_ID = env.str("GEMINI_MODEL_ID")
	USER = env.str("OMNIMCP_USER", env.str("USER_NAME", env.str("USER", "admin")), )
	PASSWORD = env.str("PASSWORD")
	AUTH_SECRET = env.str("AUTH_SECRET")

	FILES_PATH = Path(env.str("FILES_PATH", str(BASE_DIR / "workspace"))).expanduser().resolve()
	COOKIE_SECURE = env.bool("COOKIE_SECURE", False)
	SESSION_TTL_SECONDS = max(300, env.int("SESSION_TTL_SECONDS", 60 * 60 * 12))
	MAX_SESSIONS = max(1, env.int("MAX_SESSIONS", 10))
	MAX_HISTORY_MESSAGES = max(10, env.int("MAX_HISTORY_MESSAGES", 100))
	MAX_TOOL_ROUNDS = max(1, env.int("MAX_TOOL_ROUNDS", 12))
	MAX_FILE_SIZE_BYTES = max(1024, env.int("MAX_FILE_SIZE_BYTES", 2 * 1024 * 1024))
	MAX_TOOL_OUTPUT_CHARS = max(1000, env.int("MAX_TOOL_OUTPUT_CHARS", 50_000))


if len(DevConfig.PASSWORD) < 10:
	raise ValueError("PASSWORD must contain at least 10 characters.")
if len(DevConfig.AUTH_SECRET) < 32:
	raise ValueError("AUTH_SECRET must contain at least 32 characters.")
if DevConfig.AUTH_SECRET in {"replace-with-a-long-random-secret", "your-generated-random-secret", }:
	raise ValueError("AUTH_SECRET must be replaced with a cryptographically random value.")
