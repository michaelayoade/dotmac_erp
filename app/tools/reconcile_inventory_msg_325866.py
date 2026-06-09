"""Reconcile Stores Garki inventory from message 325866.

Dry run:
    python -m app.tools.reconcile_inventory_msg_325866

Apply:
    python -m app.tools.reconcile_inventory_msg_325866 --apply
"""

from __future__ import annotations

import argparse
import csv
import re
from difflib import SequenceMatcher
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, text

from app.config import settings


ORG_ID = "00000000-0000-0000-0000-000000000001"
WAREHOUSE_NAME = "Stores Garki"
REFERENCE = "RECON-MSG-325866"
LOT_NUMBER = REFERENCE
SOURCE_DOCUMENT_TYPE = "STOCK_RECONCILIATION"
REASON_CODE = "STOCK_RECON"
TRANSACTION_TYPE = "ADJUSTMENT"
REPORT_DIR = Path("reports")
ITEM_ALIASES = {
    "2 Core FOC": "2Core FOC",
    "8 Core FOC": "8Core FOC",
    "Mikrotic 80km": "Mikrotic DDM SFP 80Km",
    "Mikrotic 20km": "Mikrotic DDM SFP 20Km",
    "Dell DMC SFP + SR": "Dell SFP-SR",
    "Huawei 10km": "SFP- Hauwei 10G-10Km",
    "Rocket Prism-5AC-GEN2": "Rocket Prism -5AC-Gen2",
    "Airfiber 5XHD": "AirFiber-AF-5XHD",
    "AIR MAX ROCKET AC LITE": "AirMAX Rocket AC Lite",
    "22mm Clips": "22mm Clip",
    "8mm Clips": "8mm Clip",
    "Electrical Tapes": "Electrical Tape",
    "Flatbars": "Flat Bars",
    "Threaded Rods": "Threaded Rod",
    "Cable Tie X370": "Cable Tie X 370",
    "Cable Tie X200": "Cable Tie X 200",
    "Airfiber-5G30-S4F-DISH": "AirFiber -5G30-S45 -Dish",
    "Mikrotik Dish": "Mikrotik Dish - 5G-30DBI",
    "Rocket Dish": "Rocket Dish -5G30 30dBi",
    "Pattress Box-Fiber": "Pattress Box - Fiber",
    "Pattress Box Cover-PVC": "Pattress Box Cover - PVC",
    "Fishure (s12)": "Fishure(s12)",
    "Flexible Pipe- 20mm (Black)": "Flexible pipe - 20mm(Black Color)",
    "Flexible Pipe- 20mm (White)": "Flexible pipe - 20mm(White Color)",
    "Flexible Pipe- 25mm": "Flexible pipe - 25mm",
    "LTU Long Range Radio": "LTU Long Range - Radio",
    "LITEBEAM 5AC GEN2": "LiteBeam - LBE-5AC-GEN2",
    '1" Inch Steel Pole': "1 inch Steel pole",
    "Off wall 2ft": "Offwall - 2Ft",
    "Indoor CAT6 cable (Full Copper)": "Indoor Cat 6 -Full Copper",
    "RJ45 Connector Indoor (CAT6)": "Rj45 connector indoor",
    "Outdoor CAT6 Cable": "Outdoor Cat 6 cable",
    "Outdoor CAT7": "Outdoor Cat 7 cable",
    "Patch Cord SC-SC (Blue)": "Patchcord - SC-SC (BLUE)",
    "Patch Cord SC-LC (Green)": "Patchcord SC-LC GREEN",
    "Patch Cord SC-SC (Green)": "Patchcord - SC-SC (GREEN)",
    "Patch Cord SC-LC (Blue)": "Patchcord SC-LC BLUE",
    "Power Meter + VFL": "Power Meter",
    "Splitter 1:16": "SPLITTER1:16",
    "48 CORE DOME CLOSURE": "48Core Dome Closure",
    "Edge Switch 24Port": "Edge Switch-24Port",
    "Unifi Switch 24Port": "Unifi Switch 24 Port",
    "Cloud Core Router (CCR1009-7G-1C-1S+PC)": "CCR1009-7G-1C-1S+PC",
    "Cloud Core Router (CCR2004 -1G-12S+12XS)": "CCR2004-1G-12S+2XS",
}

