"""Run versioned schema setup and checkpoint migrations, optionally seed demo users."""

from configuration import Config
from database import Database

if __name__ == "__main__":
    config = Config.environment()
    db = Database(config.database_url)
    db.migrate()
    db.setup_checkpoints()
    if config.seed_demo:
        db.seed_demo()
    print("Schema revision 001 and checkpoint migrations applied.")
    db.engine.dispose()
