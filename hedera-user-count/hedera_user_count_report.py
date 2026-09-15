#!/usr/bin/env python3
"""
Generate a Hedera user count report from Mirror Node logs.

Hedera Mirror Node logs are treated as the source of truth for unique users.
MongoDB is used only to enrich each DID with identity/email/phone details.

No MongoDB credentials are stored in this file. Provide them through an env var
or a connection file. See hedera-user-count/README.md.
"""

import argparse
import csv
import datetime as dt
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


DEFAULT_MIRROR_NETWORK = "mainnet"
DEFAULT_SESSION_HCS20_CONTRACT_ID = "0.0.10614436"
DEFAULT_POINTS_AWARDED_TO_DID_TOPIC = (
    "0xe1857f8a840b24b3ff4964259f80839cb5de7ce8afef0d8b8c95bfeac85eee4e"
)
DEFAULT_MONGO_DB = "unthink_main"


def load_env_file(path):
    if not path:
        return

    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def read_connection_file(path):
    """Read Mongo connection details from JSON, KEY=VALUE, or raw URI file."""
    if not path:
        return "", ""

    connection_path = Path(path)
    if not connection_path.exists():
        raise FileNotFoundError(f"Mongo connection file not found: {path}")

    content = connection_path.read_text(encoding="utf-8").strip()
    if not content:
        return "", ""

    if content.startswith("{"):
        data = json.loads(content)
        mongo_uri = data.get("mongo_uri") or data.get("MONGO_URI") or ""
        mongo_db = data.get("mongo_db") or data.get("MONGO_DB") or ""
        return mongo_uri.strip(), mongo_db.strip()

    values = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")

    if values:
        return values.get("MONGO_URI", ""), values.get("MONGO_DB", "")

    return content, ""


def parse_date(value):
    if not value:
        return None
    normalized = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError:
        parsed = dt.datetime.strptime(str(value).strip(), "%Y-%m-%d")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return parsed


def hedera_timestamp_to_datetime(timestamp):
    if not timestamp:
        return None
    try:
        seconds = int(str(timestamp).split(".")[0])
        return dt.datetime.utcfromtimestamp(seconds)
    except Exception:
        return None


def datetime_to_iso(value):
    if not value:
        return ""
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def decode_did_points_log_data(data_hex):
    """Decode ABI data for event PointsAwardedToDID(string,uint256)."""
    raw = str(data_hex or "")
    if raw.startswith("0x"):
        raw = raw[2:]
    if len(raw) < 192:
        return None

    try:
        offset = int(raw[0:64], 16)
        amount = int(raw[64:128], 16)
        length_start = offset * 2
        string_length = int(raw[length_start:length_start + 64], 16)
        string_start = length_start + 64
        string_end = string_start + (string_length * 2)
        did = bytes.fromhex(raw[string_start:string_end]).decode("utf-8").strip()
    except Exception:
        return None

    if not did.startswith("did:"):
        return None

    return did, amount


