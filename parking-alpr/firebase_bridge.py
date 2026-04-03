import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import firebase_admin
from firebase_admin import credentials, firestore, messaging
from flask import Flask, jsonify, request

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
SERVICE_ACCOUNT_FILE = os.getenv(
    "FIREBASE_SERVICE_ACCOUNT_FILE", str(BASE_DIR / "firebase-service-account.json")
)
DATABASE_FILE = Path(os.getenv("ALPR_DB_FILE", str(BASE_DIR / "parking.sqlite3")))
DEFAULT_PARKING_ID = os.getenv("ALPR_DEFAULT_PARKING_ID", "p1")
DEFAULT_TOTAL_PLACES = int(os.getenv("ALPR_DEFAULT_TOTAL_PLACES", "100"))
P1_MAX_PLACES = 100

storage_lock = threading.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_plate(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def compute_rate(total: int, occupees: int) -> int:
    if total <= 0:
        return 0
    return int(round((occupees / total) * 100))


def open_database() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_FILE)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database() -> None:
    DATABASE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open_database() as connection:
        connection.execute("PRAGMA journal_mode=WAL;")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS parking_state (
                parking_id TEXT PRIMARY KEY,
                total INTEGER NOT NULL,
                occupees INTEGER NOT NULL,
                taux INTEGER NOT NULL,
                last_plate TEXT,
                last_event TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS parking_plates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parking_id TEXT NOT NULL,
                plate TEXT NOT NULL,
                raw_plate TEXT,
                source TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(parking_id, plate)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_parking_plates_parking_id ON parking_plates(parking_id)"
        )


def ensure_state(connection: sqlite3.Connection, parking_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM parking_state WHERE parking_id = ?",
        (parking_id,),
    ).fetchone()
    if row is None:
        now = utc_now()
        total = DEFAULT_TOTAL_PLACES
        occupees = 0
        taux = compute_rate(total, occupees)
        connection.execute(
            """
            INSERT INTO parking_state (parking_id, total, occupees, taux, last_plate, last_event, updated_at)
            VALUES (?, ?, ?, ?, NULL, 'initialized', ?)
            """,
            (parking_id, total, occupees, taux, now),
        )
        row = connection.execute(
            "SELECT * FROM parking_state WHERE parking_id = ?",
            (parking_id,),
        ).fetchone()
    return row


