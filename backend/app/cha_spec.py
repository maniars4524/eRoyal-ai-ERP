"""Form definitions of the CHA Import job (Bill of Entry): the tabs after General. One definition feeds the screen and the server-side validation of what is saved.
A tab is a list of collections; a collection is either `rows` (a table with a small form: IGM, bonds, certificates) or `records` (numbered records such as invoices and items, each with several sections).
Field = (key, label[, type[, options]]) with type text (default), date, number, area, check or select."""
from __future__ import annotations

YES_NO = ["Yes", "No"]
TERMS = ["FOB", "CIF", "CFR", "C&I", "CNF", "EXW", "FCA", "CPT", "CIP", "DAP", "DDP"]
CCY = "text"


def F(key, label, typ="text", opts=None):
    d = {"k": key, "l": label}
    if typ != "text":
        d["t"] = typ
    if opts:
        d["o"] = opts
    return d


IMPORT_SPEC = [
    {"tab": "IGM", "collections": [
        {"key": "igm", "kind": "rows", "title": "IGM details", "fields": [
            F("airline", "Airline"), F("flight_no", "Flight / vessel no"), F("flight_date", "Flight / inward date", "date"), F("igm_no", "IGM no"), F("igm_date", "IGM date", "date"), F("mawb_no", "MAWB / MBL no"), F("mawb_date", "MAWB / MBL date", "date"),
            F("hawb_no", "HAWB / HBL no"), F("hawb_date", "HAWB / HBL date", "date"), F("line_number", "Line number"), F("shipping_line", "Shipping line"), F("voyage", "Voyage"), F("packages", "Packages", "number"), F("package_unit", "Package unit"),
            F("gross_weight", "Gross weight", "number"), F("weight_unit", "Weight unit"), F("marks", "Marks & numbers", "area"), F("gateway_igm_no", "Gateway IGM no"), F("gateway_igm_date", "Gateway IGM date", "date"), F("gateway_port_code", "Gateway port code")]},
        {"key": "bonds", "kind": "rows", "title": "Bond", "fields": [
            F("bond_no", "Bond no"), F("bond_location", "Bond location"), F("bond_type", "Bond type", "select", ["CE - Central excise cert"]), F("available_bond_amount", "Available bond amount", "number"), F("bond_amount", "Bond amount", "number")]},
        {"key": "certs", "kind": "rows", "title": "Certificate", "fields": [
            F("cert_no", "Certificate no"), F("cert_date", "Certificate date", "date"), F("cert_type", "Certificate type", "select", ["CE - Central excise cert"])]},
    ]},
    {"tab": "Invoice", "collections": [
        {"key": "invoices", "kind": "records", "title": "Invoice", "sections": [
            {"title": "Main details", "fields": [F("invoice_no", "Invoice no"), F("invoice_date", "Invoice date", "date"), F("terms", "Terms", "select", TERMS), F("currency", "Currency"), F("invoice_value", "Invoice value", "number"), F("exchange_rate", "Exchange rate", "number"),
                                                F("single_freight_insurance", "Single freight & insurance for all invoices", "check")]},
            {"title": "Freight & misc", "fields": [F("misc_ccy", "Misc currency"), F("misc_exch", "Misc exchange rate", "number"), F("misc_charge", "Misc charge", "number"), F("agency_ccy", "Agency currency"), F("agency_exch", "Agency exchange rate", "number"), F("agency_charge", "Agency charge", "number"),
                                         F("discount_ccy", "Discount currency"), F("discount_exch", "Discount exchange rate", "number"), F("discount", "Discount", "number"), F("discount_reason", "Discount reason"), F("loading_charge", "Loading", "number"),
                                         F("freight_ccy", "Freight currency"), F("freight_exch", "Freight exchange rate", "number"), F("freight_charge", "Freight", "number"), F("insurance_ccy", "Insurance currency"), F("insurance_exch", "Insurance exchange rate", "number"),
                                         F("insurance_charge", "Insurance", "number"), F("high_sea_sale_ccy", "High sea sale currency"), F("high_sea_sale_charge", "High sea sale", "number"), F("revenue_deposit_pct", "Revenue deposit %", "number"), F("revenue_deposit_on", "Revenue deposit on")]},
            {"title": "Other charges", "fields": [F("brokerage_commission", "Brokerage & commission", "number"), F("container_cost", "Cost of container", "number"), F("packaging_cost", "Cost of packaging", "number"), F("handling_charges", "Handling charges", "number"),
                                               F("goods_services_cost", "Cost of goods and services", "number"), F("documentation", "Documentation", "number"), F("coo_certificate", "Country of origin certificate", "number"), F("royalties", "Royalties and licence fees", "number"),
                                               F("proceeds_accrue", "Value of proceeds which accrue", "number"), F("warranty_cost", "Cost of warranty services", "number"), F("other_cost_payment", "Other cost of payment", "number"),
                                               F("other_charges_payment", "Other charges & payment", "number"), F("loading_charges", "Loading charges", "number"), F("unloading_charges", "Unloading charges", "number")]},
            {"title": "Supplier", "fields": [F("supplier_name", "Supplier name"), F("branch_sl_no", "Branch sl no"), F("supp_address1", "Address 1"), F("supp_address2", "Address 2"), F("supp_country", "Country"), F("supp_pin", "Pin code"),
                                           F("broker_name", "Broker name"), F("broker_address", "Broker address"), F("broker_country", "Broker country"), F("broker_pin", "Broker pin"), F("seller_name", "Seller name"), F("seller_address", "Seller address"),
                                           F("seller_country", "Seller country"), F("seller_pin", "Seller pin")]},
            {"title": "SVB", "fields": [F("svb_relation", "SVB relation", "check"), F("svb_ref_no", "SVB ref no"), F("svb_date", "SVB date", "date"), F("svb_custom_house", "SVB custom house"), F("loading_on", "Loading on"), F("assessable_rate", "Assessable rate", "number"),
                                        F("assessable_status", "Assessable status"), F("duty_rate", "Duty rate", "number"), F("duty_status", "Duty status")]},
            {"title": "Other details", "fields": [F("po_no", "Purchase order no"), F("po_date", "Purchase date", "date"), F("contract_no", "Contract no"), F("contract_date", "Contract date", "date"), F("lc_no", "LC no"), F("lc_date", "LC date", "date"),
                                                 F("nature_of_trans", "Nature of transaction", "select", ["S - Sale", "P - Purchase"]), F("nature_of_payment", "Nature of payment", "select", ["OTH - Others", "LC - Letter of credit", "TT - Telegraphic transfer"]),
                                                 F("conditions", "Conditions", "area"), F("valuation_method", "Valuation method", "select", ["Rule 3 - Identical goods", "Rule 4 - Transaction Value", "Rule 5 - Similar goods", "Rule 7 - Deductive", "Rule 8 - Computed", "Rule 9 - Fall back"]),
                                                 F("sap_delivery_id", "SAP delivery ID"), F("ship_date", "Ship date", "date"), F("invoice_level", "Invoice level"), F("country_departure", "Country of departure")]},
            {"title": "Third party", "fields": [F("third_party_name", "Third party name"), F("third_party_address1", "Address 1"), F("third_party_address2", "Address 2"), F("third_party_city", "City"), F("country_sub_division", "Country sub division"),
                                              F("third_party_country", "Country code"), F("third_party_pin", "Pin"), F("terms_place", "Terms place"), F("aeo_code", "Authorized Economic Operator code"), F("aeo_country", "AEO country"), F("aeo_role", "AEO role"),
                                              F("buyer_seller_related", "Buyer and seller related", "select", YES_NO)]},
        ]},
    ]},
    {"tab": "Product", "collections": [
        {"key": "items", "kind": "records", "title": "Item", "link": "invoices", "sections": [
            {"title": "Item main", "fields": [F("invoice_sr", "Invoice sr no", "number"), F("description", "Item description", "area"), F("qty", "Item quantity", "number"), F("qty_unit", "Unit (UQC)"), F("unit_price", "Unit price", "number"), F("price_ccy", "Price currency"),
                                              F("foc_item", "FOC item", "select", YES_NO), F("amount", "Item amount", "number"), F("amount_ccy", "Amount currency"), F("ritc_no", "RITC no"), F("info_msr", "Info MSR", "number"), F("info_uqc", "Info UQC")]},
            {"title": "General", "fields": [F("cth_qty1", "CTH quantity 1", "number"), F("cth_qty1_unit", "Unit 1"), F("cth_qty2", "CTH quantity 2", "number"), F("cth_qty2_unit", "Unit 2"), F("generic_description", "Generic description", "area"),
                                            F("manufacturer", "Manufacturer"), F("brand", "Brand"), F("model", "Model"), F("end_use", "End use"), F("country_origin", "Country of origin"), F("part_no", "Part no"), F("accessories", "Accessories", "area"),
                                            F("previous_be_no", "Previous BE no"), F("previous_be_date", "Previous BE date", "date"), F("unit_price_note", "Unit price"), F("customs_house", "Customs house")]},
            {"title": "CTH", "fields": [F("scheme_code", "Scheme code"), F("scheme_notn", "Scheme notification"), F("scheme_notn_sl", "Scheme notification sl"), F("cth_no", "CTH no"), F("ritc_cth", "RITC no"), F("basic_duty_notn", "Basic duty notification"),
                                         F("basic_duty_pct", "Basic duty %", "number"), F("social_welfare_pct", "Social welfare %", "number"), F("ncd_notn", "NCD notification"), F("ncd_pct", "NCD %", "number"), F("aggregate_duty", "Aggregate duty", "number"),
                                         F("safeguard_pct", "Safeguard duty %", "number"), F("sapta_notn", "SAPTA notification"), F("tariff_value", "Tariff value", "number"), F("aid_cess_notn", "AID cess notification"), F("aid_cess_pct", "AID cess %", "number")]},
            {"title": "CETH", "fields": [F("ceth_no", "CETH no"), F("cvd_pct", "CVD %", "number"), F("addl_excise_sia", "Addl duty of excise (SIA)", "number"), F("spl_excise", "Spl. excise duty (Sched-11)", "number"), F("addl_excise_tta", "Addl duty of excise (TTA)", "number"),
                                          F("health_cess", "Health cess", "number"), F("cess_notification", "Cess & notification"), F("other_duty_notn", "Other duty notification"), F("other_duty_pct", "Other duty %", "number"), F("infra_cess", "Infra cess", "number"),
                                          F("mrp_duty", "MRP duty", "check"), F("rsp_applicability", "RSP applicability", "select", ["N", "Y"])]},
            {"title": "IGST", "fields": [F("levy_notn_no", "Levy notification no"), F("levy_notn_sl", "Levy sl"), F("levy_pct", "Levy %", "number"), F("exemption_notn_no", "Exemption notification no"), F("exemption_pct", "Exemption %", "number"),
                                          F("levy_cp_cess_notn_no", "Levy C.P. cess notification no"), F("levy_cp_cess_pct", "Levy C.P. cess %", "number"), F("compliance_requirements", "Compulsory compliance requirements (CCR)", "area")]},
            {"title": "Item SVB", "fields": [F("svb_relation", "SVB relation", "check"), F("svb_ref_no", "SVB ref no"), F("svb_date", "SVB date", "date"), F("svb_custom_house", "SVB custom house"), F("loading_on", "Loading on"), F("assessable_rate", "Assessable rate", "number"), F("duty_rate", "Duty rate", "number")]},
            {"title": "Re-import", "fields": [F("sb_no", "SB no"), F("sb_date", "SB date", "date"), F("sb_invoice_sl", "SB invoice sl no"), F("sb_invoice_item_no", "SB invoice item no"), F("port_export", "Port of export"), F("reimp_notn_no", "Notification no"), F("reimp_notn_sl", "Notification sl no"),
                                           F("export_freight", "Export freight", "number"), F("export_insurance", "Export insurance", "number"), F("customs_duty", "Customs duty", "number"), F("excise_duty", "Excise duty", "number")]},
            {"title": "ADD", "fields": [F("add_notn_no", "Anti-dumping notification no"), F("add_notn_sl", "Notification sl no"), F("add_pct", "ADD %", "number"), F("add_amount", "ADD amount", "number"), F("add_cvd_notn_no", "CVD / safeguard notification no"), F("add_cvd_pct", "CVD / safeguard %", "number")]},
            {"title": "Licence", "fields": [F("licence_type", "Licence type"), F("licence_no", "Licence no"), F("licence_date", "Licence date", "date"), F("licence_port", "Licence port"), F("licence_sl_no", "Licence item sl no"), F("licence_debit_qty", "Debit quantity", "number"), F("licence_debit_unit", "Debit unit"), F("licence_debit_value", "Debit value", "number")]},
            {"title": "S/W", "fields": [F("sw_info_type", "Single-window info type"), F("sw_info_code", "Info code"), F("sw_info_qualifier", "Info qualifier"), F("sw_info_text", "Info text", "area"), F("sw_info_measure", "Info measure", "number"), F("sw_info_uqc", "Info UQC")]},
            {"title": "Declaration", "fields": [F("stmt_type", "Statement type"), F("stmt_code", "Statement code"), F("stmt_text", "Statement text", "area")]},
            {"title": "Supporting document", "fields": [F("doc_level", "Level", "select", ["Job Level", "Invoice Level", "Item Level"]), F("image_ref_no", "Image ref no"), F("doc_type_code", "Document type code"), F("iss_party_name", "Issuing party name"),
                                                      F("place_of_issue", "Place of issue"), F("issue_date", "Issue date", "date"), F("expiry_date", "Expiry date", "date"), F("doc_no", "Document no"), F("file_type", "File type"), F("icegate_id", "ICEGATE ID")]},
        ]},
    ]},
]


