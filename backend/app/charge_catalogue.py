"""Standard freight-forwarding charge catalogue (India) with proposed SAC headings and GST rates.

These are PROPOSED defaults for review, not a legal determination. Every imported charge is created INACTIVE with
tax_status = REVIEW_REQUIRED. It can only be activated after the exact SAC, rate (or tax treatment), the official
notification reference and the effective date are recorded (enforced in masters_api), and that activation is audited.
"""

# (code, name, group, sac heading or "", classification note, proposed GST % or None, tax configuration note)
T = "9965"
S = "9967"
C = "9968"
CATALOGUE = [
    ("AIR-FRT-DOM", "Air Freight — Domestic", "Freight", T, "Goods transport services", 18, "Verify service and transaction"),
    ("AIR-FRT-IMP", "Air Freight — International Import", "Freight", T, "Goods transport services", None, "Verify import-freight rules"),
    ("AIR-FRT-EXP", "Air Freight — International Export", "Freight", T, "Goods transport services", None, "Verify export / zero-rating eligibility"),
    ("SEA-FRT-DOM", "Sea Freight — Domestic", "Freight", T, "Goods transport services", 5, "Subject to applicable conditions"),
    ("SEA-FRT-IMP", "Sea Freight — International Import", "Freight", T, "Goods transport services", None, "Verify import-freight rules"),
    ("SEA-FRT-EXP", "Sea Freight — International Export", "Freight", T, "Goods transport services", None, "Verify export / zero-rating eligibility"),
    ("ROAD-NGTA", "Road Freight — Non-GTA", "Freight", T, "Goods transport services", None, "Exemptions and conditions may apply"),
    ("ROAD-GTA", "Road Freight — GTA", "Freight", T, "Goods transport services", None, "5% or 18% depending on the GTA tax option and conditions"),
    ("RAIL-FRT", "Rail Freight", "Freight", T, "Goods transport services", 5, "Subject to applicable conditions"),
    ("MULTIMODAL", "Multimodal Transportation", "Freight", T, "Multimodal transport", 12, "Verify service definition and current rate"),
    ("CONT-TRANSPORT", "Container Transportation", "Freight", T, "Goods transport services", None, "Depends on supplier and service type"),
    ("COURIER", "Courier Charges", "Freight", C, "Postal and courier services", 18, "Verify applicable classification"),
    ("FFC", "Freight Forwarding Charges", "Documentation", "", "Supporting / forwarding service", 18, "Verify exact SAC"),
    ("CUSTOMS-CLR", "Customs Clearance Charges", "Customs", "", "Customs clearance service", 18, "Verify exact SAC and supply"),
    ("DOC", "Documentation Charges", "Documentation", "", "Documentation / supporting service", 18, "Verify exact SAC"),
    ("BL-CHG", "Bill of Lading Charges", "Documentation", "", "Shipping documentation service", 18, "Verify exact SAC"),
    ("DO-CHG", "Delivery Order Charges", "Documentation", "", "Shipping documentation service", 18, "Verify exact SAC"),
    ("DOC-AMEND", "Documentation Amendment Charges", "Documentation", "", "Documentation service", 18, "Verify exact SAC"),
    ("PORT-AGENCY", "Port Agency Charges", "Documentation", "", "Shipping agency / supporting service", 18, "Verify exact SAC"),
    ("EXP-DOC", "Export Documentation Charges", "Documentation", "", "Documentation service", 18, "Verify export-related tax treatment"),
    ("IMP-DOC", "Import Documentation Charges", "Documentation", "", "Documentation service", 18, "Verify exact SAC"),
    ("CB-SERVICE", "Customs Broker Service Charges", "Customs", "", "Customs clearance service", 18, "Verify exact SAC"),
    ("THC", "Terminal Handling Charges (THC)", "Handling", S, "Supporting services in transport", 18, "Verify exact service"),
    ("STUFFING", "Container Stuffing Charges", "Handling", S, "Supporting services in transport", 18, "Verify exact SAC"),
    ("DESTUFFING", "Container Destuffing Charges", "Handling", S, "Supporting services in transport", 18, "Verify exact SAC"),
    ("CFS", "CFS Charges", "Handling", "", "Cargo handling / storage service", 18, "Verify exact service"),
    ("ICD-HANDLING", "ICD Handling Charges", "Handling", "", "Cargo handling / supporting service", 18, "Verify exact service"),
    ("LOADING", "Loading Charges", "Handling", "", "Cargo handling service", 18, "Verify exact SAC"),
    ("UNLOADING", "Unloading Charges", "Handling", "", "Cargo handling service", 18, "Verify exact SAC"),
    ("PALLETIZATION", "Palletization Charges", "Handling", "", "Packing / cargo handling service", 18, "Verify exact SAC"),
    ("PACKING", "Packing Charges", "Handling", "", "Packing service", 18, "Verify exact SAC"),
    ("WAREHOUSING", "Warehousing Charges", "Warehouse", "", "Warehousing service", 18, "Verify exact SAC"),
    ("STORAGE", "Storage Charges", "Warehouse", "", "Storage service", 18, "Verify exact SAC"),
    ("VGM", "VGM Charges", "Handling", "", "Weighing / cargo service", 18, "Verify exact SAC"),
    ("EXAM-HANDLING", "Examination Handling Charges", "Handling", "", "Cargo handling service", 18, "Verify exact service"),
    ("CONT-SURVEY", "Container Survey Charges", "Handling", "", "Inspection / survey service", 18, "Verify exact SAC"),
    ("CONT-CLEANING", "Container Cleaning Charges", "Handling", "", "Cleaning service", 18, "Verify exact SAC"),
    ("INSURANCE", "Insurance Service Charges", "Other", "", "Insurance service", None, "Verify applicable insurance rate"),
    ("DEMURRAGE", "Demurrage Charges", "Other", "", "Depends on underlying service", None, "Verify classification and contract"),
    ("DETENTION", "Detention Charges", "Other", "", "Depends on underlying service", None, "Verify classification and contract"),
    ("VEHICLE-HIRE", "Vehicle Hire with Operator", "Other", "", "Transport vehicle rental", None, "Verify applicable rental category"),
    ("COURIER-DOC", "Courier Documentation Charges", "Other", "", "Courier / documentation service", 18, "Verify exact service"),
    ("REPACKING", "Repacking Charges", "Other", "", "Packing service", 18, "Verify exact SAC"),
    ("LABOUR", "Labour Charges", "Other", "", "Depends on work performed", 18, "Verify exact service"),
    ("MISC-LOGISTICS", "Miscellaneous Logistics Charges", "Other", "", "Determine actual service", 18, "Must be reviewed before activation"),
    ("DUTY-RECOVERY", "Customs Duty Recovery", "Other", "", "Statutory duty / reimbursement", None, "Not automatically a SAC rate; configure separately"),
    ("GOVT-FEE-RECOVERY", "Port or Government Fee Recovery", "Other", "", "Depends on the underlying transaction", None, "Verify pure-agent and other applicable rules"),
]
HEADINGS = {T: "Goods transport services", S: "Supporting services in transport", C: "Postal and courier services"}
TAX_TREATMENTS = ("taxable", "exempt", "nil_rated", "non_gst", "reverse_charge", "zero_rated")


def validate_activation(data: dict) -> None:
    """Raise ValueError when a catalogue charge is activated without verified tax configuration."""
    if str(data.get("catalogue", "")).lower() != "yes":
        return
    treatment = str(data.get("tax_treatment") or "taxable").lower()
    if treatment not in TAX_TREATMENTS:
        raise ValueError("Tax treatment must be taxable, exempt, nil_rated, non_gst, reverse_charge or zero_rated")
    missing = []
    if not str(data.get("hsn_code") or "").strip():
        missing.append("the exact SAC / HSN code")
    if treatment == "taxable" and str(data.get("gst_percent") or "").strip() == "":
        missing.append("the GST rate")
    if not str(data.get("notification_ref") or "").strip():
        missing.append("the official notification reference")
    if not str(data.get("effective_date") or "").strip():
        missing.append("the effective date")
    if missing:
        raise ValueError("Verify the tax configuration before activating this charge: " + ", ".join(missing) + " must be recorded")
