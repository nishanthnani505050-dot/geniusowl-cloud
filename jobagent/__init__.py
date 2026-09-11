"""
GeniusOwl job agent.

Daily matched-job discovery, adjacent-role expansion, ATS resume tailoring
and WhatsApp-native applying. Mount it on the existing Flask app:

    from jobagent.routes import bp as jobagent_bp
    from jobagent import store
    store.init_db()
    app.register_blueprint(jobagent_bp)
"""

__all__ = ["store", "models", "taxonomy", "matcher", "tailor", "apply", "whatsapp",
           "conversation", "daily", "resume", "routes", "sources"]
