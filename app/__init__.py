import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv

load_dotenv()

db = SQLAlchemy()


def create_app():
    app = Flask(__name__, template_folder="../templates", static_folder="../static")

    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv(
        "DATABASE_URL", "sqlite:///meetings.db"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    from app.routes import main_bp

    app.register_blueprint(main_bp)

    # Register markdown filter for templates
    from markupsafe import Markup
    try:
        import markdown as md
        @app.template_filter('markdown')
        def markdown_filter(text):
            return Markup(md.markdown(text or '', extensions=['extra', 'nl2br']))
    except ImportError:
        import re
        @app.template_filter('markdown')
        def markdown_filter(text):
            # Simple fallback: convert **bold**, ## headers, - bullets, [ ] checkboxes
            if not text:
                return Markup('')
            text = Markup.escape(text)
            text = re.sub(r'^## (.+)$', r'<h2>\1</h2>', str(text), flags=re.MULTILINE)
            text = re.sub(r'^### (.+)$', r'<h3>\1</h3>', str(text), flags=re.MULTILINE)
            text = re.sub(r'^\- \[ \] (.+)$', r'<li><input type="checkbox"> \1</li>', str(text), flags=re.MULTILINE)
            text = re.sub(r'^\- (.+)$', r'<li>\1</li>', str(text), flags=re.MULTILINE)
            text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
            text = re.sub(r'\n\n', '<br><br>', text)
            return Markup(text)

    with app.app_context():
        db.create_all()
        _migrate_calendar_feeds(db)

    return app


def _migrate_calendar_feeds(db):
    """Add owner_name / owner_email columns if they don't exist yet."""
    from sqlalchemy import text
    with db.engine.connect() as conn:
        migrations = [
            ("calendar_feeds", "owner_name",  "VARCHAR(255)"),
            ("calendar_feeds", "owner_email", "VARCHAR(255)"),
            ("meetings",       "transcript",           "TEXT"),
            ("meetings",       "formatted_transcript", "TEXT"),
            ("tasks",          "meeting_id",           "INTEGER"),
        ]
        for table, col, coltype in migrations:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {coltype} DEFAULT ''"))
                conn.commit()
            except Exception:
                pass  # column already exists