def fetch_json(url, params=None, timeout=12):
    request_url = url
    if params:
        request_url = f"{url}?{urlencode(params)}"
    request = Request(request_url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_non_empty_string(data, keys):
    if not isinstance(data, dict):
        return ""
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        value = str(value).strip()
        if value:
            return value
    return ""


def get_hedera_registration_points(points_summary):
    if not isinstance(points_summary, dict):
        return 0, ""

    sources = points_summary.get("sources") or points_summary.get("points_source") or {}
    if isinstance(sources, dict) and sources:
        numeric_sources = {
            str(key): value
            for key, value in sources.items()
            if isinstance(value, (int, float)) and value > 0
        }
        if numeric_sources:
            return sum(numeric_sources.values()), "; ".join(sorted(numeric_sources.keys()))

    total_earned = points_summary.get("total_earned")
    if isinstance(total_earned, (int, float)) and total_earned > 0:
        return total_earned, "total_earned"

    return 0, ""


def fetch_hedera_did_transactions(args):
    base_url = f"https://{args.network}.mirrornode.hedera.com"
    next_path = f"/api/v1/contracts/{args.contract_id}/results/logs"
    params = {"limit": args.limit, "order": "desc"}
    start_date = parse_date(args.start_date)
    end_date = parse_date(args.end_date)
    did_map = {}
    total_matching_logs = 0

    for page_number in range(args.max_pages):
        payload = fetch_json(f"{base_url}{next_path}", params=params, timeout=args.timeout)

        for log in payload.get("logs", []):
            topics = log.get("topics") or []
            if not topics or str(topics[0]).lower() != args.event_topic.lower():
                continue

            log_datetime = hedera_timestamp_to_datetime(log.get("timestamp"))
            if start_date and log_datetime and log_datetime < start_date:
                continue
            if end_date and log_datetime and log_datetime > end_date:
                continue

            decoded = decode_did_points_log_data(log.get("data"))
            if not decoded:
                continue

            did, amount = decoded
            transaction_hash = log.get("transaction_hash", "")
            transaction_id = (
                log.get("transaction_id")
                or log.get("transactionId")
                or log.get("transaction_id_string")
                or ""
            )
            timestamp_iso = datetime_to_iso(log_datetime)
            total_matching_logs += 1

            entry = did_map.setdefault(did, {
                "did_id": did,
                "hedera_total_points": 0,
                "transactions": [],
            })
            entry["hedera_total_points"] += amount
            entry["transactions"].append({
                "amount": amount,
                "timestamp": timestamp_iso,
                "transaction_hash": transaction_hash,
                "transaction_id": transaction_id,
            })

        next_path = payload.get("links", {}).get("next")
        if not next_path:
            break
        params = None

        if args.verbose:
            print(f"Fetched Hedera log page {page_number + 1}; unique DIDs so far: {len(did_map)}")

    for entry in did_map.values():
        entry["transactions"].sort(key=lambda tx: tx.get("timestamp") or "")
        timestamps = [tx["timestamp"] for tx in entry["transactions"] if tx.get("timestamp")]
        hashes = []
        transaction_ids = []
        for tx in entry["transactions"]:
            if tx.get("transaction_hash") and tx["transaction_hash"] not in hashes:
                hashes.append(tx["transaction_hash"])
            if tx.get("transaction_id") and tx["transaction_id"] not in transaction_ids:
                transaction_ids.append(tx["transaction_id"])

        entry["hedera_transaction_count"] = len(entry["transactions"])
        entry["hedera_transaction_hashes"] = hashes
        entry["hedera_transaction_ids"] = transaction_ids
        entry["hedera_first_transaction_at"] = timestamps[0] if timestamps else ""
        entry["hedera_last_transaction_at"] = timestamps[-1] if timestamps else ""
        entry["last_hedera_transaction_hash"] = hashes[-1] if hashes else ""
        entry["last_hedera_transaction_id"] = transaction_ids[-1] if transaction_ids else ""

    return did_map, total_matching_logs


def clean_mongo_uri_and_apply_proxy(mongo_uri):
    import socket

    parsed = urlparse(mongo_uri)
    query_params = {k.lower(): v[0] for k, v in parse_qs(parsed.query).items()}
    proxy_host = query_params.get("proxyhost")
    proxy_port = query_params.get("proxyport")

    if proxy_host and proxy_port:
        try:
            import socks
            socks.set_default_proxy(socks.SOCKS5, proxy_host, int(proxy_port))
            socket.socket = socks.socksocket
        except ImportError:
            print("Warning: SOCKS5 proxy configured but 'PySocks' is not installed.", file=sys.stderr)

    if "?" not in mongo_uri:
        return mongo_uri

    base, query = mongo_uri.split("?", 1)
    clean_params = [
        param for param in query.split("&")
        if not param.lower().startswith("proxyhost=") and not param.lower().startswith("proxyport=")
    ]
    if clean_params:
        return f"{base}?{'&'.join(clean_params)}"
    return base


def get_mongo_config(args):
    file_uri, file_db = read_connection_file(args.mongo_connection_file)
    mongo_uri = file_uri or os.getenv(args.mongo_uri_env, "")
    mongo_db = args.mongo_db or file_db or os.getenv("MONGO_DB", "") or DEFAULT_MONGO_DB
    return mongo_uri.strip(), mongo_db.strip()


def build_mongo_client(args):
    import pymongo

    mongo_uri, mongo_db = get_mongo_config(args)
    if not mongo_uri:
        raise RuntimeError(
            "MongoDB URI is required for identity/email/phone counts. "
            "Set MONGO_URI, pass --mongo-connection-file, or use --skip-mongo."
        )

    clean_uri = clean_mongo_uri_and_apply_proxy(mongo_uri)
    client = pymongo.MongoClient(clean_uri)
    client.admin.command("ping")
    return client, mongo_db


def fetch_mongo_enrichment(dids, args):
    if args.skip_mongo or not dids:
        return {}, {}

    client, mongo_db = build_mongo_client(args)
    db = client[mongo_db]
    users_info_col = db["users_info"]
    user_earnings_col = db["user_earnings"]

    users = list(users_info_col.find(
        {
            "$or": [
                {"userDID": {"$in": dids}},
                {"user_did": {"$in": dids}},
                {"did_id": {"$in": dids}},
            ]
        },
        {
            "_id": 0,
            "user_id": 1,
            "emailId": 1,
            "email": 1,
            "phone": 1,
            "first_name": 1,
            "last_name": 1,
            "user_name": 1,
            "userDID": 1,
            "user_did": 1,
            "did_id": 1,
        },
    ))

    users_by_did = {}
    user_ids = []
    for user in users:
        did = get_non_empty_string(user, ("userDID", "user_did", "did_id"))
        if not did:
            continue
        users_by_did.setdefault(did, user)
        if user.get("user_id"):
            user_ids.append(user["user_id"])

    earnings_by_user_id = {}
    if user_ids:
        query = {"user_id": {"$in": list(set(user_ids))}}
        if args.store:
            query["store_name"] = args.store
        if args.event_id:
            query["event_id"] = args.event_id
        date_range = {}
        if args.start_date:
            date_range["$gte"] = args.start_date
        if args.end_date:
            date_range["$lte"] = args.end_date
        if date_range:
            query["last_updated"] = date_range

        earnings = list(user_earnings_col.find(
            query,
            {
                "_id": 0,
                "user_id": 1,
                "store_name": 1,
                "event_id": 1,
                "last_updated": 1,
                "points_summary": 1,
            },
        ))
        for earning in earnings:
            earnings_by_user_id.setdefault(earning.get("user_id"), []).append(earning)

    return users_by_did, earnings_by_user_id


def aggregate_earnings(earnings):
    total_points = 0
    points_types = []
    stores = []
    event_ids = []
    last_updated_values = []

    for earning in earnings or []:
        points, points_type = get_hedera_registration_points(earning.get("points_summary"))
        total_points += points
        if points_type and points_type not in points_types:
            points_types.append(points_type)
        store = earning.get("store_name")
        event_id = earning.get("event_id")
        last_updated = earning.get("last_updated")
        if store and store not in stores:
            stores.append(store)
        if event_id and event_id not in event_ids:
            event_ids.append(event_id)
        if last_updated:
            last_updated_values.append(str(last_updated))

    return {
        "points": total_points,
        "points_type": "; ".join(points_types),
        "store_name": "; ".join(stores),
        "event_id": "; ".join(event_ids),
        "registered_at": min(last_updated_values) if last_updated_values else "",
    }


def build_rows(did_map, users_by_did, earnings_by_user_id):
    rows = []
    for did, hedera_activity in sorted(did_map.items()):
        user = users_by_did.get(did, {})
        user_id = user.get("user_id", "")
        earnings = earnings_by_user_id.get(user_id, []) if user_id else []
        earnings_summary = aggregate_earnings(earnings)
        full_name = " ".join(filter(None, [
            get_non_empty_string(user, ("first_name",)),
            get_non_empty_string(user, ("last_name",)),
        ])).strip() or get_non_empty_string(user, ("user_name",))
        has_identity_email_phone = any([
            str(user_id).strip(),
            get_non_empty_string(user, ("emailId", "email")),
            get_non_empty_string(user, ("phone",)),
        ])

        rows.append({
            "did_id": did,
            "user_id": user_id,
            "name": full_name,
            "email": get_non_empty_string(user, ("emailId", "email")),
            "phone": get_non_empty_string(user, ("phone",)),
            "has_identity_email_phone": "yes" if has_identity_email_phone else "no",
            "event_id": earnings_summary["event_id"],
            "store_name": earnings_summary["store_name"],
            "points": earnings_summary["points"],
            "points_type": earnings_summary["points_type"],
            "hedera_total_points": hedera_activity.get("hedera_total_points", 0),
            "hedera_transaction_count": hedera_activity.get("hedera_transaction_count", 0),
            "hedera_transaction_ids": "; ".join(hedera_activity.get("hedera_transaction_ids", [])),
            "hedera_transaction_hashes": "; ".join(hedera_activity.get("hedera_transaction_hashes", [])),
            "last_hedera_transaction_id": hedera_activity.get("last_hedera_transaction_id", ""),
            "last_hedera_transaction_hash": hedera_activity.get("last_hedera_transaction_hash", ""),
            "hedera_first_transaction_at": hedera_activity.get("hedera_first_transaction_at", ""),
            "hedera_last_transaction_at": hedera_activity.get("hedera_last_transaction_at", ""),
            "registered_at": earnings_summary["registered_at"],
            "mongo_user_found": "yes" if user else "no",
            "mongo_earnings_found": "yes" if earnings else "no",
        })
    return rows


def write_csv(rows, output_path):
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "did_id",
        "user_id",
        "name",
        "email",
        "phone",
        "has_identity_email_phone",
        "event_id",
        "store_name",
        "points",
        "points_type",
        "hedera_total_points",
        "hedera_transaction_count",
        "hedera_transaction_ids",
        "hedera_transaction_hashes",
        "last_hedera_transaction_id",
        "last_hedera_transaction_hash",
        "hedera_first_transaction_at",
        "hedera_last_transaction_at",
        "registered_at",
        "mongo_user_found",
        "mongo_earnings_found",
    ]
    with output.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def print_summary(rows, total_matching_logs, output_path, skip_mongo=False):
    total_unique_users = len(rows)
    users_with_identity = sum(1 for row in rows if row["has_identity_email_phone"] == "yes")
    mongo_user_matches = sum(1 for row in rows if row["mongo_user_found"] == "yes")
    mongo_earning_matches = sum(1 for row in rows if row["mongo_earnings_found"] == "yes")
    total_transactions = sum(int(row["hedera_transaction_count"] or 0) for row in rows)

    print("Hedera user count report generated")
    print(f"Output: {output_path}")
    print(f"Total unique Hedera users: {total_unique_users}")
    if skip_mongo:
        print("Identity/email/phone counts: skipped because --skip-mongo was used")
    else:
        print(f"Users with identity/email/phone: {users_with_identity}")
    print(f"Matching Hedera log entries: {total_matching_logs}")
    print(f"Grouped Hedera transactions: {total_transactions}")
    if not skip_mongo:
        print(f"Mongo users matched: {mongo_user_matches}")
        print(f"Mongo earnings matched: {mongo_earning_matches}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate unique Hedera user counts with optional Mongo identity enrichment.",
    )
    parser.add_argument("--output", default="reports/hedera_user_count_report.csv")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--network", default=os.getenv("HEDERA_MIRROR_NETWORK") or DEFAULT_MIRROR_NETWORK)
    parser.add_argument("--contract-id", default=os.getenv("SESSION_HCS20_CONTRACT_ID") or DEFAULT_SESSION_HCS20_CONTRACT_ID)
    parser.add_argument("--event-topic", default=os.getenv("POINTS_AWARDED_TO_DID_TOPIC") or DEFAULT_POINTS_AWARDED_TO_DID_TOPIC)
    parser.add_argument("--limit", type=int, default=int(os.getenv("HEDERA_LOG_FETCH_LIMIT") or "100"))
    parser.add_argument("--max-pages", type=int, default=int(os.getenv("HEDERA_LOG_MAX_PAGES") or "200"))
    parser.add_argument("--timeout", type=int, default=12)
    parser.add_argument("--store", default="", help="Optional Mongo earnings store_name filter")
    parser.add_argument("--event-id", default="", help="Optional Mongo earnings event_id filter")
    parser.add_argument("--start-date", default="", help="Optional ISO/date filter for Hedera log timestamps and Mongo last_updated")
    parser.add_argument("--end-date", default="", help="Optional ISO/date filter for Hedera log timestamps and Mongo last_updated")
    parser.add_argument("--skip-mongo", action="store_true", help="Only output Hedera DID data; skip identity/email/phone enrichment")
    parser.add_argument("--mongo-uri-env", default="MONGO_URI", help="Environment variable name that contains the MongoDB URI")
    parser.add_argument("--mongo-db", default="", help="MongoDB database name. Defaults to MONGO_DB or unthink_main")
    parser.add_argument("--mongo-connection-file", default="", help="File containing MongoDB connection details")
    parser.add_argument("--verbose", action="store_true")
    return parser.parse_args()


def get_env_file_arg(default=".env"):
    for index, arg in enumerate(sys.argv[1:]):
        if arg == "--env-file" and index + 2 <= len(sys.argv[1:]):
            return sys.argv[index + 2]
        if arg.startswith("--env-file="):
            return arg.split("=", 1)[1]
    return default


def main():
    load_env_file(get_env_file_arg())
    args = parse_args()
    load_env_file(args.env_file)

    did_map, total_matching_logs = fetch_hedera_did_transactions(args)
    users_by_did, earnings_by_user_id = fetch_mongo_enrichment(list(did_map.keys()), args)
    rows = build_rows(did_map, users_by_did, earnings_by_user_id)
    write_csv(rows, args.output)
    print_summary(rows, total_matching_logs, args.output, args.skip_mongo)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("Interrupted")