# parts shared by Import and Export jobs
COMMON_SPEC = [
    {"tab": "e-Way Bill", "collections": [
        {"key": "eway", "kind": "form", "title": "e-Way Bill", "sections": [
            {"title": "Bill from", "fields": [F("from_name", "Name"), F("from_gstn", "GSTN ID"), F("from_state", "State"), F("from_address1", "Address 1"), F("from_address2", "Address 2"), F("from_place", "Place"), F("from_pin", "Pin"), F("from_actual_state", "Actual state"), F("from_subsup_desc", "Sub-supply description")]},
            {"title": "Bill to", "fields": [F("to_name", "Name"), F("to_gstn", "GSTN ID"), F("to_state", "State"), F("to_address1", "Address 1"), F("to_address2", "Address 2"), F("to_place", "Place"), F("to_pin", "Pin"), F("to_actual_state", "Actual state"), F("cess_non_advol", "Cess non-advol amount", "number"), F("to_other", "Other")]},
            {"title": "Transport", "fields": [F("transport_name", "Transport name"), F("transport_id", "Transport ID"), F("distance_km", "Approx. distance (km)", "number"), F("transport_mode", "Mode", "select", ["Road", "Rail", "Air", "Ship"]), F("transport_doc_no", "Transport document no"), F("vehicle_no", "Vehicle no"),
                                         F("vehicle_type", "Vehicle type", "select", ["Regular", "Over Dim. Cargo"]), F("transport_date", "Transport date", "date"), F("user_gstn", "User GSTN ID"), F("eway_no", "e-Way bill no"), F("eway_date", "e-Way bill date", "date")]},
        ]},
    ]},
    {"tab": "Job History", "collections": [
        {"key": "notes", "kind": "rows", "title": "Job notes", "fields": [F("note_date", "Date", "date"), F("note_by", "By"), F("note_text", "Note", "area")]},
    ]},
    {"tab": "Bill to party", "collections": [
        {"key": "billto", "kind": "form", "title": "Bill to party", "sections": [
            {"title": "Party", "fields": [F("party_name", "Party name"), F("branch_sl_no", "Branch sl no"), F("address1", "Address 1"), F("address2", "Address 2"), F("address3", "Address 3"), F("pincode", "Pincode"), F("state_name", "State"), F("gstn", "GSTN number")]},
        ]},
    ]},
]

