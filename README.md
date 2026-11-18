# Stellar MSSP Usage Metering

A guide and tool for Managed Security Service Providers (MSSPs) that deliver
TXOne Stellar OT endpoint protection to several end customers from one shared
StellarOne console, and need to know, every month, how many license seats each
customer used.

## Important disclaimer

> [!IMPORTANT]
> **This is an independent, unofficial community project. It is not a TXOne
> Networks product and has not been developed, reviewed, certified, approved,
> endorsed or supported by TXOne Networks.** TXOne Networks and StellarOne are
> trademarks or product names of their respective owner. Their use here is only
> to identify the systems with which this project is intended to interoperate.

This project is provided solely as an example and a source of inspiration. The
script has only been tested against a simulated API, not a production
StellarOne console. You must independently review, test and validate the code,
API behaviour, classification rules, reports and commercial assumptions against
your own environment, contracts and current TXOne documentation before using
them for operations, monitoring, licensing or billing.

Use of this project is entirely at your own risk. To the maximum extent
permitted by applicable law, the author and contributors make no representations
or warranties regarding its accuracy, completeness, reliability, security or
fitness for a particular purpose, and accept no liability for errors, omissions,
incorrect usage figures or invoices, license shortages, unprotected endpoints,
service interruption, data loss or any other direct or indirect loss arising
from its use or modification.

You may copy, modify and redistribute the original code and documentation in
this repository under the [MIT License](LICENSE). You are responsible for every
copy or modified version you use or distribute, and must preserve the copyright
and license notices. This permission does not grant rights to TXOne proprietary
software, documentation, trademarks or other third-party material.

