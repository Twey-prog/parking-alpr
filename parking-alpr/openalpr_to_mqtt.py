#!/usr/bin/env python3
"""Scan one image with OpenALPR and publish the detected plate to MQTT."""

import argparse
import json
import subprocess
import sys
from urllib import error as urllib_error
from urllib import request as urllib_request
from pathlib import Path


def run_openalpr(openalpr_bin: str, country: str, image_path: Path) -> dict:
    command = [openalpr_bin, "-j", "-c", country, str(image_path)]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"OpenALPR binary not found: {openalpr_bin}. Install OpenALPR or set --openalpr-bin."
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise RuntimeError(f"OpenALPR failed: {stderr}") from exc

    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OpenALPR output is not valid JSON.") from exc


def pick_best_plate(payload: dict, min_confidence: float) -> str:
    results = payload.get("results") or []
    if not results:
        raise RuntimeError("No plate detected by OpenALPR.")

    best = results[0]
    candidates = best.get("candidates") or []
    if not candidates:
        raise RuntimeError("No candidate plate found in OpenALPR result.")

    top = candidates[0]
    confidence = float(top.get("confidence", 0.0))
    if confidence < min_confidence:
        raise RuntimeError(
            f"Best candidate confidence too low: {confidence:.2f} < {min_confidence:.2f}"
        )

    plate = str(top.get("plate", "")).strip().upper()
    if not plate:
        raise RuntimeError("Detected plate is empty.")
    return plate


def publish_plate(
    mqtt_host: str,
    mqtt_port: int,
    mqtt_topic: str,
    plate: str,
    mosquitto_pub_bin: str,
) -> None:
    command = [
        mosquitto_pub_bin,
        "-h",
        mqtt_host,
        "-p",
        str(mqtt_port),
        "-t",
        mqtt_topic,
        "-m",
        plate,
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"mosquitto_pub binary not found: {mosquitto_pub_bin}. Install mosquitto-clients or set --mosquitto-pub-bin."
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise RuntimeError(f"Failed to publish MQTT message: {stderr}") from exc


def post_plate_to_webhook(webhook_url: str, plate: str) -> None:
    payload = json.dumps({"plate": plate}).encode("utf-8")
    req = urllib_request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=10) as response:
            status = response.getcode()
            if status < 200 or status >= 300:
                raise RuntimeError(f"Webhook returned non-success status: {status}")
    except urllib_error.URLError as exc:
        raise RuntimeError(f"Failed to call Home Assistant webhook: {exc}") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read a plate with OpenALPR and publish it to MQTT for Home Assistant."
    )
    parser.add_argument("--image", required=True, help="Path to image file to analyze.")
    parser.add_argument("--country", default="eu", help="OpenALPR country code (default: eu).")
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=80.0,
        help="Minimum confidence required to publish (default: 80).",
    )
    parser.add_argument("--openalpr-bin", default="openalpr", help="Path to openalpr binary.")
    parser.add_argument(
        "--mosquitto-pub-bin",
        default="mosquitto_pub",
        help="Path to mosquitto_pub binary.",
    )
    parser.add_argument(
        "--ha-webhook-url",
        default="http://localhost:8123/api/webhook/alpr_p1_plate",
        help="Home Assistant webhook URL for plate ingestion.",
    )
    parser.add_argument(
        "--transport",
        choices=["webhook", "mqtt"],
        default="webhook",
        help="Transport to push detected plate (default: webhook).",
    )
    parser.add_argument("--mqtt-host", default="localhost", help="MQTT broker host (mqtt mode).")
    parser.add_argument("--mqtt-port", type=int, default=1883, help="MQTT broker port.")
    parser.add_argument(
        "--mqtt-topic",
        default="parking/p1/alpr/plate",
        help="MQTT topic used by Home Assistant automation.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run detection but do not publish to MQTT.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    image_path = Path(args.image)
    if not image_path.exists():
        print(f"Image not found: {image_path}", file=sys.stderr)
        return 2

    try:
        payload = run_openalpr(args.openalpr_bin, args.country, image_path)
        plate = pick_best_plate(payload, args.min_confidence)

        if args.dry_run:
            print(f"[DRY-RUN] plate={plate}")
            return 0

        if args.transport == "webhook":
            post_plate_to_webhook(args.ha_webhook_url, plate)
            print(f"Posted plate {plate} to webhook {args.ha_webhook_url}")
        else:
            publish_plate(
                mqtt_host=args.mqtt_host,
                mqtt_port=args.mqtt_port,
                mqtt_topic=args.mqtt_topic,
                plate=plate,
                mosquitto_pub_bin=args.mosquitto_pub_bin,
            )
            print(f"Published plate {plate} to topic {args.mqtt_topic}")
        return 0
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