def serialize_state(connection: sqlite3.Connection, parking_id: str) -> dict[str, Any]:
    state = ensure_state(connection, parking_id)
    total = int(state["total"])
    occupees = int(state["occupees"])
    taux = int(state["taux"])
    rows = connection.execute(
        """
        SELECT plate, raw_plate, source, created_at, updated_at
        FROM parking_plates
        WHERE parking_id = ?
        ORDER BY updated_at DESC, created_at DESC, plate ASC
        """,
        (parking_id,),
    ).fetchall()
    return {
        "parking_id": parking_id,
        "total": total,
        "occupees": occupees,
        "free": max(total - occupees, 0),
        "taux": taux,
        "plate_count": len(rows),
        "last_plate": state["last_plate"],
        "last_event": state["last_event"],
        "updated_at": state["updated_at"],
        "plates": [
            {
                "plate": row["plate"],
                "raw_plate": row["raw_plate"],
                "source": row["source"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ],
    }


def sync_firebase_state(parking_id: str, total: int, occupees: int, last_plate: str | None) -> None:
    if not firebase_initialized:
        return

    free = max(total - occupees, 0)
    taux = compute_rate(total, occupees)
    parking_data = {
        "parking_id": parking_id,
        "total": total,
        "occupees": occupees,
        "free": free,
        "taux": taux,
        "last_plate": last_plate,
        "updated_at": utc_now(),
    }
    db.collection("parkings").document(str(parking_id)).set(parking_data, merge=True)


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


initialize_database()


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
    data = request.get_json(silent=True) or {}
    parking_id = str(data.get("parking_id") or DEFAULT_PARKING_ID)
    total = data.get("total")
    occupees = data.get("occupees")

    if total is None:
        return jsonify({"error": "total is required"}), 400

    try:
        total_int = int(total)
        if total_int < 0:
            return jsonify({"error": "total must be greater than or equal to 0"}), 400
        if parking_id.lower() == "p1" and total_int > P1_MAX_PLACES:
            return jsonify({"error": "p1 max places is 100"}), 400

        with storage_lock:
            with open_database() as connection:
                state = ensure_state(connection, parking_id)
                current_occupees = int(state["occupees"])
                occupees_int = current_occupees if occupees is None else int(occupees)
                if occupees_int < 0:
                    return jsonify({"error": "occupees must be greater than or equal to 0"}), 400
                if parking_id.lower() == "p1" and occupees_int > P1_MAX_PLACES:
                    return jsonify({"error": "p1 max places is 100"}), 400

                taux = compute_rate(total_int, occupees_int)
                now = utc_now()
                connection.execute(
                    """
                    UPDATE parking_state
                    SET total = ?, occupees = ?, taux = ?, last_event = 'config_updated', updated_at = ?
                    WHERE parking_id = ?
                    """,
                    (total_int, occupees_int, taux, now, parking_id),
                )
                connection.commit()

        sync_firebase_state(parking_id, total_int, occupees_int, None)
        return jsonify({"success": True, "state": {"parking_id": parking_id, "total": total_int, "occupees": occupees_int, "taux": taux}}), 200
    except (TypeError, ValueError):
        return jsonify({"error": "total and occupees must be integers"}), 400
    except Exception as exc:
        return jsonify({"error": f"Failed to update parking state: {str(exc)}"}), 500


@app.route("/plates/scan", methods=["POST"])
def scan_plate() -> tuple[Any, int]:
    data = request.get_json(silent=True) or {}
    parking_id = str(data.get("parking_id") or DEFAULT_PARKING_ID)
    raw_plate = data.get("plate") or data.get("license_plate") or data.get("recognized_plate")
    source = str(data.get("source") or "homeassistant")

    if raw_plate is None:
        return jsonify({"error": "plate is required"}), 400

    plate = normalize_plate(str(raw_plate))
    if not plate:
        return jsonify({"error": "plate is empty after normalization"}), 400

    try:
        with storage_lock:
            with open_database() as connection:
                state = ensure_state(connection, parking_id)
                total = int(state["total"])
                occupees = int(state["occupees"])
                now = utc_now()

                existing = connection.execute(
                    "SELECT id FROM parking_plates WHERE parking_id = ? AND plate = ?",
                    (parking_id, plate),
                ).fetchone()

                if existing is not None:
                    connection.execute(
                        "DELETE FROM parking_plates WHERE id = ?",
                        (existing["id"],),
                    )
                    occupees = max(occupees - 1, 0)
                    action = "removed"
                else:
                    if total > 0 and occupees >= total:
                        return jsonify({
                            "success": False,
                            "error": "parking_full",
                            "parking_id": parking_id,
                            "total": total,
                            "occupees": occupees,
                            "free": 0,
                            "taux": compute_rate(total, occupees),
                        }), 409

                    connection.execute(
                        """
                        INSERT INTO parking_plates (parking_id, plate, raw_plate, source, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (parking_id, plate, str(raw_plate), source, now, now),
                    )
                    occupees += 1
                    action = "added"

                taux = compute_rate(total, occupees)
                connection.execute(
                    """
                    UPDATE parking_state
                    SET occupees = ?, taux = ?, last_plate = ?, last_event = ?, updated_at = ?
                    WHERE parking_id = ?
                    """,
                    (occupees, taux, plate, action, now, parking_id),
                )
                connection.commit()

        sync_firebase_state(parking_id, total, occupees, plate)
        return (
            jsonify(
                {
                    "success": True,
                    "action": action,
                    "plate": plate,
                    "raw_plate": str(raw_plate),
                    "parking_id": parking_id,
                    "total": total,
                    "occupees": occupees,
                    "free": max(total - occupees, 0),
                    "taux": taux,
                }
            ),
            200,
        )
    except (TypeError, ValueError):
        return jsonify({"error": "parking_id, total and occupees must be valid integers when provided"}), 400
    except Exception as exc:
        return jsonify({"error": f"Failed to process plate: {str(exc)}"}), 500


@app.route("/parking/state", methods=["GET"])
def parking_state() -> tuple[Any, int]:
    parking_id = request.args.get("parking_id", DEFAULT_PARKING_ID)
    try:
        with storage_lock:
            with open_database() as connection:
                state = serialize_state(connection, parking_id)
        return jsonify({"success": True, "state": state}), 200
    except Exception as exc:
        return jsonify({"error": f"Failed to load parking state: {str(exc)}"}), 500


@app.route("/health", methods=["GET"])
def health() -> tuple[Any, int]:
    return (
        jsonify(
            {
                "ok": True,
                "firebase_initialized": firebase_initialized,
                "service_account_file": SERVICE_ACCOUNT_FILE,
                "service_account_file_exists": os.path.exists(SERVICE_ACCOUNT_FILE),
                "database_file": str(DATABASE_FILE),
                "database_file_exists": DATABASE_FILE.exists(),
            }
        ),
        200,
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5001"))
    app.run(host="0.0.0.0", port=port)
