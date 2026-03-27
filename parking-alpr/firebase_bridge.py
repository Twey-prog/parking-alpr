import os
from typing import Any

import firebase_admin
from firebase_admin import credentials, firestore, messaging
from flask import Flask, jsonify, request

app = Flask(__name__)

SERVICE_ACCOUNT_FILE = os.getenv("FIREBASE_SERVICE_ACCOUNT_FILE", "firebase-service-account.json")

# Initialize Firebase Admin SDK
try:
    cred = credentials.Certificate(SERVICE_ACCOUNT_FILE)
    firebase_admin.initialize_app(cred)
    firebase_initialized = True
    db = firestore.client()
except FileNotFoundError:
    firebase_initialized = False
    db = None
    print(f"Warning: Service account file not found at {SERVICE_ACCOUNT_FILE}")
except Exception as exc:
    firebase_initialized = False
    db = None
    print(f"Warning: Failed to initialize Firebase: {exc}")


@app.route("/notify", methods=["POST"])
def notify() -> tuple[Any, int]:
    if not firebase_initialized:
        return jsonify({"error": "Firebase not initialized"}), 503

    data = request.get_json(silent=True) or {}
    parking_id = data.get("parking_id")
    taux = data.get("taux")

    if parking_id is None or taux is None:
        return jsonify({"error": "parking_id and taux are required"}), 400

    try:
        message = messaging.Message(
            topic=f"parking_{parking_id}",
            notification=messaging.Notification(
                title=f"⚠️ Parking {parking_id} presque plein",
                body=f"{taux}% occupé",
            ),
            data={
                "parking_id": str(parking_id),
                "taux": str(taux),
            },
        )
        message_id = messaging.send(message)
        return jsonify({"success": True, "message_id": message_id}), 200
    except Exception as exc:
        return jsonify({"error": f"Failed to send message: {str(exc)}"}), 500


@app.route("/places", methods=["POST"])
def places() -> tuple[Any, int]:
    if not firebase_initialized:
        return jsonify({"error": "Firebase not initialized"}), 503

    data = request.get_json(silent=True) or {}
    parking_id = data.get("parking_id")
    total = data.get("total")
    occupees = data.get("occupees")

    if parking_id is None or total is None or occupees is None:
        return jsonify({"error": "parking_id, total and occupees are required"}), 400

    try:
        # Calculate derived fields
        free = total - occupees
        taux = int((occupees / total) * 100) if total > 0 else 0

        # Update Firestore
        parking_data = {
            "parking_id": parking_id,
            "total": total,
            "occupees": occupees,
            "free": free,
            "taux": taux,
        }
        db.collection("parkings").document(parking_id).update(parking_data)

        # Send FCM message
        message = messaging.Message(
            topic=f"parking_{parking_id}",
            data={
                "type": "places_update",
                "parking_id": str(parking_id),
                "total": str(total),
                "occupees": str(occupees),
                "free": str(free),
                "taux": str(taux),
            },
        )
        message_id = messaging.send(message)
        return jsonify({"success": True, "message_id": message_id}), 200
    except Exception as exc:
        return jsonify({"error": f"Failed to send message: {str(exc)}"}), 500


@app.route("/health", methods=["GET"])
def health() -> tuple[Any, int]:
    return (
        jsonify(
            {
                "ok": True,
                "firebase_initialized": firebase_initialized,
                "service_account_file": SERVICE_ACCOUNT_FILE,
                "service_account_file_exists": os.path.exists(SERVICE_ACCOUNT_FILE),
            }
        ),
        200,
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5001"))
    app.run(host="0.0.0.0", port=port)
