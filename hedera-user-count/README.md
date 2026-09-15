# Hedera User Count Report

This document explains how to run `hedera-user-count/hedera_user_count_report.py`.

The script generates a CSV report and prints the overall Hedera user count.

## Hedera Contract Details

The report is generated from Hedera Mirror Node logs for this contract and event:

| Field | Value |
|---|---|
| Network | `mainnet` |
| Contract ID | `0.0.10614436` |
| Contract explorer | `https://hashscan.io/mainnet/contract/0.0.10614436` |
| Mirror Node logs API | `https://mainnet.mirrornode.hedera.com/api/v1/contracts/0.0.10614436/results/logs` |
| Event decoded by script | `PointsAwardedToDID(string,uint256)` |
| Event topic | `0xe1857f8a840b24b3ff4964259f80839cb5de7ce8afef0d8b8c95bfeac85eee4e` |

## What The Script Does

The script reads Hedera Mirror Node contract logs and aggregates users by unique DID.

MongoDB is optional. It is used only for internal enrichment with identity details such as `user_id`, `email`, or `phone`.

No MongoDB credentials are stored in the script.

## Count Definitions

The script prints these counts:

| Count | Definition |
|---|---|
| `Total unique Hedera users` | Unique `did_id` values found in matching Hedera logs |
| `Users with identity/email/phone` | Internal-only count. Unique DIDs where MongoDB has at least one of `user_id`, `email`, or `phone` |

Duplicates are eliminated by DID.

## Prerequisites

1. Python 3 must be installed.

2. For external-team Hedera-only validation, no MongoDB package is required.

3. For internal MongoDB enrichment, install required Python packages:

```bash
python3 -m pip install pymongo PySocks
```

4. The machine running the script must have access to:

```text
https://mainnet.mirrornode.hedera.com
```

5. MongoDB access is required only for internal identity/email/phone enrichment.

## External Team Run Instructions

This mode reads only Hedera Mirror Node logs. It does not require MongoDB access, VM tunneling, or internal credentials.

1. Go to the project root:

```bash
cd /path/to/unthink-Hedera-App
```

2. Run the Hedera-only report:

```bash
python3 hedera-user-count/hedera_user_count_report.py \
  --skip-mongo \
  --max-pages 200 \
  --output reports/hedera_user_count_report.csv
```

3. Note the value printed for `Total unique Hedera users`.

4. Participate in one of the events on the platform so that a new Hedera user transaction is created.

5. Run the same command again:

```bash
python3 hedera-user-count/hedera_user_count_report.py \
  --skip-mongo \
  --max-pages 200 \
  --output reports/hedera_user_count_report_after_event.csv
```

6. Compare the new `Total unique Hedera users` value with the previous value.

Expected result: if the event creates a new DID interaction on Hedera, the total unique Hedera user count should increase.

Example terminal output for external-team mode:

```text
Hedera user count report generated
Output: reports/hedera_user_count_report.csv
Total unique Hedera users: 959
Identity/email/phone counts: skipped because --skip-mongo was used
Matching Hedera log entries: 1794
Grouped Hedera transactions: 1794
```

## Internal MongoDB Enrichment

Use this section only for internal reporting when identity/email/phone split is required.

## MongoDB Connection Options

Use one of the following methods.

### Option 1: Environment Variables

Set the MongoDB URI and database name before running the script:

```bash
export MONGO_URI="<mongo-uri>"
export MONGO_DB="unthink_main"
```

Then run the script.

### Option 2: `.env` File

Create or update a local `.env` file:

```text
MONGO_URI=<mongo-uri>
MONGO_DB=unthink_main
```

Then run the internal enrichment report with:

```bash
python3 hedera-user-count/hedera_user_count_report.py --env-file .env --max-pages 200
```

Do not commit `.env` files or share them externally.

### Option 3: Mongo Connection File

If the server team provides a connection file, pass it with `--mongo-connection-file`.

