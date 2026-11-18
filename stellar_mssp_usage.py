#!/usr/bin/env python3
"""
Stellar MSSP usage metering - count StellarOne license seats per customer.

Model: one shared StellarOne, one seat pool, one top-level agent group per
end customer. This script reads agents and groups through the StellarOne
OpenAPI (v2.1, read-only calls only), maps every agent to its customer group
and counts what each customer uses.

Commands
  snapshot   Take today's count. Prints a table, writes a CSV per customer and a
             CSV per agent, and (with --history-dir) stores the snapshot so
             monthly figures can be calculated later. Run it once a day.
  month      Build a monthly billing report from stored snapshots:
             peak and average billable seats per customer for YYYY-MM.

Configuration (environment variables)
  STELLARONE_URL       Console base URL, e.g. https://stellarone.mssp.example
  STELLARONE_API_KEY   API key (Account Management > Generate API Key).
                       A Viewer account with access to all groups is enough.
  STELLARONE_CA_FILE   Optional: console certificate if it is self-signed
  STELLARONE_INSECURE  Optional: 1 = skip TLS verification (lab only)

Python 3.8+, standard library only.
"""
import argparse
import collections
import csv
import datetime as dt
import glob
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UNGROUPED = "(ungrouped)"
PLATFORM = {"protect": "Windows", "enforce": "Windows-Legacy", "splnx": "Linux"}

# Usage classes, in the order they are reported.
BILLABLE = "billable"          # holds an ICS/Kiosk/OEM seat and is licensed
GRACE = "grace"                # holds a seat, license in grace period (also billable)
DISCOVER = "discover"          # Discover/sensor edition agent
INACTIVE = "inactive"          # registered but holds no seat (over-seat / not activated)
EXPIRED = "expired"            # license expired on the agent
CLASSES = [BILLABLE, GRACE, DISCOVER, INACTIVE, EXPIRED]
LICENSE_ALERT_DAYS = 60


# --------------------------------------------------------------------------- API

class ApiError(Exception):
    pass


def _ssl_context():
    if os.environ.get("STELLARONE_INSECURE") == "1":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    ca = os.environ.get("STELLARONE_CA_FILE")
    return ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()


