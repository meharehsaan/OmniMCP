from environs import Env

env = Env()
env.read_env()


class Config(object):
    DEBUG = False
    TESTING = False

class DevelopmentConfig(Config):
    DEBUG = True
    TESTING = True

    # add things in your .env file
    FILES_PATH = "tmp/"
    GEMINI_API_KEY = env.str("GEMINI_API_KEY")
    GEMINI_MODEL_ID = env.str("GEMINI_MODEL_ID")

    USER = env.str("USER")               # ehsaan
    PASSWORD = env.str("PASSWORD")       # ehsaan@123