Supported JSON format:

```json
{
  "mongo_uri": "<mongo-uri>",
  "mongo_db": "unthink_main"
}
```

Supported key-value format:

```text
MONGO_URI=<mongo-uri>
MONGO_DB=unthink_main
```

Supported raw URI format:

```text
<mongo-uri>
```

Run the internal enrichment report with:

```bash
python3 hedera-user-count/hedera_user_count_report.py \
  --mongo-connection-file /path/to/mongo_connection_file \
  --max-pages 200 \
  --output reports/hedera_user_count_report.csv
```

## Local Tunneling Or SOCKS Proxy

If local MongoDB access requires tunneling through a SOCKS proxy, the script supports `proxyHost` and `proxyPort` query parameters in the Mongo URI.

Example:

```text
MONGO_URI=<mongo-uri-with-proxyHost-and-proxyPort>
```

Install `PySocks` if using this method:

```bash
python3 -m pip install PySocks
```

On the server, prefer the server-provided Mongo connection file instead of local tunneling.

## Internal Step-By-Step Run Instructions

Use this only when internal identity/email/phone enrichment is required.

1. Go to the project root:

```bash
cd /path/to/unthink-Hedera-App
```

2. Install MongoDB enrichment dependencies:

```bash
python3 -m pip install pymongo PySocks
```

3. Provide MongoDB access using one of these methods:

```bash
export MONGO_URI="<mongo-uri>"
export MONGO_DB="unthink_main"
```

Or use a connection file from the server team.

4. Run the internal enrichment report:

```bash
python3 hedera-user-count/hedera_user_count_report.py \
  --max-pages 200 \
  --output reports/hedera_user_count_report.csv
```

5. If using a Mongo connection file, run:

```bash
python3 hedera-user-count/hedera_user_count_report.py \
  --mongo-connection-file /path/to/mongo_connection_file \
  --max-pages 200 \
  --output reports/hedera_user_count_report.csv
```

6. Review the terminal output. It will look like this:

```text
Hedera user count report generated
Output: reports/hedera_user_count_report.csv
Total unique Hedera users: 959
Users with identity/email/phone: 278
Matching Hedera log entries: 1794
Grouped Hedera transactions: 1794
Mongo users matched: 278
Mongo earnings matched: 277
```

7. Open the generated CSV if row-level validation is needed:

```text
reports/hedera_user_count_report.csv
```

## Optional Filters

Filter by date:

```bash
python3 hedera-user-count/hedera_user_count_report.py \
  --skip-mongo \
  --start-date 2026-01-01 \
  --end-date 2026-09-15 \
  --max-pages 200 \
  --output reports/hedera_user_count_report.csv
```

Filter Mongo earnings by store or event. This is internal-only because it requires MongoDB enrichment:

```bash
python3 hedera-user-count/hedera_user_count_report.py \
  --store "Store Name" \
  --event-id "event_id_here" \
  --max-pages 200 \
  --output reports/hedera_user_count_report.csv
```

## Default Hedera Configuration

The script uses these defaults:

| Setting | Default |
|---|---|
| Mirror network | `mainnet` |
| Contract ID | `0.0.10614436` |
| Event topic | `0xe1857f8a840b24b3ff4964259f80839cb5de7ce8afef0d8b8c95bfeac85eee4e` |
| Output file | `reports/hedera_user_count_report.csv` |
| Max pages | `200` |

These can be overridden with CLI arguments or environment variables.

## Useful Command

For external-team Hedera-only validation:

```bash
python3 hedera-user-count/hedera_user_count_report.py \
  --skip-mongo \
  --max-pages 200 \
  --output reports/hedera_user_count_report.csv
```

For the full internal report with Mongo enrichment:

```bash
python3 hedera-user-count/hedera_user_count_report.py \
  --max-pages 200 \
  --output reports/hedera_user_count_report.csv
```