def api_get(path, query=None, retries=4):
    base = os.environ.get("STELLARONE_URL", "").rstrip("/")
    key = os.environ.get("STELLARONE_API_KEY", "")
    if not base or not key:
        raise ApiError("Set STELLARONE_URL and STELLARONE_API_KEY first.")
    url = base + path
    if query:
        url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
    req = urllib.request.Request(url, headers={"Authorization": key, "Accept": "application/json"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, context=_ssl_context(), timeout=60) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:      # rate limited: back off and retry
                e.close()
                time.sleep(2 ** attempt)
                continue
            rid = e.headers.get("x-stellar-request-id")
            hint = {401: "API key missing, invalid or expired.",
                    403: "The API key's account lacks access (check role / Group Control)."}
            raise ApiError(f"HTTP {e.code} GET {path} {hint.get(e.code, '')}\n"
                           f"request-id: {rid}\n{e.read().decode(errors='replace')}")
        except urllib.error.URLError as e:
            raise ApiError(f"Cannot reach {url}: {e.reason}")


def fetch_all(path, key, query=None, page_size=200):
    """Follow pageToken / page pagination until every item is read."""
    query = dict(query or {}, limit=page_size)
    items, page = [], 1
    while True:
        data = api_get(path, query)
        batch = data.get(key) or []
        items.extend(batch)
        pg = data.get("pagination") or {}
        total = int(pg.get("total") or 0)
        if not batch or (total and len(items) >= total):
            return items
        if pg.get("pageToken"):
            query["pageToken"] = pg["pageToken"]
            query.pop("page", None)
        elif total:
            page += 1
            query["page"] = page
        else:
            return items


def fetch_agents():
    """All agents. Inactive (seat-less) agents are fetched explicitly as well, in
    case the default listing leaves them out; duplicates are merged by UUID."""
    agents = {}
    for state in (None, "AGENT_STATE_INACTIVATED"):
        q = {"agentState": state} if state else None
        for a in fetch_all("/api/v1/agents", "agents", q):
            agents.setdefault(a.get("agentUuid"), a)
    return list(agents.values())


# --------------------------------------------------------------------------- logic

def customer_resolver(groups, level):
    """Return f(groupUuid) -> customer name.

    level 0 = the root group itself is the customer; level 1 (default) = each
    direct child of the root group is a customer; level 2 = grandchildren, ...
    Agents sitting above the customer level are reported under the name of the
    group they sit in, prefixed with '(above customer level)'.
    """
    by_id = {g["groupUuid"]: g for g in groups if g.get("groupUuid")}

    def chain(gid):
        path, seen = [], set()
        while gid and gid in by_id and gid not in seen:
            seen.add(gid)
            path.append(by_id[gid])
            gid = by_id[gid].get("parentGroupUuid")
        return list(reversed(path))          # root first

    def resolve(gid):
        if not gid or gid not in by_id:
            return UNGROUPED
        path = chain(gid)
        if len(path) > level:
            return path[level].get("name") or path[level]["groupUuid"]
        return "(above customer level) " + (path[-1].get("name") or path[-1]["groupUuid"])

    return resolve


def classify(agent):
    if agent.get("agentEdition") == "AGENT_EDITION_SENSOR":
        return DISCOVER
    lic = agent.get("licenseStatus")
    if lic == "LICENSE_STATUS_EXPIRED":
        return EXPIRED
    if agent.get("activated") is False or lic in (None, "LICENSE_STATUS_NOTACTIVATED"):
        return INACTIVE
    if lic == "LICENSE_STATUS_GRACE_PERIOD":
        return GRACE
    return BILLABLE


def load_quotas(path):
    """CSV with columns customer,max_seats -> {customer: int}."""
    if not path:
        return {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {r["customer"].strip(): int(r["max_seats"]) for r in csv.DictReader(f)
                if r.get("customer") and r.get("max_seats")}


def summarize(agents, resolve):
    rows, per_customer = [], {}
    for a in agents:
        cust = resolve(a.get("groupUuid"))
        cls = classify(a)
        c = per_customer.setdefault(cust, {k: 0 for k in CLASSES})
        c[cls] += 1
        rows.append({
            "customer": cust,
            "class": cls,
            "hostname": a.get("hostname"),
            "ip": a.get("ipAddress"),
            "platform": PLATFORM.get(a.get("productCode"), a.get("productCode")),
            "edition": (a.get("agentEdition") or "").replace("AGENT_EDITION_", ""),
            "license_status": (a.get("licenseStatus") or "").replace("LICENSE_STATUS_", ""),
            "license_type": (a.get("licenseType") or "").replace("LICENSE_TYPE_", ""),
            "license_expires": _date(a.get("licenseExpiredAt")),
            "online": a.get("agentOnlineStatus"),
            "agent_uuid": a.get("agentUuid"),
            "group_uuid": a.get("groupUuid"),
        })
    return per_customer, rows


def _date(value):
    """Return an API epoch (seconds/milliseconds) or ISO value as YYYY-MM-DD."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        if not isinstance(value, str) or not value.strip():
            return ""
        try:
            return dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return ""
    if v <= 0:
        return ""
    if v > 10 ** 11:                         # milliseconds
        v //= 1000
    return dt.datetime.fromtimestamp(v, dt.timezone.utc).strftime("%Y-%m-%d")


def console_license_expiry(agents):
    """Infer the console license end date from agents' licenseExpiredAt values.

    Prefer active, non-sensor, full-license agents. If none expose an expiry,
    fall back to any non-sensor agent. The most commonly reported date wins,
    because a console is expected to have one primary license end date.
    """
    protection = [a for a in agents if a.get("agentEdition") != "AGENT_EDITION_SENSOR"]
    preferred = [a for a in protection
                 if a.get("activated") is not False
                 and a.get("licenseStatus") not in
                 ("LICENSE_STATUS_EXPIRED", "LICENSE_STATUS_NOTACTIVATED")
                 and a.get("licenseType") != "LICENSE_TYPE_TRIAL"]

    def dates(items):
        result = []
        for agent in items:
            value = _date(agent.get("licenseExpiredAt"))
            if value:
                result.append(value)
        return result

    values = dates(preferred) or dates(protection)
    if not values:
        return None
    counts = collections.Counter(values)
    selected = sorted(counts, key=lambda value: (-counts[value], value))[0]
    return dt.date.fromisoformat(selected)


def license_expiry_summary(agents, today=None):
    """Return the report line and optional 60-day expiry alert."""
    today = today or dt.date.today()
    expiry = console_license_expiry(agents)
    if not expiry:
        return None, None
    days = (expiry - today).days
    unit = "day" if abs(days) == 1 else "days"
    if days >= 0:
        line = f"License expires {expiry.isoformat()} ({days} {unit})"
    else:
        line = f"License expired {expiry.isoformat()} ({abs(days)} {unit} ago)"

    if days < 0:
        alert = (f"LICENSE: expired {abs(days)} {unit} ago on {expiry.isoformat()} "
                 "- renew immediately")
    elif days == 0:
        alert = f"LICENSE: expires today ({expiry.isoformat()}) - renew immediately"
    elif days <= LICENSE_ALERT_DAYS:
        alert = (f"LICENSE: expires in {days} {unit} on {expiry.isoformat()} "
                 "- renew before expiry")
    else:
        alert = None
    return line, alert


def write_csv(path, rows, fields):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def print_table(headers, rows):
    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows else len(str(h))
              for i, h in enumerate(headers)]
    line = "  ".join(str(h).ljust(w) for h, w in zip(headers, widths))
    print(line)
    print("-" * len(line))
    for r in rows:
        print("  ".join(str(v).ljust(w) for v, w in zip(r, widths)))


# --------------------------------------------------------------------------- commands

def cmd_snapshot(args):
    groups = fetch_all("/api/v1/groups", "groups")
    agents = fetch_agents()
    per_customer, detail = summarize(agents, customer_resolver(groups, args.customer_level))
    quotas = load_quotas(args.quotas)
    today = dt.date.today().isoformat()

    total_billable = sum(c[BILLABLE] + c[GRACE] for c in per_customer.values())
    summary, alerts = [], []
    license_line, license_alert = license_expiry_summary(agents, dt.date.fromisoformat(today))
    if license_alert:
        alerts.append(license_alert)
    for cust in sorted(per_customer, key=lambda k: (k == UNGROUPED, k.lower())):
        c = per_customer[cust]
        billable = c[BILLABLE] + c[GRACE]
        quota = quotas.get(cust)
        row = {"date": today, "customer": cust, "billable_seats": billable,
               **{k: c[k] for k in CLASSES},
               "total_agents": sum(c.values()),
               "quota": quota if quota is not None else "",
               "over_quota": max(0, billable - quota) if quota is not None else ""}
        summary.append(row)
        if quota is not None and billable > quota:
            alerts.append(f"{cust}: {billable} billable seats, quota {quota} (+{billable - quota})")
        if c[INACTIVE]:
            alerts.append(f"{cust}: {c[INACTIVE]} agent(s) without a seat - not protected")
        if c[EXPIRED]:
            alerts.append(f"{cust}: {c[EXPIRED]} agent(s) with an expired license")

    headers = ["Customer", "Billable", "of which grace", "Discover", "Inactive", "Expired",
               "Total", "Quota"]
    print(f"\nStellarOne usage per customer - {today}  ({os.environ.get('STELLARONE_URL')})\n")
    if license_line:
        print(license_line + "\n")
    print_table(headers, [[r["customer"], r["billable_seats"], r[GRACE], r[DISCOVER],
                           r[INACTIVE], r[EXPIRED], r["total_agents"], r["quota"]]
                          for r in summary])
    print(f"\nBillable seats in use: {total_billable}", end="")
    if args.pool_size:
        free = args.pool_size - total_billable
        print(f" of {args.pool_size} purchased ({free} free, "
              f"{100 * total_billable / args.pool_size:.0f}% used)", end="")
        if free < 0:
            alerts.append(f"POOL: {-free} more agents than purchased seats - those agents go Inactive")
        elif free < args.pool_size * 0.1:
            alerts.append(f"POOL: less than 10% of the seat pool left ({free} seats)")
    print()
    if alerts:
        print("\nAlerts:")
        for a in alerts:
            print("  ! " + a)

    out = args.out_dir
    sum_fields = list(summary[0].keys()) if summary else ["date", "customer"]
    write_csv(os.path.join(out, f"usage_by_customer_{today}.csv"), summary, sum_fields)
    det_fields = list(detail[0].keys()) if detail else ["customer"]
    write_csv(os.path.join(out, f"usage_by_agent_{today}.csv"),
              sorted(detail, key=lambda r: (r["customer"], str(r["hostname"]))), det_fields)
    print(f"\nWrote {out}/usage_by_customer_{today}.csv and usage_by_agent_{today}.csv")

    if args.history_dir:
        write_csv(os.path.join(args.history_dir, f"snapshot_{today}.csv"), summary, sum_fields)
        print(f"Stored snapshot in {args.history_dir}/snapshot_{today}.csv")

    return 2 if (args.fail_on_alert and alerts) else 0


def cmd_month(args):
    files = sorted(glob.glob(os.path.join(args.history_dir, f"snapshot_{args.month}-*.csv")))
    if not files:
        raise ApiError(f"No snapshots for {args.month} in {args.history_dir}. "
                       "Run 'snapshot --history-dir ...' daily first.")
    days = len(files)
    data = {}
    for f in files:
        with open(f, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                d = data.setdefault(r["customer"], [])
                d.append(int(r["billable_seats"]))
    rows = []
    for cust in sorted(data, key=lambda k: (k == UNGROUPED, k.lower())):
        vals = data[cust] + [0] * (days - len(data[cust]))   # absent days count as 0
        rows.append({"month": args.month, "customer": cust,
                     "peak_seats": max(vals),
                     "average_seats": round(sum(vals) / days, 1),
                     "last_seats": data[cust][-1] if data[cust] else 0,
                     "days_measured": days})
    print(f"\nBilling basis for {args.month} ({days} daily snapshots)\n")
    print_table(["Customer", "Peak", "Average", "Last", "Days"],
                [[r["customer"], r["peak_seats"], r["average_seats"], r["last_seats"],
                  r["days_measured"]] for r in rows])
    path = os.path.join(args.out_dir, f"billing_{args.month}.csv")
    write_csv(path, rows, list(rows[0].keys()))
    print(f"\nWrote {path}")
    return 0


def main():
    p = argparse.ArgumentParser(description="Stellar MSSP usage metering per customer group")
    sp = p.add_subparsers(dest="cmd", required=True)

    s = sp.add_parser("snapshot", help="Count today's usage per customer")
    s.add_argument("--customer-level", type=int, default=1,
                   help="Group depth that represents a customer: 0 = root group, "
                        "1 = children of the root (default), 2 = grandchildren")
    s.add_argument("--pool-size", type=int, help="Seats purchased for the console (for headroom)")
    s.add_argument("--quotas", help="CSV with columns customer,max_seats")
    s.add_argument("--out-dir", default="reports")
    s.add_argument("--history-dir", help="Store a dated snapshot here for the monthly report")
    s.add_argument("--fail-on-alert", action="store_true",
                   help="Exit code 2 when there are alerts (for schedulers / monitoring)")

    m = sp.add_parser("month", help="Monthly billing basis from stored snapshots")
    m.add_argument("month", help="YYYY-MM")
    m.add_argument("--history-dir", required=True)
    m.add_argument("--out-dir", default="reports")

    args = p.parse_args()
    try:
        sys.exit(cmd_snapshot(args) if args.cmd == "snapshot" else cmd_month(args))
    except ApiError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
