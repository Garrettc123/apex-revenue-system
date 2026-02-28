from flask import Flask, jsonify, request
import datetime, os
import google.generativeai as genai

app = Flask(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")
else:
    model = None

@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "time": str(datetime.datetime.utcnow()),
        "gemini": "connected" if model else "no key — set GEMINI_API_KEY secret"
    })

@app.route("/metrics")
def metrics():
    return jsonify({
        "status": "operational",
        "revenue_target": "$5000 MRR",
        "systems": ["API", "Revenue Tracker", "AI Hub", "Gemini"],
        "uptime": "100%"
    })

@app.route("/")
def index():
    return jsonify({"msg": "Apex Revenue System — LIVE", "owner": "Garrett Carrol", "gemini": bool(model)})

@app.route("/genesis", methods=["GET", "POST"])
def genesis():
    if not model:
        return jsonify({"error": "GEMINI_API_KEY not set in Railway secrets"}), 503
    if request.is_json and request.json.get("prompt"):
        prompt = request.json["prompt"]
    else:
        prompt = "You are GENESIS, an autonomous AI revenue engine. Analyze the current state of AI SaaS markets and provide 3 specific, actionable revenue strategies for an autonomous AI business platform. Be concrete with dollar amounts and timelines."
    try:
        response = model.generate_content(prompt)
        return jsonify({
            "status": "success",
            "genesis_output": response.text,
            "timestamp": str(datetime.datetime.utcnow())
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/ai/analyze", methods=["POST"])
def ai_analyze():
    if not model:
        return jsonify({"error": "GEMINI_API_KEY not set in Railway secrets"}), 503
    data = request.get_json(force=True)
    prompt = data.get("prompt", "Provide a business analysis for an autonomous AI revenue system.")
    try:
        response = model.generate_content(prompt)
        return jsonify({
            "analysis": response.text,
            "timestamp": str(datetime.datetime.utcnow())
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/ai/leads", methods=["GET"])
def ai_leads():
    if not model:
        return jsonify({"error": "GEMINI_API_KEY not set"}), 503
    try:
        response = model.generate_content(
            "Generate 3 high-value B2B SaaS lead profiles for an autonomous AI platform. "
            "Include: company name, industry, pain point, estimated deal size USD, and outreach angle. "
            "Return as a JSON array."
        )
        return jsonify({"leads": response.text, "timestamp": str(datetime.datetime.utcnow())})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