RAW_INVENTORY = r"""
2 Core FOC
INVENTORY
525,000
10,640 Meters

8 Core FOC
INVENTORY
750000  (105 naira per meter)
7,047  Meters

Mikrotic 80km
INVENTORY
3 Pieces

Mikrotic 10G DDM 10km
INVENTORY
1 Pieces

Mikrotic 20km
INVENTORY
1 Pcs

Unknown SFP
INVENTORY
3 Pieces

Dell DMC SFP + SR
INVENTORY
1 Pcs

Huawei Class C+
INVENTORY
1 Pcs

Huawei 10km
INVENTORY
45,000
1 Pcs

Ubiquiti (SFP B+, 20km)
INVENTORY
2 Pcs

Fortinet
INVENTORY
1 Pcs

Rocket Prism-5AC-GEN2
INVENTORY
355,000
1
2248G 70A7414874F2

Rocket Prism-5AC-GEN2
INVENTORY
355,000
1
2305G 70A7414EE6EF

Rocket Prism-5AC-GEN2
INVENTORY
355,000
1
2201G 784558A4840D

Rocket Prism-5AC-GEN2
INVENTORY
355,000
1
NIL

Rocket Prism-5AC-GEN2
INVENTORY
355,000
1
NIL

Rocket Prism-5AC-GEN2
INVENTORY
355,000
1
NIL

Air wave ML06
INVENTORY
1
1C6A1B6D5E3F

Air wave ML06
INVENTORY
1
1C6A1B6D5DD6

Air wave ML06
INVENTORY
1
1C6A1B6D5E03

Air wave ML06
INVENTORY
1
1C6A1B6D5C7D

Air wave ML06
INVENTORY
1
1C6A1B6D5C96

Air wave ML06
INVENTORY
1
1C6A1B6D2127

Electrical Box
INVENTORY
3,000 naira per box
nil

Airfiber 5XHD
INVENTORY
630,000
1
2202C 7845580BD0D1

Airfiber 5XHD
INVENTORY
630,000
1
2202C 7845580BD009

Airfiber 5XHD
INVENTORY
630,000
1
2133C 7492BF5F4A43

Airfiber 5XHD
INVENTORY
630,000
1
2049C F492BF1F22B7

Airfiber 5XHD
INVENTORY
630,000
1
1749P802AA8CEF54A

Airfiber 5XHD
INVENTORY
630,000
1
2020C18E82908D5A3

Airfiber 5XHD
INVENTORY
630,000
1
2115CF492BF2F7596

Airfiber 5XHD
INVENTORY
630,000
1
2208C7845587F2062

POE ADAPTER 24V
INVENTORY
13,000
1

ADAPTER - Blue
INVENTORY
1000
1406

ADAPTER - Green
INVENTORY
1000
3361

LTU ROCKET
INVENTORY
1
2038CE063DABF267B

LTU ROCKET
INVENTORY
1
2037CE063DABF248C

LTU ROCKET
INVENTORY
1
2113CF492BF2F6BFA

LTU ROCKET
INVENTORY
1
2043CE063DABF9060

LTU ROCKET
INVENTORY
1
2037CE063DABF2495

LTU ROCKET
INVENTORY
1
2130CF492BF5F0CC7

LTU ROCKET
INVENTORY
1
2043CE063DABF8513

LTU ROCKET
INVENTORY
1
2043CE063DABF9001

LTU ROCKET
INVENTORY
1
F492BF2FFB14

LTU ROCKET
INVENTORY
1
7845580B61BC

LTU ROCKET
INVENTORY
1
D021F9F0CC72

LTU ROCKET
INVENTORY
1
7845580B61E2

AIR MAX ROCKET AC LITE
INVENTORY
1
1934G7483C260395A

AIR MAX ROCKET AC LITE
INVENTORY
1
1934G7483C26039A8

AIR MAX ROCKET AC LITE
INVENTORY
1
NIL

ROCKET M5
INVENTORY
1
1816KFCECDA9CEC34

22mm Clips
INVENTORY
2,500 per pack
18

8mm Clips
INVENTORY
700 per pack
17

7mm Clips
INVENTORY
6

Electrical Tapes
INVENTORY
4000 per roll ( 400 naira per one of 10 pieces in a roll)
Nil

PVC Coupler
INVENTORY
2500
1275 Pieces

SC-APC Pigtail
INVENTORY
12,387 Pieces

Nuts
INVENTORY
60 naira
359 Pieces

Bolt and Nuts
INVENTORY
NIL

Flatbars
INVENTORY
780
217 Pieces

Threaded Rods
INVENTORY
500 naira
208 Pieces

Cable Tie X370
INVENTORY
3,000 naira per packet (30 naira per one tie of 100pieces in a packet)
2030 Pieces

Cable Tie X200
INVENTORY
NIL

Airfiber-5G30-S4F-DISH
INVENTORY
2

Mikrotik Dish
INVENTORY
NIL

Rocket Dish
INVENTORY
NIL

Cleavers
INVENTORY
150,000
4

Pattress Box-Fiber
INVENTORY
939

Pattress Box - 3x6 PVC
INVENTORY
NIL

Pattress Box Cover-PVC
INVENTORY
NIL

Electrode
INVENTORY
80,000
9

Fishure (s6)
INVENTORY
NIL

Fishure (s12)
INVENTORY
75naira
2

Flexible Pipe- 20mm (Black)
INVENTORY
NIL

Flexible Pipe- 20mm (White)
INVENTORY
330 Naira per meter( 16,500 per one roll)
28 Meters

Flexible Pipe- 25mm
INVENTORY
NIL

Heat Shrink
INVENTORY
2,221 pieces

LTU Long Range Radio
INVENTORY
1

LAP-GPS Antenna
INVENTORY
185,000
1
2514V1C6A1BB40864

LITEAP AC
INVENTORY
155,000
1
NIL

LITEAP AC
INVENTORY
155,000
1
NIL

LITEAP AC
INVENTORY
155,000
1
2115GF492BFBAC23

LITEAP AC
INVENTORY
155,000
1
2303G70A7414E1354

LITEAP AC
INVENTORY
155,000
1
2303G70A7414E168E

LITEBEAM 5AC GEN2
INVENTORY
107,000
1
1C6A1BC6AED7

LITEBEAM 5AC GEN2
INVENTORY
107,000
1
1C6A1BC6AF35

LITEBEAM 5AC GEN2
INVENTORY
107,000
1
1C6A1BC6A66D

LITEBEAM 5AC GEN2
INVENTORY
107,000
1
1C6A1BC6AB83

LITEBEAM 5AC GEN2
INVENTORY
107,000
1
1C6A1BC6994B

LITEBEAM 5AC GEN2
INVENTORY
107,000
1
1C6A1BC695EE

LITEBEAM 5AC GEN2
INVENTORY
107,000
1
1C6A1BC69672

LITEBEAM 5AC GEN2
INVENTORY
107,000
1
1C6A1BC69E41

LITEBEAM 5AC GEN2
INVENTORY
107,000
1
1C6A1BC69763

LITEBEAM 5AC GEN2
INVENTORY
107,000
1
1C6A1BC6B0B8

LITEBEAM 5AC GEN2
INVENTORY
107,000
1
1C6A1BC69D15

LITEBEAM 5AC GEN2
INVENTORY
107,000
1
1C6A1BC699B0

LITEBEAM 5AC GEN2
INVENTORY
107,000
1
1C6A1BC6901E

LITEBEAM 5AC GEN2
INVENTORY
107,000
1
1C6A1BC694A0

LITEBEAM 5AC GEN2
INVENTORY
107,000
1
1C6A1BC695F8

LITEBEAM 5AC GEN2
INVENTORY
107,000
1
1C6A1BC69865

MOKO Spirit
INVENTORY
NIL

1" Inch Nail
INVENTORY
NIL

3" Inch Nail
INVENTORY
nil

Onwall Pole
INVENTORY
2

1" Inch Steel Pole
INVENTORY
9,000
2

11/4 Steel Pole
INVENTORY
16,000
2

Off wall 2ft
INVENTORY
14,500
Nil

Indoor CAT6 cable (Full Copper)
INVENTORY
295.08 naira per meter
198 Meters

RJ45 Connector Outdoor (CAT6)
INVENTORY
1385 Pcs

RJ45 Connector Indoor (CAT6)
INVENTORY
75 naira
721 Pcs

RJ45 Union Connector
INVENTORY
1200
4 Pieces

RJ45 Connector Outdoor (CAT7)
INVENTORY
500
NIL

Huawei ONT EG8145V6
INVENTORY
20,000
1
3C15FBC6C26F

Outdoor CAT6 Cable
INVENTORY
327.87 naira per meter
490 meters

Outdoor CAT7
INVENTORY
1,000 per meter
16 meters

OLT Power Pack
INVENTORY
2

Patch Cord SC-SC (Blue)
INVENTORY
2,500
85 Pieces

Patch Cord SC-LC (Green)
INVENTORY
32 Pieces

Patch Cord LC-LC 3m
INVENTORY
73 Pieces

Patch Cord SC-SC (Green)
INVENTORY
2,500
54 Pieces

Patch Cord SC-LC (Blue)
INVENTORY
18 Pieces

Patch Cord FC-LC 3m
INVENTORY
15 Pieces

Patch Cord FC-FC (Green)
INVENTORY
3 Pieces

Cutter
INVENTORY
6,000
6

Power Meter + VFL
INVENTORY
50,000
2

OTDR
INVENTORY
500,000
1

PVC Pipe 20mm
INVENTORY
8,000 naira per bundles
12 Bundles

Splitter 1:4
INVENTORY
126

Splitter 1:8
INVENTORY
137

Splitter 1:16
INVENTORY
160

Splitter 1:32
INVENTORY
15

Splitter 1:64
INVENTORY
36

Stagger
INVENTORY
1

TENDA F3 (4-in-1)
INVENTORY
21,000
Nil

TP-LINK
INVENTORY
60,000
NIL

DUCT PIPE
INVENTORY
533 naira per meter
120 meter

48 CORE DOME CLOSURE
INVENTORY
35,000
NIL

Edge Switch 24Port
INVENTORY
830,000
1
213GE43883D850A8

Edge Switch 24Port
INVENTORY
830,000
1
2146V784558E26288

Edge Switch 24Port
INVENTORY
830,000
1
1801G788A20FA9F6C

Unifi Switch 24Port
INVENTORY
1
1922G18E829ACCAFA-DKZCFH

CISCO SG250-28 Port switch
INVENTORY
1 Good
DNI232203JS

Ufiber OLT GPON
INVENTORY
1
NIL

Ufiber OLT GPON
INVENTORY
1
1827GB4F3E41AF564-FQKWTF

Ufiber OLT GPON
INVENTORY
1
1804G788A20FC63B9-QC5RPY

Ufiber OLT GPON
INVENTORY
1
1827GB4FBE41AF170-TSFZ3H

Ufiber OLT GPON
INVENTORY
1
1833GB4FBE45010E6-MEMUAD

Ufiber OLT GPON
INVENTORY
1
2227G70A741C4B8C4-JHN6P6

Cloud Core Router (CCR1009-7G-1C-1S+PC)
INVENTORY
1
HCC08EFHJFM4/2134/R2

Cloud Core Router (CCR1036-12G-4S)
INVENTORY
1
5AAB046BEB8B4/519

Cloud Core Router (CCR2004 -1G-12S+12XS)
INVENTORY
1
D4F00E00B2A64/120

Cloud Core Router (CCR2004 -1G-12S+12XS)
INVENTORY
1
D4F00EED3899/110

Cloud Core Router (CCR2004 -1G-12S+12XS)
INVENTORY
1
D4F00EEA10E6/11

Cloud Core Router (CCR1072-1G-8S+)
INVENTORY
1
8A340B334F4A/936

Cloud Core switch (CRS328-24P-4S-1RM)
INVENTORY
740,000
1
HCN0874W29M/221/r2

Cloud Core switch (CRS328-24P-4S-1RM)
INVENTORY
740,000
1
CFD40C3C724D/018/r2

Cloud Core switch (CRS328-24P-4S-1RM)
INVENTORY
740,000
1
HCP081VWB3D/222/r2
"""


