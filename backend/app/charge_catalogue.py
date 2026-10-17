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
    """A catalogue charge needs its SAC / HSN code and (when taxable) its GST rate before it can be used on a quotation or invoice.
    The notification reference and effective date are recorded when known but no longer block activation."""
    if str(data.get("catalogue", "")).lower() != "yes":
        return
    treatment = str(data.get("tax_treatment") or "taxable").lower()
    if treatment not in TAX_TREATMENTS:
        raise ValueError("Tax treatment must be taxable, exempt, nil_rated, non_gst, reverse_charge or zero_rated")
    missing = []
    if not str(data.get("hsn_code") or "").strip():
        missing.append("the SAC / HSN code")
    if treatment == "taxable" and str(data.get("gst_percent") or "").strip() == "":
        missing.append("the GST rate")
    if missing:
        raise ValueError("To activate this charge, enter " + " and ".join(missing) + " (open the charge, fill them in, set Status to Active)")


# Standard SAC codes used by freight forwarders (code, description, GST rate or "" when the rate depends on the service, charge category).
# PROPOSED reference data: confirm each code and rate against the current official notification before relying on it.
STANDARD_SAC = [
    ("9965", "Goods transport services (heading)", "", "Freight"),
    ("996511", "Road transport services of goods (incl. letting of goods carriages)", "", "Transportation / THC"),
    ("996512", "Railway transport services of goods", "5%", "Freight"),
    ("996521", "Coastal and transoceanic (sea-going) water transport services of goods", "", "Ocean Freight"),
    ("996522", "Inland water transport services of goods", "5%", "Freight"),
    ("996531", "Air transport services of goods (freight)", "18%", "Air Freight"),
    ("996601", "Rental services of road vehicles with operator", "", "Transportation / THC"),
    ("9967", "Supporting services in transport (heading)", "18%", "Handling"),
    ("996711", "Container handling services (THC, stuffing, destuffing)", "18%", "Handling"),
    ("996712", "Customs house agent services", "18%", "Custom Clearance"),
    ("996713", "Other cargo handling services", "18%", "Handling"),
    ("996721", "Refrigerated storage services", "18%", "Warehouse / Storage"),
    ("996722", "Storage and warehousing services of bulk liquids or gases", "18%", "Warehouse / Storage"),
    ("996729", "Other storage and warehousing services", "18%", "Warehouse / Storage"),
    ("996749", "Other supporting services for road transport", "18%", "Handling"),
    ("996759", "Other supporting services for water transport (port, agency)", "18%", "Documentation"),
    ("996769", "Other supporting services for air transport", "18%", "Handling"),
    ("996791", "Goods transport agency (GTA) services", "", "Freight"),
    ("996799", "Other supporting transport services n.e.c. (forwarding, documentation)", "18%", "Documentation"),
    ("998541", "Packaging and crating services", "18%", "Handling"),
    ("997134", "Marine, aviation and other transport insurance services", "18%", "Other"),
    ("9968", "Postal and courier services (heading)", "18%", "Courier"),
    ("996812", "Courier services", "18%", "Courier"),
]


# The specific SAC of every catalogue charge, with its tax treatment: code -> (SAC, tax treatment, GST %).
# PROPOSED defaults from the standard freight-forwarding practice in India (international import freight is exempt, export freight is zero-rated,
# statutory recoveries are outside GST); the company confirms them against the current notification and edits them in the Charge Master.
CHARGE_SAC = {
    "AIR-FRT-DOM": ("996531", "taxable", 18), "AIR-FRT-IMP": ("996531", "exempt", 0), "AIR-FRT-EXP": ("996531", "zero_rated", 0),
    "SEA-FRT-DOM": ("996521", "taxable", 5), "SEA-FRT-IMP": ("996521", "exempt", 0), "SEA-FRT-EXP": ("996521", "zero_rated", 0),
    "ROAD-NGTA": ("996511", "taxable", 18), "ROAD-GTA": ("996791", "taxable", 5), "RAIL-FRT": ("996512", "taxable", 5), "MULTIMODAL": ("996521", "taxable", 12),
    "CONT-TRANSPORT": ("996511", "taxable", 18), "COURIER": ("996812", "taxable", 18), "FFC": ("996799", "taxable", 18), "CUSTOMS-CLR": ("996712", "taxable", 18),
    "DOC": ("996799", "taxable", 18), "BL-CHG": ("996799", "taxable", 18), "DO-CHG": ("996799", "taxable", 18), "DOC-AMEND": ("996799", "taxable", 18),
    "PORT-AGENCY": ("996759", "taxable", 18), "EXP-DOC": ("996799", "taxable", 18), "IMP-DOC": ("996799", "taxable", 18), "CB-SERVICE": ("996712", "taxable", 18),
    "THC": ("996711", "taxable", 18), "STUFFING": ("996711", "taxable", 18), "DESTUFFING": ("996711", "taxable", 18), "CFS": ("996713", "taxable", 18),
    "ICD-HANDLING": ("996713", "taxable", 18), "LOADING": ("996713", "taxable", 18), "UNLOADING": ("996713", "taxable", 18), "PALLETIZATION": ("998541", "taxable", 18),
    "PACKING": ("998541", "taxable", 18), "WAREHOUSING": ("996729", "taxable", 18), "STORAGE": ("996729", "taxable", 18), "VGM": ("996713", "taxable", 18),
    "EXAM-HANDLING": ("996713", "taxable", 18), "CONT-SURVEY": ("996713", "taxable", 18), "CONT-CLEANING": ("996713", "taxable", 18), "INSURANCE": ("997134", "taxable", 18),
    "DEMURRAGE": ("996799", "taxable", 18), "DETENTION": ("996799", "taxable", 18), "VEHICLE-HIRE": ("996601", "taxable", 18), "COURIER-DOC": ("996812", "taxable", 18),
    "REPACKING": ("998541", "taxable", 18), "LABOUR": ("996713", "taxable", 18), "MISC-LOGISTICS": ("996799", "taxable", 18), "DUTY-RECOVERY": ("996712", "non_gst", 0),
    "GOVT-FEE-RECOVERY": ("996759", "non_gst", 0),
}
assert set(CHARGE_SAC) == {c[0] for c in CATALOGUE}, "every catalogue charge needs a SAC"
