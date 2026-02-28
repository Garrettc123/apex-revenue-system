from flask import Flask, jsonify
import datetime, os

app = Flask(__name__)

@app.route("/")
def index():
    return jsonify({"msg": "Apex Revenue System — LIVE", "owner": "Garrett Carrol", "status": "operational"})

@app.route("/health")
def health():
    return jsonify({"status": "healthy", "time": str(datetime.datetime.utcnow())})

@app.route("/metrics")
def metrics():
    return jsonify({
        "status": "operational",
        "revenue_target": "$5000 MRR",
        "systems": ["API", "Revenue Tracker", "AI Hub"],
        "uptime": "100%",
        "owner": "Garrett Carrol"
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
