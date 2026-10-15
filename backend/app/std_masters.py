"""Standard (system) records for the Container Type, Unit, Pack and Incoterm masters.

Every company receives them once (idempotent, never overwritten). System records may be deactivated but not deleted, and their codes are fixed.
The `data` keys are the slugs of the detail-form labels, so the generic master screens show and edit them without extra wiring.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import MasterRecord, Organization

TYPES = ("container_type", "unit", "pack", "incoterm")
SYSTEM_KEY = "system_default"


def is_system(record: MasterRecord) -> bool:
    return str((record.data or {}).get(SYSTEM_KEY, "")).lower() in ("yes", "true", "1")


# ---------------------------------------------------------------- containers
_DRY, _RF, _OT, _FR, _PL, _TK, _SP = "Dry Container", "Reefer", "Open Top", "Flat Rack", "Platform", "Tank", "Special Equipment"
_HC, _STD = "High Cube (9'6\")", "Standard (8'6\")"


def _box(code, name, iso, cat, size, height, group, ext, inn, door, cbm, tare, gross, temp=None, extra=None):
    d = {
        "iso_6346_code": iso, "category": cat, "size_feet": str(size), "height_type": height, "container_group": group,
        "length_mm": ext[0], "width_mm": ext[1], "height_mm": ext[2], "cubic_capacity_cbm": cbm,
        "internal_length_mm": inn[0], "internal_width_mm": inn[1], "internal_height_mm": inn[2], "door_opening_w_h_mm": door,
        "maximum_gross_weight_kg": gross, "tare_weight_kg": tare, "payload_kg": gross - tare, "csc_plate_required": "Yes",
        "remarks": "Nominal ISO 668 / 6346 values; check the carrier's equipment guide for the exact box.",
    }
    if temp:
        d.update({"temperature_range_min_c": temp[0], "temperature_range_max_c": temp[1], "genset_required": "No", "ventilation": "Yes"})
    d.update(extra or {})
    return (code, name, d)


CONTAINERS = [
    _box("20GP", "20' General Purpose", "22G1", _DRY, 20, _STD, "FCL Standard", (6058, 2438, 2591), (5898, 2352, 2393), "2340 x 2280", 33.2, 2300, 30480),
    _box("40GP", "40' General Purpose", "42G1", _DRY, 40, _STD, "FCL Standard", (12192, 2438, 2591), (12032, 2352, 2393), "2340 x 2280", 67.7, 3750, 30480),
    _box("40HC", "40' High Cube", "45G1", _DRY, 40, _HC, "FCL Standard", (12192, 2438, 2896), (12032, 2352, 2698), "2340 x 2585", 76.4, 3940, 30480),
    _box("45HC", "45' High Cube", "L5G1", _DRY, 45, _HC, "FCL Standard", (13716, 2438, 2896), (13556, 2352, 2698), "2340 x 2585", 86.0, 4800, 30480),
    _box("20RF", "20' Reefer", "22R1", _RF, 20, _STD, "FCL Standard", (6058, 2438, 2591), (5444, 2294, 2276), "2290 x 2260", 28.4, 3000, 30480, (-30, 30)),
    _box("40RF", "40' Reefer", "42R1", _RF, 40, _STD, "FCL Standard", (12192, 2438, 2591), (11584, 2294, 2250), "2290 x 2260", 59.8, 4800, 34000, (-30, 30)),
    _box("40RH", "40' High Cube Reefer", "45R1", _RF, 40, _HC, "FCL Standard", (12192, 2438, 2896), (11584, 2294, 2506), "2290 x 2516", 67.3, 4900, 34000, (-30, 30)),
    _box("20OT", "20' Open Top", "22U1", _OT, 20, _STD, "Special Equipment", (6058, 2438, 2591), (5898, 2352, 2348), "2340 x 2280", 32.5, 2300, 30480),
    _box("40OT", "40' Open Top", "42U1", _OT, 40, _STD, "Special Equipment", (12192, 2438, 2591), (12032, 2352, 2330), "2340 x 2280", 65.9, 3900, 30480),
    _box("20FR", "20' Flat Rack", "22P3", _FR, 20, _STD, "Special Equipment", (6058, 2438, 2591), (5638, 2230, 2233), "Open ends", 28.0, 2700, 34000),
    _box("40FR", "40' Flat Rack", "42P3", _FR, 40, _STD, "Special Equipment", (12192, 2438, 2591), (11800, 2130, 1960), "Open ends", 49.0, 5000, 45000),
    _box("40PL", "40' Platform", "42P1", _PL, 40, _STD, "Special Equipment", (12192, 2438, 2591), (12080, 2438, 0), "Open platform", 0, 4500, 45000),
    _box("20TK", "20' Tank Container (ISO)", "22T6", _TK, 20, _STD, "Special Equipment", (6058, 2438, 2591), (6058, 2438, 2591), "Top / bottom discharge", 26.0, 3700, 36000),
    _box("20TKB", "20' Bulk Liquid Tank", "22B0", _TK, 20, _STD, "Special Equipment", (6058, 2438, 2591), (6058, 2438, 2591), "Top / bottom discharge", 24.0, 3900, 36000),
    _box("20BK", "20' Dry Bulk", "22B1", _TK, 20, _STD, "Special Equipment", (6058, 2438, 2591), (5898, 2352, 2393), "Roof hatches / rear door", 31.0, 2500, 30480),
    _box("20HT", "20' Hard Top", "22U6", _SP, 20, _STD, "Special Equipment", (6058, 2438, 2591), (5898, 2352, 2348), "2340 x 2280", 32.0, 2400, 30480),
    _box("40HT", "40' Hard Top", "42U6", _SP, 40, _STD, "Special Equipment", (12192, 2438, 2591), (12032, 2352, 2330), "2340 x 2280", 65.0, 4100, 30480),
    _box("20VT", "20' Ventilated", "22V0", _SP, 20, _STD, "Special Equipment", (6058, 2438, 2591), (5898, 2352, 2393), "2340 x 2280", 33.0, 2400, 30480),
]

# ---------------------------------------------------------------- units
_UNIT_CATS = {
    "Weight": [("KG", "Kilogram", 1, "kg", 3), ("G", "Gram", 0.001, "g", 3), ("MT", "Metric Ton", 1000, "t", 3), ("QTL", "Quintal", 100, "q", 2), ("LB", "Pound", 0.45359237, "lb", 3)],
    "Volume": [("CBM", "Cubic Metre", 1, "m³", 3), ("LTR", "Litre", 0.001, "L", 3), ("CFT", "Cubic Foot", 0.0283168, "ft³", 3), ("GAL", "Gallon (US)", 0.00378541, "gal", 3)],
    "Length": [("M", "Metre", 1, "m", 2), ("CM", "Centimetre", 0.01, "cm", 1), ("MM", "Millimetre", 0.001, "mm", 0), ("KM", "Kilometre", 1000, "km", 2), ("FT", "Foot", 0.3048, "ft", 2), ("IN", "Inch", 0.0254, "in", 1)],
    "Area": [("SQM", "Square Metre", 1, "m²", 2), ("SQFT", "Square Foot", 0.092903, "ft²", 2)],
    "Quantity": [("PCS", "Pieces", 1, "pcs", 0), ("NOS", "Numbers", 1, "nos", 0), ("SET", "Set", 1, "set", 0), ("PR", "Pair", 2, "pr", 0), ("DOZ", "Dozen", 12, "doz", 0), ("TEU", "Twenty-foot Equivalent Unit", 1, "TEU", 0), ("FEU", "Forty-foot Equivalent Unit", 2, "FEU", 0)],
    "Time": [("DAY", "Day", 1, "d", 0), ("HRS", "Hours", 0.0416667, "h", 0), ("WK", "Week", 7, "wk", 0), ("MTH", "Month", 30, "mo", 0)],
    "Temperature": [("DEGC", "Degree Celsius", 1, "°C", 1), ("DEGF", "Degree Fahrenheit", 1, "°F", 1), ("KELV", "Kelvin", 1, "K", 1)],
}
UNITS = []
for _cat, _items in _UNIT_CATS.items():
    for _i, (_c, _n, _f, _s, _p) in enumerate(_items):
        UNITS.append((_c, _n, {"category": _cat, "decimal_precision": _p, "conversion_factor_to_base": _f, "base_unit_for_category": "Yes" if _i == 0 else "No", "symbol": _s,
                               "remarks": "Temperature units convert by formula, not by a single factor." if _cat == "Temperature" else ""}))

# ---------------------------------------------------------------- packages (UN/ECE Recommendation 21)
PACKS = [(c, n, {"un_ece_code": u, "description": d}) for c, n, u, d in [
    ("CTN", "Carton", "CT", "Cardboard carton"), ("PLT", "Pallet", "PX", "Pallet, modular"), ("BOX", "Box", "BX", "Box (wood, plastic or metal)"),
    ("BAG", "Bag", "BG", "Bag (paper, plastic or woven)"), ("SAC", "Sack", "SA", "Sack"), ("DRM", "Drum", "DR", "Drum (steel, plastic or fibre)"),
    ("BAL", "Bale", "BL", "Bale, compressed"), ("BDL", "Bundle", "BE", "Bundle"), ("CAS", "Case", "CS", "Case"), ("CRT", "Crate", "CR", "Crate"),
    ("PKG", "Package", "PK", "Package, unspecified"), ("ROL", "Roll", "RO", "Roll"), ("TNK", "Tank", "TK", "Tank, rectangular"),
    ("CAN", "Can", "CA", "Can, rectangular"), ("BTL", "Bottle", "BO", "Bottle, non-protected, cylindrical"), ("JAR", "Jar", "JR", "Jar"),
    ("PCS", "Piece", "PC", "Piece"), ("UNP", "Unpacked", "NE", "Unpacked or unpackaged"), ("LSE", "Loose / Bulk", "VR", "Bulk, solid or loose"),
    ("CSK", "Cask", "CK", "Cask"), ("BKT", "Bucket", "BJ", "Bucket"), ("PAL", "Pail", "PL", "Pail"), ("TUB", "Tube", "TU", "Tube"),
    ("TRY", "Tray", "PU", "Tray / tray pack"), ("SKD", "Skid", "SI", "Skid"), ("IBC", "Intermediate Bulk Container", "IB", "Intermediate bulk container"),
]]

# ---------------------------------------------------------------- Incoterms 2020
_ANY, _SEA = "Any mode of transport", "Sea and inland waterway only"


def _inco(code, name, scope, risk, insurance, export, imp, freight, desc):
    sea = scope == _SEA
    return (code, name, {
        "version": "Incoterms 2020", "description": desc, "sea_freight": "✓ Applicable", "air_freight": "Not Applicable" if sea else "✓ Applicable",
        "road_rail": "Not Applicable" if sea else "✓ Applicable", "multimodal": "Not Applicable" if sea else "✓ Applicable",
        "risk_transfer_point": risk, "insurance": insurance, "export_clearance": export, "import_clearance": imp, "freight_payable": freight, "applicability": scope,
    })


INCOTERMS = [
    _inco("EXW", "Ex Works", _ANY, "Seller's premises, goods placed at buyer's disposal", "Buyer", "Buyer", "Buyer", "Buyer", "Seller makes the goods available at its premises; the buyer bears all costs and risks from there."),
    _inco("FCA", "Free Carrier", _ANY, "Handed to the buyer's carrier at the named place", "Buyer", "Seller", "Buyer", "Buyer", "Seller delivers the cleared goods to the carrier named by the buyer."),
    _inco("CPT", "Carriage Paid To", _ANY, "Handed to the first carrier", "Buyer", "Seller", "Buyer", "Seller", "Seller pays carriage to the named destination; risk passes when the goods are handed to the first carrier."),
    _inco("CIP", "Carriage and Insurance Paid To", _ANY, "Handed to the first carrier", "Seller", "Seller", "Buyer", "Seller", "As CPT, with the seller also insuring the goods (Institute Cargo Clauses A)."),
    _inco("DAP", "Delivered at Place", _ANY, "At the named destination, ready for unloading", "Seller / Optional", "Seller", "Buyer", "Seller", "Seller delivers when the goods are ready for unloading at the named place; the buyer clears import."),
    _inco("DPU", "Delivered at Place Unloaded", _ANY, "At the named destination, unloaded", "Seller / Optional", "Seller", "Buyer", "Seller", "Seller delivers and unloads the goods at the named place of destination."),
    _inco("DDP", "Delivered Duty Paid", _ANY, "At the named destination, import-cleared", "Seller / Optional", "Seller", "Seller", "Seller", "Seller bears all costs and risks including import clearance and duties."),
    _inco("FAS", "Free Alongside Ship", _SEA, "Alongside the vessel at the port of shipment", "Buyer", "Seller", "Buyer", "Buyer", "Seller delivers when the goods are placed alongside the vessel named by the buyer."),
    _inco("FOB", "Free on Board", _SEA, "On board the vessel at the port of shipment", "Buyer", "Seller", "Buyer", "Buyer", "Seller delivers the goods on board the vessel named by the buyer."),
    _inco("CFR", "Cost and Freight", _SEA, "On board the vessel at the port of shipment", "Buyer", "Seller", "Buyer", "Seller", "Seller pays the freight to the destination port; risk passes once the goods are on board."),
    _inco("CIF", "Cost, Insurance and Freight", _SEA, "On board the vessel at the port of shipment", "Seller", "Seller", "Buyer", "Seller", "As CFR, with the seller also insuring the goods (minimum Institute Cargo Clauses C)."),
]

STANDARD = {"container_type": CONTAINERS, "unit": UNITS, "pack": PACKS, "incoterm": INCOTERMS}


def seed_org(db: Session, org_id: int, types=TYPES, user_id: int | None = None) -> dict:
    """Create the missing standard records for one company; existing records are never changed."""
    out = {}
    for t in types:
        have = {c for (c,) in db.execute(select(MasterRecord.code).where(MasterRecord.organization_id == org_id, MasterRecord.master_type == t))}
        created = 0
        for code, name, data in STANDARD[t]:
            if code in have:
                continue
            db.add(MasterRecord(organization_id=org_id, master_type=t, code=code, name=name, status="ACTIVE", created_by=user_id, data={**data, SYSTEM_KEY: "yes", "source": "standard"}))
            created += 1
        out[t] = {"created": created, "standard": len(STANDARD[t])}
    db.flush()
    return out


def seed_all_orgs(db: Session) -> None:
    for org_id in db.scalars(select(Organization.id)):
        seed_org(db, org_id)
    db.commit()
