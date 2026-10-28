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
            {"title": "Declaration", "fields": [F("stmt_type", "Statement type"), F("stmt_code", "Statement code"), F("stmt_text", "Statement text", "area")]},
            {"title": "Supporting document", "fields": [F("doc_level", "Level", "select", ["Job Level", "Invoice Level", "Item Level"]), F("image_ref_no", "Image ref no"), F("doc_type_code", "Document type code"), F("iss_party_name", "Issuing party name"),
                                                      F("place_of_issue", "Place of issue"), F("issue_date", "Issue date", "date"), F("expiry_date", "Expiry date", "date"), F("doc_no", "Document no"), F("file_type", "File type"), F("icegate_id", "ICEGATE ID")]},
        ]},
    ]},
]


def collections(spec=None) -> dict[str, dict]:
    out = {}
    for tab in spec or IMPORT_SPEC:
        for c in tab["collections"]:
            keys = [f["k"] for f in c["fields"]] if c["kind"] == "rows" else [f["k"] for s in c["sections"] for f in s["fields"]]
            out[c["key"]] = {"keys": set(keys), "kind": c["kind"], "title": c["title"], "labels": {f["k"]: f["l"] for f in (c["fields"] if c["kind"] == "rows" else [f for s in c["sections"] for f in s["fields"]])}}
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