YN = ["Yes", "No"]
TERMS_X = ["CIF", "FOB", "CFR", "C&I", "CNF", "EXW", "FCA", "CPT", "CIP", "DAP", "DDP"]


def _money(label, key):
    return [F(key + "_pct", label + " rate %", "number"), F(key + "_amt", label + " amount", "number"), F(key + "_ccy", label + " currency")]


EXPORT_SPEC = [
    {"tab": "Invoice", "collections": [
        {"key": "invoices", "kind": "records", "title": "Invoice", "sections": [
            {"title": "Invoice main details", "fields": [
                F("invoice_no", "GST invoice no"), F("invoice_date", "Date", "date"), F("payment_method", "Payment method", "select", ["NA", "AP - Advance payment", "LC - Letter of credit", "DP - Documents against payment", "OA - Open account"]),
                F("invoice_value", "Invoice value", "number"), F("currency", "Invoice currency"), F("exchange_rate", "Exchange rate", "number"), F("terms", "Terms", "select", TERMS_X), F("consignee_buyer_same", "Whether consignee and buyer same", "select", YN),
                F("period_of_payment", "Period of payment"), F("buyer_name", "Buyer name"), F("buyer_address1", "Buyer address 1"), F("buyer_address2", "Buyer address 2"), F("contract_no", "Contract no"), F("contract_date", "Contract date", "date")]},
            {"title": "Freight & insurance", "fields": _money("Commission", "commission") + _money("FOB discount", "fob_disc") + _money("Pack & misc", "pack_misc") + _money("Other deductions", "other_ded") + _money("Freight", "freight") + _money("Insurance", "insurance")
                + [F("unit_price_includes", "Whether the unit price includes", "select", ["N", "Y"])]},
            {"title": "Third party (SW)", "fields": [
                F("third_party_name", "Third party name"), F("third_party_address1", "Address 1"), F("third_party_address2", "Address 2"), F("third_party_city", "City"), F("country_sub_division", "Country sub division"), F("third_party_country", "Country code"),
                F("third_party_pin", "Pin"), F("terms_place", "Terms place"), F("aeo_code", "Authorized Economic Operator code"), F("aeo_country", "AEO country"), F("aeo_role", "AEO role"), F("notify_name", "Notify name"),
                F("notify_address1", "Notify address 1"), F("notify_address2", "Notify address 2"), F("notify_address3", "Notify address 3"), F("notify_country", "Notify country")]},
        ]},
        {"key": "ar4", "kind": "rows", "title": "AR4 details", "fields": [F("ar4_no", "AR4 no"), F("ar4_date", "AR4 date", "date"), F("commissionerate", "Commissionerate"), F("division", "Division"), F("range", "Range"), F("ar4_item_no", "Item no"), F("remarks", "Remarks", "area")]},
    ]},
    {"tab": "Item", "collections": [
        {"key": "items", "kind": "records", "title": "Item", "link": "invoices", "sections": [
            {"title": "Item main details", "fields": [
                F("invoice_sr", "Invoice sr no", "number"), F("description", "Description", "area"), F("ritc_no", "RITC no"), F("qty", "Item quantity", "number"), F("qty_unit", "Unit"), F("unit_price", "Item unit price", "number"), F("per", "Per", "number"),
                F("amount", "Item amount", "number"), F("scheme", "Scheme", "select", ["00-FREE SHIPPING BILL", "01-ADVANCE LICENCE", "02-EPCG", "03-DEEC", "04-DFIA", "05-EOU", "06-DRAWBACK"]), F("str", "STR Y/N", "select", ["No", "Yes"]),
                F("reward", "Reward Y/N", "select", ["Yes", "No"]), F("cess", "Cess", "select", ["No", "Yes"]), F("rodtep", "RODTEP (Y/N)", "select", ["Yes", "No"])]},
            {"title": "Other details", "fields": [
                F("accessory_desc", "Accessory description", "area"), F("end_use", "End use of item"), F("igst_payment_status", "IGST payment status", "select", ["NA", "P - Paid", "LUT - Without payment"]), F("taxable_value", "Taxable value", "number"),
                F("igst_rate", "IGST rate", "number"), F("igst_amount", "IGST amount", "number"), F("info_msr", "Info MSR", "number"), F("info_uqc", "Info UQC"), F("under_rta_fta", "Under RTA / FTA"), F("state_of_origin", "State of origin"),
                F("district", "District"), F("gst_cess_amt", "GST cess amount", "number"), F("tp_exporter_name", "Third party exporter name"), F("tp_iec", "Third party IEC code"), F("tp_branch_sl", "Third party branch sl no"),
                F("tp_address1", "Third party address 1"), F("tp_address2", "Third party address 2"), F("tp_address3", "Third party address 3"), F("tp_gstn_type", "GSTN type"), F("tp_gstn_id", "GSTN ID")]},
            {"title": "SW - manufacturer", "fields": [
                F("mfr_code_type", "Item manufacturer / producer code type"), F("mfr_grower_code", "Item manufacturer / producer / grower code"), F("mfr_address1", "Address 1"), F("mfr_address2", "Address 2"), F("mfr_city", "City"),
                F("mfr_country_subdivision", "Country subdivision"), F("mfr_pincode", "Pincode"), F("mfr_country", "Manufacturer country"), F("source_state", "Source state"), F("transit_country", "Transit country"),
                F("accessory_status", "Accessory status (0 / 1 / 2)", "select", ["0 - No accessories", "1 - Accessories along with this item", "2 - Accessories as separate item"]), F("hawb_no", "HAWB no"), F("part_no", "Part no"), F("total_package", "Total package", "number")]},
            {"title": "Single window", "fields": [
                F("sw_dept", "Available department"), F("sw_ritc", "RITC no"), F("sw_info_type", "Info type"), F("sw_info_qualifier", "Info qualifier"), F("sw_info_code", "Info code"), F("sw_info_text", "Info text"), F("sw_info_msr", "Info MSR", "number"),
                F("sw_info_uqc", "Info UQC"), F("sw_sr_no", "Sr no")]},
            {"title": "Supporting documents", "fields": [
                F("doc_level", "Level", "select", ["Job Level", "Invoice Level", "Item Level"]), F("irn_no", "IRN no"), F("doc_type_code", "Document type code"), F("iss_party_code", "Issuing party code"), F("iss_party_name", "Issuing party name"),
                F("iss_party_add", "Issuing party address"), F("iss_party_add2", "Address 2"), F("iss_party_city", "City"), F("iss_party_pin", "Pin"), F("doc_no", "Document no"), F("place_of_issue", "Place of issue"), F("issue_date", "Issue date", "date"),
                F("expiry_date", "Expiry date", "date"), F("ben_party_code", "Beneficiary party code"), F("ben_party_name", "Beneficiary party name"), F("ben_party_add", "Beneficiary address"), F("ben_party_add2", "Address 2"),
                F("ben_party_city", "City"), F("ben_party_pin", "Pin"), F("file_type", "File type"), F("icegate_id", "ICEGATE ID"), F("icegate_filename", "ICEGATE file name")]},
        ]},
    ]},
    {"tab": "Annexure C1", "collections": [
        {"key": "annexure_c1", "kind": "form", "title": "Annexure C1 details", "sections": [
            {"title": "Details", "fields": [
                F("exporter_name", "Exporter name"), F("address1", "Address"), F("address2", "Address 2"), F("address3", "Address 3"), F("iec_no", "IEC no"), F("branch_sl_no", "Branch sl no"), F("examination_date", "Examination date", "date"),
                F("examination_officer", "Examination officer"), F("examination_designation", "Designation"), F("supervising_officer", "Supervising officer"), F("supervising_designation", "Designation"), F("commissionerate", "Commissionerate"),
                F("division", "Division"), F("range", "Range"), F("verified_by_officer", "Verified by examination officer", "check"), F("sample_drawn", "Sample drawn", "check"), F("seal_no", "Seal no")]},
        ]},
    ]},
]