@dataclass
class Target:
    name: str
    quantity: Decimal = Decimal("0")
    serials: list[str] = field(default_factory=list)
    source_rows: int = 0


def normalize_name(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def loose_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def parse_quantity(value: str) -> Decimal | None:
    cleaned = value.strip().casefold()
    if cleaned in {"nil", "nill", "n/a", "na", "none", "null", "-", ""}:
        return Decimal("0")
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", cleaned)
    if not match:
        return None
    return Decimal(match.group(0).replace(",", ""))


def is_quantity(value: str) -> bool:
    cleaned = value.strip().casefold()
    if cleaned in {"nil", "nill", "n/a", "na", "none", "null", "-", ""}:
        return True
    return bool(
        re.match(
            r"^[-+]?\d[\d,]*(?:\.\d+)?(?:\s*(meters?|pieces?|pcs?|bundles?|rolls?|packs?|packet|good))?$",
            cleaned,
        )
    )


def is_priceish(value: str) -> bool:
    cleaned = value.strip().casefold()
    return "naira" in cleaned or "per " in cleaned


def parse_targets() -> dict[str, Target]:
    lines = [line.strip() for line in RAW_INVENTORY.splitlines()]
    rows: list[tuple[str, list[str]]] = []
    index = 0
    while index < len(lines):
        if lines[index].upper() != "INVENTORY" or index == 0:
            index += 1
            continue
        name = lines[index - 1].strip()
        values: list[str] = []
        pos = index + 1
        blanks = 0
        while pos < len(lines):
            current = lines[pos].strip()
            if not current:
                blanks += 1
                if blanks >= 2 and values:
                    break
                pos += 1
                continue
            if current.upper() == "INVENTORY":
                break
            next_pos = pos + 1
            while next_pos < len(lines) and not lines[next_pos].strip():
                next_pos += 1
            if next_pos < len(lines) and lines[next_pos].upper() == "INVENTORY":
                break
            values.append(current)
            blanks = 0
            pos += 1
        rows.append((name, values))
        index = pos

    targets: dict[str, Target] = {}
    placeholders = {"nil", "nill", "n/a", "na", "none", "null", "-", ""}
    for name, values in rows:
        values = [value for value in values if value]
        if not values:
            continue
        if len(values) >= 2 and (
            is_priceish(values[0])
            or (
                re.fullmatch(r"\d[\d,]*(?:\.\d+)?", values[0])
                and is_quantity(values[1])
            )
        ):
            quantity_index = 1
        elif is_quantity(values[0]):
            quantity_index = 0
        elif len(values) >= 2 and is_quantity(values[1]):
            quantity_index = 1
        else:
            quantity_index = 0

        quantity = parse_quantity(values[quantity_index])
        if quantity is None:
            raise RuntimeError(f"Could not parse quantity for {name!r}: {values!r}")

        serials: list[str] = []
        for value in values[quantity_index + 1 :]:
            cleaned = value.strip()
            lowered = cleaned.casefold()
            if lowered in placeholders or "=" in cleaned or is_priceish(cleaned):
                continue
            if re.search(r"\d", cleaned) and not is_quantity(cleaned):
                serials.append(cleaned)

        target = targets.setdefault(name, Target(name=name))
        target.quantity += quantity
        target.serials.extend(serials)
        target.source_rows += 1

    return targets


ON_HAND_SQL = text(
    """
    select
        coalesce(sum(case
            when transaction_type in ('RECEIPT', 'RETURN', 'ASSEMBLY') then quantity
            when transaction_type in ('ISSUE', 'SALE', 'SCRAP', 'DISASSEMBLY') then -quantity
            else quantity
        end), 0)
    from inv.inventory_transaction
    where organization_id = :org_id
      and item_id = :item_id
      and warehouse_id = :warehouse_id
    """
)


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--candidates", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    engine = create_engine(str(settings.database_url))
    targets = parse_targets()
    now = datetime.now(timezone.utc)
    today = date(2026, 6, 8)

    updated: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []

    with engine.begin() as conn:
        conn.execute(
            text("select set_config('app.current_organization_id', :org_id, false)"),
            {"org_id": ORG_ID},
        )
        existing_ref = conn.execute(
            text(
                """
                select count(*)
                from inv.inventory_transaction
                where organization_id = :org_id and reference = :reference
                """
            ),
            {"org_id": ORG_ID, "reference": REFERENCE},
        ).scalar_one()
        if existing_ref and args.apply:
            raise RuntimeError(f"{REFERENCE} already has {existing_ref} transactions")

        warehouse = conn.execute(
            text(
                """
                select warehouse_id
                from inv.warehouse
                where organization_id = :org_id
                  and warehouse_name = :warehouse_name
                  and is_active = true
                """
            ),
            {"org_id": ORG_ID, "warehouse_name": WAREHOUSE_NAME},
        ).scalar_one()

        fiscal_period = conn.execute(
            text(
                """
                select fiscal_period_id
                from gl.fiscal_period
                where organization_id = :org_id
                  and start_date <= :today
                  and end_date >= :today
                  and status = 'OPEN'
                order by start_date desc
                limit 1
                """
            ),
            {"org_id": ORG_ID, "today": today},
        ).scalar_one()

        user_id = conn.execute(
            text(
                """
                select id
                from public.people
                where organization_id = :org_id and is_active = true
                order by case when email = 'admin@example.com' then 0 else 1 end, created_at
                limit 1
                """
            ),
            {"org_id": ORG_ID},
        ).scalar_one()

        item_rows = list(
            conn.execute(
                text(
                    """
                select item_id, item_code, item_name, base_uom, currency_code,
                       coalesce(average_cost, standard_cost, last_purchase_cost, 0) as unit_cost,
                       track_inventory, track_serial_numbers, is_active
                from inv.item
                where organization_id = :org_id
                """
                ),
                {"org_id": ORG_ID},
            ).mappings()
        )
        by_key: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in item_rows:
            item = dict(row)
            by_key[normalize_name(str(item["item_name"]))].append(item)
            by_key[normalize_name(str(item["item_code"]))].append(item)

        if args.verify:
            mismatches: list[dict[str, object]] = []
            checked = 0
            for target in targets.values():
                lookup_name = ITEM_ALIASES.get(target.name, target.name)
                matches = {
                    str(match["item_id"]): match
                    for match in by_key.get(normalize_name(lookup_name), [])
                    if match["is_active"] and match["track_inventory"]
                }
                if len(matches) != 1:
                    continue
                item = next(iter(matches.values()))
                checked += 1
                tx_on_hand = conn.execute(
                    ON_HAND_SQL,
                    {
                        "org_id": ORG_ID,
                        "item_id": item["item_id"],
                        "warehouse_id": warehouse,
                    },
                ).scalar_one()
                lot_balance = conn.execute(
                    text(
                        """
                        select coalesce(sum(b.quantity_on_hand), 0),
                               coalesce(sum(b.quantity_available), 0)
                        from inv.inventory_lot_balance b
                        join inv.inventory_lot l on l.lot_id = b.lot_id
                        where b.organization_id = :org_id
                          and l.item_id = :item_id
                          and b.warehouse_id = :warehouse_id
                        """
                    ),
                    {
                        "org_id": ORG_ID,
                        "item_id": item["item_id"],
                        "warehouse_id": warehouse,
                    },
                ).one()
                serial_count = conn.execute(
                    text(
                        """
                        select count(*)
                        from inv.inventory_serial
                        where organization_id = :org_id
                          and item_id = :item_id
                          and warehouse_id = :warehouse_id
                          and lot_id in (
                              select lot_id
                              from inv.inventory_lot
                              where organization_id = :org_id
                                and item_id = :item_id
                                and lot_number = :lot_number
                          )
                          and status = 'AVAILABLE'
                          and is_active = true
                        """
                    ),
                    {
                        "org_id": ORG_ID,
                        "item_id": item["item_id"],
                        "warehouse_id": warehouse,
                        "lot_number": LOT_NUMBER,
                    },
                ).scalar_one()
                expected_serial_count = (
                    len(set(target.serials))
                    if item["track_serial_numbers"]
                    else serial_count
                )
                if (
                    Decimal(str(tx_on_hand)) != target.quantity
                    or Decimal(str(lot_balance[0])) != target.quantity
                    or Decimal(str(lot_balance[1])) != target.quantity
                    or int(serial_count) != int(expected_serial_count)
                ):
                    mismatches.append(
                        {
                            "file_item_name": target.name,
                            "item_name": item["item_name"],
                            "target_quantity": target.quantity,
                            "transaction_on_hand": tx_on_hand,
                            "lot_on_hand": lot_balance[0],
                            "lot_available": lot_balance[1],
                            "serial_count": serial_count,
                            "expected_serial_count": expected_serial_count,
                        }
                    )
            path = REPORT_DIR / "inventory_recon_325866_verify_mismatches.csv"
            write_csv(
                path,
                mismatches,
                [
                    "file_item_name",
                    "item_name",
                    "target_quantity",
                    "transaction_on_hand",
                    "lot_on_hand",
                    "lot_available",
                    "serial_count",
                    "expected_serial_count",
                ],
            )
            print(f"checked={checked} mismatches={len(mismatches)}")
            print(f"mismatch_report={path}")
            return

        if args.candidates:
            candidate_rows: list[dict[str, object]] = []
            active_inventory = [
                dict(row)
                for row in item_rows
                if row["is_active"] and row["track_inventory"]
            ]
            for target in targets.values():
                exact = by_key.get(normalize_name(target.name), [])
                if exact:
                    continue
                target_loose = loose_key(target.name)
                scored = []
                for item in active_inventory:
                    choices = [str(item["item_name"]), str(item["item_code"])]
                    score = max(
                        SequenceMatcher(None, target_loose, loose_key(choice)).ratio()
                        for choice in choices
                    )
                    if score >= 0.64 or target_loose in [loose_key(c) for c in choices]:
                        scored.append((score, item))
                scored.sort(key=lambda pair: pair[0], reverse=True)
                for score, item in scored[:5]:
                    candidate_rows.append(
                        {
                            "file_item_name": target.name,
                            "target_quantity": target.quantity,
                            "serials_from_file": len(set(target.serials)),
                            "score": f"{score:.3f}",
                            "candidate_item_code": item["item_code"],
                            "candidate_item_name": item["item_name"],
                            "candidate_item_id": item["item_id"],
                            "track_serial_numbers": item["track_serial_numbers"],
                        }
                    )
            path = REPORT_DIR / "inventory_recon_325866_candidates.csv"
            write_csv(
                path,
                candidate_rows,
                [
                    "file_item_name",
                    "target_quantity",
                    "serials_from_file",
                    "score",
                    "candidate_item_code",
                    "candidate_item_name",
                    "candidate_item_id",
                    "track_serial_numbers",
                ],
            )
            print(f"candidate_report={path}")
            return

        for target in targets.values():
            lookup_name = ITEM_ALIASES.get(target.name, target.name)
            matches = {
                str(match["item_id"]): match
                for match in by_key.get(normalize_name(lookup_name), [])
                if match["is_active"] and match["track_inventory"]
            }
            if len(matches) != 1:
                skipped.append(
                    {
                        "item_name": target.name,
                        "target_quantity": target.quantity,
                        "serials_from_file": len(target.serials),
                        "reason": "unmatched" if not matches else "ambiguous",
                        "match_count": len(matches),
                    }
                )
                continue

            item = next(iter(matches.values()))
            item_id = item["item_id"]
            current_on_hand = conn.execute(
                ON_HAND_SQL,
                {"org_id": ORG_ID, "item_id": item_id, "warehouse_id": warehouse},
            ).scalar_one()
            adjustment = target.quantity - Decimal(str(current_on_hand))

            existing_lot = conn.execute(
                text(
                    """
                    select lot_id
                    from inv.inventory_lot
                    where organization_id = :org_id
                      and item_id = :item_id
                      and lot_number = :lot_number
                    """
                ),
                {"org_id": ORG_ID, "item_id": item_id, "lot_number": LOT_NUMBER},
            ).scalar()

            serial_count = 0
            if args.apply:
                if existing_lot is None:
                    existing_lot = conn.execute(
                        text(
                            """
                            insert into inv.inventory_lot (
                                organization_id, item_id, lot_number, received_date,
                                unit_cost, initial_quantity, allocation_reference, is_active
                            )
                            values (
                                :org_id, :item_id, :lot_number, :received_date,
                                :unit_cost, :initial_quantity, :reference, true
                            )
                            returning lot_id
                            """
                        ),
                        {
                            "org_id": ORG_ID,
                            "item_id": item_id,
                            "lot_number": LOT_NUMBER,
                            "received_date": today,
                            "unit_cost": item["unit_cost"],
                            "initial_quantity": target.quantity,
                            "reference": REFERENCE,
                        },
                    ).scalar_one()
                else:
                    conn.execute(
                        text(
                            """
                            update inv.inventory_lot
                            set initial_quantity = :initial_quantity,
                                unit_cost = :unit_cost,
                                allocation_reference = :reference,
                                is_active = true,
                                updated_at = :now
                            where lot_id = :lot_id
                            """
                        ),
                        {
                            "initial_quantity": target.quantity,
                            "unit_cost": item["unit_cost"],
                            "reference": REFERENCE,
                            "now": now,
                            "lot_id": existing_lot,
                        },
                    )

                conn.execute(
                    text(
                        """
                        update inv.inventory_lot_balance b
                        set quantity_on_hand = 0,
                            quantity_available = 0,
                            is_active = case when quantity_allocated > 0 then true else false end,
                            updated_at = :now
                        from inv.inventory_lot l
                        where b.lot_id = l.lot_id
                          and b.organization_id = :org_id
                          and l.item_id = :item_id
                          and b.warehouse_id = :warehouse_id
                          and b.lot_id <> :lot_id
                        """
                    ),
                    {
                        "org_id": ORG_ID,
                        "item_id": item_id,
                        "warehouse_id": warehouse,
                        "lot_id": existing_lot,
                        "now": now,
                    },
                )

                balance_id = conn.execute(
                    text(
                        """
                        select lot_balance_id
                        from inv.inventory_lot_balance
                        where organization_id = :org_id
                          and lot_id = :lot_id
                          and warehouse_id = :warehouse_id
                        """
                    ),
                    {
                        "org_id": ORG_ID,
                        "lot_id": existing_lot,
                        "warehouse_id": warehouse,
                    },
                ).scalar()
                if balance_id:
                    conn.execute(
                        text(
                            """
                            update inv.inventory_lot_balance
                            set quantity_on_hand = :quantity,
                                quantity_allocated = 0,
                                quantity_available = :quantity,
                                is_active = true,
                                is_quarantined = false,
                                quarantine_reason = null,
                                qc_status = null,
                                updated_at = :now
                            where lot_balance_id = :balance_id
                            """
                        ),
                        {
                            "quantity": target.quantity,
                            "now": now,
                            "balance_id": balance_id,
                        },
                    )
                else:
                    conn.execute(
                        text(
                            """
                            insert into inv.inventory_lot_balance (
                                organization_id, lot_id, warehouse_id, quantity_on_hand,
                                quantity_allocated, quantity_available, is_active,
                                is_quarantined
                            )
                            values (
                                :org_id, :lot_id, :warehouse_id, :quantity,
                                0, :quantity, true, false
                            )
                            """
                        ),
                        {
                            "org_id": ORG_ID,
                            "lot_id": existing_lot,
                            "warehouse_id": warehouse,
                            "quantity": target.quantity,
                        },
                    )

                if adjustment:
                    conn.execute(
                        text(
                            """
                            insert into inv.inventory_transaction (
                                organization_id, transaction_type, transaction_date,
                                fiscal_period_id, item_id, warehouse_id, location_id,
                                lot_id, to_warehouse_id, to_location_id, quantity, uom,
                                unit_cost, total_cost, currency_code, cost_variance,
                                quantity_before, quantity_after, source_document_type,
                                source_document_id, source_document_line_id, reference,
                                reason_code, journal_entry_id, created_by_user_id
                            )
                            values (
                                :org_id, :transaction_type, :transaction_date,
                                :fiscal_period_id, :item_id, :warehouse_id, null,
                                :lot_id, null, null, :quantity, :uom,
                                :unit_cost, abs(:quantity) * :unit_cost, :currency_code, 0,
                                :quantity_before, :quantity_after, :source_document_type,
                                null, null, :reference, :reason_code, null, :created_by_user_id
                            )
                            """
                        ),
                        {
                            "org_id": ORG_ID,
                            "transaction_type": TRANSACTION_TYPE,
                            "transaction_date": now,
                            "fiscal_period_id": fiscal_period,
                            "item_id": item_id,
                            "warehouse_id": warehouse,
                            "lot_id": existing_lot,
                            "quantity": adjustment,
                            "uom": item["base_uom"],
                            "unit_cost": item["unit_cost"],
                            "currency_code": item["currency_code"],
                            "quantity_before": current_on_hand,
                            "quantity_after": target.quantity,
                            "source_document_type": SOURCE_DOCUMENT_TYPE,
                            "reference": REFERENCE,
                            "reason_code": REASON_CODE,
                            "created_by_user_id": user_id,
                        },
                    )

                if item["track_serial_numbers"]:
                    serial_set = list(dict.fromkeys(target.serials))
                    conn.execute(
                        text(
                            """
                            update inv.inventory_serial
                            set is_active = false,
                                updated_at = :now
                            where organization_id = :org_id
                              and item_id = :item_id
                              and warehouse_id = :warehouse_id
                              and serial_number <> all(:serials)
                            """
                        ),
                        {
                            "org_id": ORG_ID,
                            "item_id": item_id,
                            "warehouse_id": warehouse,
                            "serials": serial_set or ["__none__"],
                            "now": now,
                        },
                    )
                    for serial in serial_set:
                        serial_id = conn.execute(
                            text(
                                """
                                select serial_id
                                from inv.inventory_serial
                                where organization_id = :org_id
                                  and item_id = :item_id
                                  and serial_number = :serial_number
                                """
                            ),
                            {
                                "org_id": ORG_ID,
                                "item_id": item_id,
                                "serial_number": serial,
                            },
                        ).scalar()
                        if serial_id:
                            conn.execute(
                                text(
                                    """
                                    update inv.inventory_serial
                                    set lot_id = :lot_id,
                                        warehouse_id = :warehouse_id,
                                        location_id = null,
                                        status = 'AVAILABLE',
                                        is_active = true,
                                        updated_at = :now
                                    where serial_id = :serial_id
                                    """
                                ),
                                {
                                    "lot_id": existing_lot,
                                    "warehouse_id": warehouse,
                                    "now": now,
                                    "serial_id": serial_id,
                                },
                            )
                        else:
                            serial_id = conn.execute(
                                text(
                                    """
                                    insert into inv.inventory_serial (
                                        organization_id, item_id, serial_number,
                                        lot_id, warehouse_id, location_id, status,
                                        is_active, notes
                                    )
                                    values (
                                        :org_id, :item_id, :serial_number,
                                        :lot_id, :warehouse_id, null, 'AVAILABLE',
                                        true, :notes
                                    )
                                    returning serial_id
                                    """
                                ),
                                {
                                    "org_id": ORG_ID,
                                    "item_id": item_id,
                                    "serial_number": serial,
                                    "lot_id": existing_lot,
                                    "warehouse_id": warehouse,
                                    "notes": REFERENCE,
                                },
                            ).scalar_one()
                        conn.execute(
                            text(
                                """
                                insert into inv.inventory_serial_movement (
                                    organization_id, serial_id, transaction_id,
                                    movement_type, from_warehouse_id, to_warehouse_id,
                                    from_location_id, to_location_id, lot_id, reason,
                                    created_by_user_id
                                )
                                values (
                                    :org_id, :serial_id, null,
                                    'ADJUSTMENT', null, :warehouse_id,
                                    null, null, :lot_id, :reason, :created_by_user_id
                                )
                                """
                            ),
                            {
                                "org_id": ORG_ID,
                                "serial_id": serial_id,
                                "warehouse_id": warehouse,
                                "lot_id": existing_lot,
                                "reason": REFERENCE,
                                "created_by_user_id": user_id,
                            },
                        )
                    serial_count = len(serial_set)

            updated.append(
                {
                    "file_item_name": target.name,
                    "item_code": item["item_code"],
                    "item_name": item["item_name"],
                    "item_id": item_id,
                    "target_quantity": target.quantity,
                    "current_transaction_on_hand": current_on_hand,
                    "transaction_adjustment": adjustment,
                    "serial_count_from_file": len(set(target.serials)),
                    "serial_count_updated": serial_count
                    if args.apply
                    else len(set(target.serials)),
                    "track_serial_numbers": item["track_serial_numbers"],
                    "source_rows": target.source_rows,
                }
            )

        if not args.apply:
            conn.rollback()

    suffix = "applied" if args.apply else "dry_run"
    updated_path = REPORT_DIR / f"inventory_recon_325866_{suffix}_updated.csv"
    skipped_path = REPORT_DIR / f"inventory_recon_325866_{suffix}_skipped.csv"
    write_csv(
        updated_path,
        updated,
        [
            "file_item_name",
            "item_code",
            "item_name",
            "item_id",
            "target_quantity",
            "current_transaction_on_hand",
            "transaction_adjustment",
            "serial_count_from_file",
            "serial_count_updated",
            "track_serial_numbers",
            "source_rows",
        ],
    )
    write_csv(
        skipped_path,
        skipped,
        ["item_name", "target_quantity", "serials_from_file", "reason", "match_count"],
    )
    print(f"mode={suffix}")
    print(
        f"targets={len(targets)} updated_candidates={len(updated)} skipped={len(skipped)}"
    )
    print(f"updated_report={updated_path}")
    print(f"skipped_report={skipped_path}")
    total_adjustment = sum(
        Decimal(str(row["transaction_adjustment"])) for row in updated
    )
    print(f"total_adjustment={total_adjustment}")


if __name__ == "__main__":
    main()
