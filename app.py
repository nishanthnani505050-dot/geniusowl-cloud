"""
app.py — Flask web server entrypoint for GeniusOwl cloud agent.

Endpoints:
  GET  /health  -> {"status":"ok"}
  POST /talk    -> accepts WAV audio (audio/wav), returns reply WAV audio

The job agent (daily matched jobs + WhatsApp applying) mounts its own routes
under /jobagent and /whatsapp — see jobagent/routes.py.

Auth: Bearer token via Authorization header.
Device identity: X-Device-Id header (used for conversation memory).
"""

import logging
import os
import io
from flask import Flask, request, Response, jsonify

from agent import CoachingAgent
from audio_pipeline import AudioPipeline
from auth import check_bearer_token

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

app = Flask(__name__)
agent = CoachingAgent()
audio = AudioPipeline()

# Job agent: optional. If its config is missing we still serve the voice
# coach rather than failing the whole deploy.
try:
    from jobagent.routes import bp as jobagent_bp
    from jobagent import store as jobagent_store

    jobagent_store.init_db()
    app.register_blueprint(jobagent_bp)
    log.info("job agent routes mounted")
except Exception as e:
    log.warning("job agent not mounted: %s", e)


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"service": "purpleowl-agent", "status": "ok"})


@app.route('/talk', methods=['POST'])
def talk():
    if not check_bearer_token(request):
        return jsonify({"error": "unauthorized"}), 401

    device_id = request.headers.get('X-Device-Id', 'unknown')
    audio_bytes = request.get_data()

    if not audio_bytes or len(audio_bytes) < 100:
        return jsonify({"error": "empty or too-small audio body"}), 400

    try:
        # 1. Transcribe student speech to text
        student_text = audio.transcribe(audio_bytes)
        log.info(f"[{device_id}] student said: {student_text!r}")

        if not student_text.strip():
            student_text = "(silence)"

        # 2. Get coaching reply from the agent
        reply_text = agent.reply(device_id, student_text)
        log.info(f"[{device_id}] owl says: {reply_text!r}")

        # 3. Synthesize reply audio
        reply_wav = audio.synthesize(reply_text)

        return Response(reply_wav, mimetype="audio/wav")

    except Exception as e:
        log.exception("talk handler crashed")
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