- **Tool:** `stellar_mssp_usage.py`. Python 3.8+, standard library only, read-only.
- **Applies to:** StellarOne with OpenAPI 2.1 (StellarOne 3.x / 5.x).
- **Status:** the script has been tested against a simulated API only. Validate it
  against your own console before you use it for billing (see
  [Validate before go-live](#12-validate-before-go-live)).

---

## Contents

1. [The MSSP operating model](#1-the-mssp-operating-model)
2. [How Stellar licensing works and what it means for an MSSP](#2-how-stellar-licensing-works-and-what-it-means-for-an-mssp)
3. [Designing the StellarOne console for multiple customers](#3-designing-the-stellarone-console-for-multiple-customers)
4. [Installing and configuring the tool](#4-installing-and-configuring-the-tool)
5. [Daily snapshot](#5-daily-snapshot)
6. [Monthly billing report](#6-monthly-billing-report)
7. [Reports and fields](#7-reports-and-fields)
8. [Quotas, pool headroom and alerts](#8-quotas-pool-headroom-and-alerts)
9. [Scheduling](#9-scheduling)
10. [Operational procedures](#10-operational-procedures)
11. [Limitations](#11-limitations)
12. [Validate before go-live](#12-validate-before-go-live)
13. [Troubleshooting](#13-troubleshooting)
14. [Security](#14-security)

---

## 1. The MSSP operating model

```
                    ┌────────────────────────── StellarOne (operated by the MSSP) ──────────────────────────┐
                    │  License: one seat pool (e.g. 500 x StellarICS)                                       │
                    │                                                                                        │
                    │  All Agents (root group)                                                               │
                    │   ├── Customer A   ← Group Control: customer A's users see only this group             │
                    │   │    ├── Site Aarhus                                                                 │
                    │   │    └── Line 3 HMIs                                                                 │
                    │   ├── Customer B                                                                       │
                    │   └── Customer C                                                                       │
                    └────────────────────────────────────────────────────────────────────────────────────────┘
                                    ▲                                   │
                     agents in each customer's OT network      stellar_mssp_usage.py (daily, read-only API)
                                                                        ▼
                                                    usage per customer → monthly billing basis
```

- The MSSP buys **one seat pool** and runs **one StellarOne console**, on its own
  premises or in its own cloud tenant.
- Each end customer is **one top-level agent group** under the root group. Sites,
  lines and other sub-groups go under the customer group.
- Seats are **not tied to machines or groups**. A seat freed at customer A (an agent is
  removed) can be used by customer B straight away. This is the "floating pool".
- The MSSP **bills its customers monthly per seat used**. The tool provides the
  figures, because StellarOne itself cannot report usage per customer or cap it.
- The MSSP's margin is mainly in the **managed services** around the license, for example:
  - managed Application Lockdown and approved-list maintenance
  - maintenance-mode windows for patching
  - monthly vulnerability reports
  - forwarding events to the MSSP SOC (syslog / Trend Vision One)

### When to use a dedicated console instead

A shared console is not suitable for every customer. Use a separate StellarOne,
with its own license, when:

| Situation | Why a dedicated console |
|---|---|
| The customer's OT network is air-gapped or cannot reach the MSSP console | Agents must be able to connect to their StellarOne |
| The customer requires its data to stay on its own premises (data sovereignty) | A shared console holds every customer's agent data |
| The customer needs a different license edition (Kiosk or OEM) | A console holds one primary edition (see section 2) |
| The customer needs its own license term or its own renewal date | A console has one license with one end date |

Many MSSPs end up with a **hybrid**: a shared pool for small, connected customers,
and dedicated consoles for large or isolated ones. TXOne SenninOne can give one
view across several StellarOne consoles.

---

## 2. How Stellar licensing works and what it means for an MSSP

Sources: StellarOne 5.0 Administrator Guide, the Stellar License Model deck (v2.7),
the TXOne price book FY26Q3. Confirm commercial points with your TXOne contact.

| Fact | Consequence for the MSSP |
|---|---|
| A StellarOne license is a **seat count per console** ("Seats: 120/500"). | The pool is shared by all customers on that console. |
| When more agents register than there are seats, the extra agents become **Inactive**. They stay registered, but they are **not protected**. | Running out of seats leaves customer machines unprotected. Watch the headroom (section 8). |
| Removing an agent releases its seat immediately. | Off-boarding a customer frees seats for others. |
| The license is bound to **one console**. Seats cannot be moved to another console. | Size each console's pool on its own. |
| A console has **one primary license** (StellarICS, StellarKiosk or StellarOEM), plus an optional StellarDiscover add-on. | Offering ICS and OEM tiers needs separate consoles. |
| One license has **one end date**. | Every customer on the console shares the same renewal date. Align customer contracts to it, or bill monthly. |
| StellarICS and StellarKiosk are annual subscriptions, in seat bands starting at 5 seats. StellarOEM is a one-time perpetual purchase with no renewal. | Annual pool plus monthly resale is the usual MSSP structure. |
| With a StellarDiscover add-on, Discover agents use their own pool and do not consume ICS seats. Without the add-on, they consume ICS seats. | The tool reports Discover agents in a separate column. |
| The OpenAPI exposes license status per agent, but not the console's purchased seat total. | You enter the pool size by hand (`--pool-size`). |

### Edition features in brief

| Feature | StellarICS | StellarKiosk | StellarOEM |
|---|---|---|---|
| Real-time, scheduled and on-demand malware scanning | ✓ | ✓ | – |
| Application / operation lockdown | ✓ | Windows-Legacy only | ✓ (enabled by default) |
| Operations Behavior Anomaly Detection | ✓ | Script behaviour only | Script behaviour only (Windows 7+) |
| OT Application Safeguard | ✓ | ✓ | ✓ |
| Trusted USB device control | ✓ | ✓ | ✓ |
| Component / pattern updates | ✓ | ✓ | – |

For a managed protection service, **StellarICS** is the normal choice.

### To agree with TXOne before launch

- That the license terms (EULA) allow the MSSP to run licenses on behalf of third parties.
- The commercial model: a pool size with periodic true-up, and whether the co-term
  requirement can be waived for an MSSP. These are not standard in the price book today.
- An NFR or demo pool for onboarding and testing.

---

## 3. Designing the StellarOne console for multiple customers

### Group structure

The tool decides which customer an agent belongs to from the group tree:

- **`--customer-level 1`** (default): each direct child of the root group is a customer.
  ```
  All Agents → Customer A → Site 1
  ```
- **`--customer-level 2`**: use this when there is a layer above the customers.
  ```
  All Agents → Region North → Customer A
  ```

Every agent in a customer group, or in any group below it, is counted for that customer.

Rules:

1. **Name customer groups exactly as they should appear on the invoice.** The quota
   file matches on the group name.
2. **Never leave agents in the root group or ungrouped.** They show up as
   `(above customer level)` or `(ungrouped)`, and nobody gets billed for them.
3. **Create the customer group before you give the customer an agent installer.**
   That way, new agents land in the right group from the start.

### Customer separation and access

StellarOne separates customers logically, through user access:

- **Roles:** Admin, Operator and Viewer.
- **Group Control:** a list of the groups each user may see.
- **SSO:** AD or SAML user groups can be mapped to groups in Group Control.

Give each customer's users access only to their own customer group. Keep Admin
accounts with the MSSP. Policies can be set per customer group, and sub-groups
inherit them.

This is logical separation inside one database, not physically separate tenants.
Make sure the customer contract says so.

### API account for metering

Create a dedicated StellarOne account for the tool:

- **Role:** Viewer.
- **Group Control:** all customer groups.

Generate the API key on that account (Account Management > Generate API Key). The
key acts with that account's permissions. If the account cannot see a group, the
agents in that group are missing from the counts.

---

## 4. Installing and configuring the tool

Requirements:

- Python 3.8 or later.
- A host that can reach the StellarOne console over HTTPS, normally a server or jump
  host in the MSSP's management network.
- No third-party packages.

Copy `stellar_mssp_usage.py` to that host, then set the connection details as
environment variables:

| Variable | Required | Meaning |
|---|---|---|
| `STELLARONE_URL` | yes | Console base URL, e.g. `https://stellarone.mssp.example` |
| `STELLARONE_API_KEY` | yes | API key of the metering account |
| `STELLARONE_CA_FILE` | no | Path to the console certificate, if it is self-signed (recommended) |
| `STELLARONE_INSECURE` | no | `1` skips TLS verification. Lab and demo systems only. |

Linux / macOS:

```bash
export STELLARONE_URL=https://stellarone.mssp.example
export STELLARONE_API_KEY='<api key>'
export STELLARONE_CA_FILE=/etc/stellar/stellarone.crt
```

Windows PowerShell:

```powershell
$env:STELLARONE_URL = "https://stellarone.mssp.example"
$env:STELLARONE_API_KEY = "<api key>"
$env:STELLARONE_CA_FILE = "C:\stellar\stellarone.crt"
```

---

## 5. Daily snapshot

```bash
python3 stellar_mssp_usage.py snapshot \
    --pool-size 500 \
    --quotas quotas.csv \
    --history-dir history \
    --out-dir reports
```

| Option | Default | Meaning |
|---|---|---|
| `--customer-level N` | `1` | Group depth that represents a customer (section 3) |
| `--pool-size N` | – | Seats purchased for this console. Enables the headroom figures and warnings. |
| `--quotas FILE` | – | CSV with each customer's agreed maximum seats (section 8) |
| `--out-dir DIR` | `reports` | Where the daily CSV reports are written |
| `--history-dir DIR` | – | Where a dated copy of the snapshot is stored. **Required for the monthly report.** |
| `--fail-on-alert` | off | Exit with code 2 when there are alerts, for monitoring systems |

The tool makes read-only calls only:

- `GET /api/v1/groups` reads the group tree.
- `GET /api/v1/agents` reads all agents. Inactive agents are also requested
  explicitly, and duplicates are merged.

The tool then:

1. Maps every agent to its customer.
2. Classifies each agent (section 7).
3. Prints a table.
4. Writes two CSV files and, with `--history-dir`, a snapshot copy.

Example console output:

```
StellarOne usage per customer - 2026-11-18  (https://stellarone.mssp.example)

Customer      Billable  of which grace  Discover  Inactive  Expired  Total  Quota
---------------------------------------------------------------------------------
Customer A    7         0               0         0         0        7      5
Customer B    3         1               0         2         0        5      10
Customer C    0         0               1         0         1        2
(ungrouped)   1         0               0         0         0        1

Billable seats in use: 11 of 14 purchased (3 free, 79% used)

Alerts:
  ! Customer A: 7 billable seats, quota 5 (+2)
  ! Customer B: 2 agent(s) without a seat - not protected
  ! Customer C: 1 agent(s) with an expired license
```

---

## 6. Monthly billing report

```bash
python3 stellar_mssp_usage.py month 2026-11 --history-dir history --out-dir reports
```

The report reads every `snapshot_2026-11-*.csv` file in the history folder and writes
`billing_2026-11.csv`. It gives each customer:

| Column | Meaning |
|---|---|
| `peak_seats` | Highest daily billable count in the month |
| `average_seats` | Average daily billable count. A day without a snapshot row for the customer counts as 0. |
| `last_seats` | Billable count on the last day the customer appeared |
| `days_measured` | Number of daily snapshots found for the month |

### Choosing the billing basis

| Basis | Pros | Cons |
|---|---|---|
| **Peak** | Simple, and it covers short spikes, such as onboarding a site mid-month | The customer pays for a full month on a one-day peak |
| **Average** | Fair when agent counts change a lot | Harder to explain. Sensitive to missing snapshot days. |
| **Committed minimum + peak above it** | Predictable revenue for the MSSP, flexible for the customer | Needs a quota per customer |

State the chosen basis in the customer contract. Run the snapshot every day: missing
days lower the average. Check `days_measured` before you invoice.

---

## 7. Reports and fields

### `usage_by_customer_<date>.csv`: one row per customer

| Column | Meaning |
|---|---|
| `date` | Snapshot date |
| `customer` | Customer group name, or `(ungrouped)` / `(above customer level) <group>` |
| `billable_seats` | **The billing figure.** Equal to `billable` + `grace`. |
| `billable` | Agents holding a seat with a valid license |
| `grace` | Agents holding a seat whose license is in its grace period. Billable, but renew now. |
| `discover` | Discover (sensor) edition agents. Not billed as protection seats. |
| `inactive` | Agents registered without a seat. **Not protected.** |
| `expired` | Agents whose license has expired |
| `total_agents` | All agents in the customer's groups |
| `quota` | Agreed maximum from the quota file (empty if none) |
| `over_quota` | Billable seats above the quota |

### `usage_by_agent_<date>.csv`: one row per agent

This file is the invoice appendix and the source for troubleshooting.

Columns: `customer`, `class`, `hostname`, `ip`, `platform` (Windows / Windows-Legacy /
Linux), `edition` (PROTECTION / SENSOR), `license_status`, `license_type` (FULL / TRIAL),
`license_expires`, `online`, `agent_uuid`, `group_uuid`.

### Classification rules

Each agent gets exactly one class, checked in this order:

| Order | Condition (agent fields) | Class | Billed |
|---|---|---|---|
| 1 | `agentEdition` = `AGENT_EDITION_SENSOR` | `discover` | no |
| 2 | `licenseStatus` = `LICENSE_STATUS_EXPIRED` | `expired` | no |
| 3 | `activated` = false, or `licenseStatus` is empty or `LICENSE_STATUS_NOTACTIVATED` | `inactive` | no |
| 4 | `licenseStatus` = `LICENSE_STATUS_GRACE_PERIOD` | `grace` | yes |
| 5 | anything else (normally `LICENSE_STATUS_VALID`) | `billable` | yes |

### `billing_<YYYY-MM>.csv`: monthly billing basis

The columns are described in section 6.

### Example files

`usage_by_customer_2026-11-18.csv`

```csv
date,customer,billable_seats,billable,grace,discover,inactive,expired,total_agents,quota,over_quota
2026-11-18,Customer A,7,7,0,0,0,0,7,5,2
2026-11-18,Customer B,3,2,1,0,2,0,5,10,0
2026-11-18,Customer C,0,0,0,1,0,1,2,,
2026-11-18,(ungrouped),1,1,0,0,0,0,1,,
```

`usage_by_agent_2026-11-18.csv` (excerpt)

```csv
customer,class,hostname,ip,platform,edition,license_status,license_type,license_expires,online,agent_uuid,group_uuid
Customer A,billable,HMI-05,10.0.0.5,Windows-Legacy,PROTECTION,VALID,FULL,2027-01-01,True,a5,gA1
Customer B,grace,HMI-10,10.0.0.10,Windows,PROTECTION,GRACE_PERIOD,FULL,2027-01-01,True,a10,gB
Customer B,inactive,HMI-20,10.0.0.20,Windows,PROTECTION,VALID,FULL,2027-01-01,True,a20,gB
Customer C,discover,HMI-11,10.0.0.11,Windows,SENSOR,VALID,FULL,2027-01-01,True,a11,gC
Customer C,expired,HMI-12,10.0.0.12,Windows,PROTECTION,EXPIRED,FULL,2027-01-01,True,a12,gC
```

`billing_2026-11.csv`

```csv
month,customer,peak_seats,average_seats,last_seats,days_measured
2026-11,Customer A,7,6.8,7,30
2026-11,Customer B,3,3.0,3,30
2026-11,Customer C,0,0.0,0,30
```

All example values are test data.

---

## 8. Quotas, pool headroom and alerts

### Quotas per customer

StellarOne cannot cap the number of seats a group uses. Any customer can grow until
the whole pool is used up. The quota file is the control:

```csv
customer,max_seats
Customer A,50
Customer B,120
```

- The name must match the customer group name exactly.
- Customers not in the file have no quota.
- Over-quota usage is flagged, never blocked. Decide in the contract whether it is
  billed as overage or triggers a re-negotiation.

### Pool headroom

With `--pool-size`, the tool shows seats used, seats free and the percentage used. It warns:

- when less than 10 % of the pool is free;
- when there are more billable agents than purchased seats.

Order more seats before the pool runs out. Agents beyond the pool become Inactive
and unprotected.

### Alerts

| Alert | Meaning | Action |
|---|---|---|
| `<customer>: N billable seats, quota Q (+X)` | The customer is above its agreed maximum | Bill the overage, or agree a new quota |
| `<customer>: N agent(s) without a seat - not protected` | Agents are Inactive | Free seats or buy more, **urgently** |
| `<customer>: N agent(s) with an expired license` | The license has expired on these agents | Renew the license, or remove the agents |
| `POOL: less than 10% of the seat pool left` | The pool is nearly full | Plan a seat increase |
| `POOL: N more agents than purchased seats` | Over-seat | Buy seats now |

With `--fail-on-alert`, the tool exits with code 2 when there are any alerts, so a
scheduler or monitoring system (Nagios, Zabbix, PRTG, a SIEM) can raise a ticket.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Error: configuration, connection, or an API error (details on stderr) |
| 2 | Success, but there were alerts (only with `--fail-on-alert`) |

---

## 9. Scheduling

Run the snapshot once a day at a fixed time. Run the monthly report on the 1st, for
the previous month.

**Linux (cron)**, with the environment variables in `/etc/stellar/metering.env`:

```cron
15 6 * * *  . /etc/stellar/metering.env && cd /opt/stellar-metering && python3 stellar_mssp_usage.py snapshot --pool-size 500 --quotas quotas.csv --history-dir history >> metering.log 2>&1
30 6 1 * *  . /etc/stellar/metering.env && cd /opt/stellar-metering && python3 stellar_mssp_usage.py month $(date -d "last month" +\%Y-\%m) --history-dir history >> metering.log 2>&1
```

**Windows (Task Scheduler):** create a daily task that runs:

```
Program:   C:\Python312\python.exe
Arguments: stellar_mssp_usage.py snapshot --pool-size 500 --quotas quotas.csv --history-dir history
Start in:  C:\stellar-metering
```

Set the `STELLARONE_*` variables as system environment variables for the service
account that runs the task. Do not put them in the task arguments.

Keep the `history` folder backed up. It is the evidence behind the invoices.

---

## 10. Operational procedures

### Onboarding a customer

1. Agree the seat quota and the billing basis in the contract.
2. Create the customer group under the root group, named as it should appear on the invoice.
3. Create sub-groups for sites or lines as needed, and set the customer's policy on the customer group.
4. Create the customer's user accounts or SSO mapping, with Group Control limited to their group.
5. Add the customer to `quotas.csv`.
6. Check pool headroom, then deploy agents with an installer that targets the customer group.
7. After the first snapshot, confirm that no agents landed in `(ungrouped)`.

### Off-boarding a customer

1. Agree the end date. The customer's last month is billed from the snapshots.
2. Uninstall or remove the customer's agents. Removing an agent from StellarOne frees its
   seat, but leaves the endpoint unprotected. Coordinate with the customer.
3. Delete the customer group and the customer's accounts.
4. Remove the customer from `quotas.csv`. Keep the history files for your retention period.

### Moving a customer between consoles

Agents can be moved from one StellarOne to another without being reinstalled, for
example from a customer's own console into the shared pool. TXOne provides a
controlled move script on request, through a support (SEG) case. The script can
place the agents directly in a target group. See the "Move Stellar Agent Practice
Guide". Seats are released on the source console and consumed on the target console.

### Renewal

All customers on a console share its license end date. Agents in grace period
appear in the `grace` column. Renew before grace runs out, or they move to `expired`.

---

## 11. Limitations

- **No seat cap per group in StellarOne.** Quotas are monitored by this tool, not enforced.
- **The purchased seat total is not available through the OpenAPI.** Set `--pool-size` by
  hand, and update it after each license change.
- **One license edition and one end date per console.** Different tiers or terms need
  separate consoles, each metered by its own run of the tool.
- **Logical, not physical, customer separation** in a shared console.
- **Daily granularity.** Agents added and removed on the same day between two snapshots
  are not seen.
- **Agents outside customer groups are not billed.** Watch for `(ungrouped)` and
  `(above customer level)`.
- **The tool only counts.** It does not create invoices, and it does not change
  anything in StellarOne.

---

## 12. Validate before go-live

The tool has only been tested against a simulated StellarOne API. Before the first
billing run, on your real console:

1. Run `snapshot` once.
2. In StellarOne, compare the following with the report:
   - the total number of agents on the Agents page;
   - the number of Inactive agents (Agents > filter Inactive);
   - the seat usage on the License page;
   - the agent count of two or three customer groups.
3. Confirm that inactive (over-seat) agents appear in the `inactive` column.
4. Confirm that Discover agents, if you have any, appear in the `discover` column.
5. If anything differs, open `usage_by_agent_<date>.csv`, look at the `class` and
   `license_status` columns of the agents concerned, and adjust the classification
   rules in `classify()` in the script if needed.

---

## 13. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `HTTP 401 ... API key missing, invalid or expired` | Wrong or expired key | Generate a new key, then update `STELLARONE_API_KEY` |
| `HTTP 403` | The account lacks permission | Give the metering account access to all groups in Group Control |
| A customer's agents are missing | The account cannot see that group | As above |
| `Cannot reach ...: CERTIFICATE_VERIFY_FAILED` | Self-signed console certificate | Set `STELLARONE_CA_FILE` to the console certificate |
| `Cannot reach ...` with any other reason | Network, proxy or firewall | Test with `curl -k $STELLARONE_URL` from the same host |
| Many agents under `(ungrouped)` | Agents were installed without a target group | Move them into the right customer group |
| A customer appears as `(above customer level) ...` | The group depth does not match `--customer-level` | Fix the group structure, or change `--customer-level` |
| `No snapshots for YYYY-MM` | `--history-dir` was not used, or points to another folder | Use the same `--history-dir` for both commands |
| A low `average_seats` | Snapshot days are missing | Check `days_measured` and the scheduler |
| Slow runs or 429 responses | API rate limit | The tool retries automatically. Avoid running it more than once an hour. |

When opening a TXOne support case for an API error, include the `request-id` the
tool prints.

---

## 14. Security

- **API key:**
  - Use a dedicated Viewer account.
  - Store the key only in environment variables or a protected file, readable by the
    service account only.
  - Never put the key in scripts, task arguments or tickets.
  - Rotate the key periodically, and when staff leave.
- **TLS:**
  - Keep TLS verification on: use `STELLARONE_CA_FILE`, not `STELLARONE_INSECURE`, in production.
- **Reports:**
  - The per-agent report contains hostnames and IP addresses of customer OT assets.
    Treat it as confidential customer data.
  - Send each customer only their own rows.
- **No write access:** the tool only reads from StellarOne. It cannot change policies,
  agents or licenses.