def spec_for(direction: str) -> list[dict]:
    """The tabs of a job beyond General: the Import (Bill of Entry) or Export (Shipping Bill) set, plus the parts both share."""
    return (EXPORT_SPEC if direction == "Export" else IMPORT_SPEC) + COMMON_SPEC


def collections(spec=None) -> dict[str, dict]:
    out = {}
    for tab in spec or (IMPORT_SPEC + EXPORT_SPEC + COMMON_SPEC):
        for c in tab["collections"]:
            flat = c["fields"] if c["kind"] == "rows" else [f for s in c["sections"] for f in s["fields"]]
            keys = [f["k"] for f in flat]
            out[c["key"]] = {"keys": set(keys), "kind": c["kind"], "title": c["title"], "labels": {f["k"]: f["l"] for f in flat}}
    return out


def clean_collection(name: str, rows, rules: dict) -> list[dict]:
    """Keep only known fields, trimmed; blank values are dropped; at most 200 records of 80 fields."""
    from fastapi import HTTPException

    if name not in rules:
        raise HTTPException(422, "Unknown section: " + name[:30])
    if not isinstance(rows, list) or len(rows) > 200:
        raise HTTPException(422, f"{rules[name]['title']}: too many records")
    out = []
    for r in rows:
        if not isinstance(r, dict):
            raise HTTPException(422, f"{rules[name]['title']}: invalid record")
        bad = [k for k in r if k not in rules[name]["keys"]]
        if bad:
            raise HTTPException(422, f"{rules[name]['title']}: unknown field {bad[0][:30]}")
        rec = {k: str(v).strip()[:2000] for k, v in r.items() if v is not None and str(v).strip() != ""}
        if rec:
            out.append(rec)
    return out
