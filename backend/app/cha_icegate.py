"""ICEGATE Shipping Bill message (JSON) built from a CHA Export job. The structure follows the sample SB files supplied by the user (headerField + master with exchangeModel, invoiceModel, itemModel, sbModel, infoType, statement).
The file is only produced here; signing it and filing it at ICEGATE is done outside the ERP."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

STATE_CODES = {"JAMMU & KASHMIR": "01", "HIMACHAL PRADESH": "02", "PUNJAB": "03", "CHANDIGARH": "04", "UTTARAKHAND": "05", "HARYANA": "06", "DELHI": "07", "RAJASTHAN": "08", "UTTAR PRADESH": "09", "BIHAR": "10", "SIKKIM": "11",
               "ARUNACHAL PRADESH": "12", "NAGALAND": "13", "MANIPUR": "14", "MIZORAM": "15", "TRIPURA": "16", "MEGHALAYA": "17", "ASSAM": "18", "WEST BENGAL": "19", "JHARKHAND": "20", "ODISHA": "21", "CHHATTISGARH": "22",
               "MADHYA PRADESH": "23", "GUJARAT": "24", "DADRA & NAGAR HAVELI AND DAMAN & DIU": "26", "MAHARASHTRA": "27", "KARNATAKA": "29", "GOA": "30", "LAKSHADWEEP": "31", "LAKSHADWEEP ": "31", "KERALA": "32", "TAMIL NADU": "33",
               "PUDUCHERRY": "34", "ANDAMAN & NICOBAR ISLANDS": "35", "TELANGANA": "36", "ANDHRA PRADESH": "37", "LADAKH": "38"}


def ymd(v: Any) -> str:
    """2026-10-19 (or a date) -> 20261019."""
    if isinstance(v, (date, datetime)):
        return v.strftime("%Y%m%d")
    s = str(v or "").strip()
    return re.sub(r"\D", "", s)[:8] if re.match(r"^\d{4}-\d{2}-\d{2}", s) else ""


def code(v: Any) -> str:
    """The code part of 'GSN - GSTIN of Normal Persons' / '33 - TAMIL NADU' / 'R - Merchant Exporter'."""
    s = str(v or "").strip()
    return re.split(r"\s+-+\s*|--", s, maxsplit=1)[0].strip() if s else ""


def country2(v: Any) -> str:
    s = code(v).upper()
    return s if len(s) == 2 else s


def state_code(v: Any) -> str:
    s = str(v or "").strip()
    m = re.match(r"^(\d{2})\b", s)
    if m:
        return m.group(1)
    return STATE_CODES.get(re.sub(r"^\d+\s*-+\s*", "", s).upper(), "")


def num(v: Any, places: int) -> str:
    try:
        return f"{float(str(v).replace(',', '')):.{places}f}"
    except (TypeError, ValueError):
        return ""


def put(d: dict, key: str, value: Any) -> None:
    if value not in (None, ""):
        d[key] = value if isinstance(value, str) else str(value)


def build_sb(job, info: dict, filing: dict, data: dict, defaults: dict, party: dict, cha_licence: str, now: date) -> tuple[dict, list[str]]:
    """Returns (message, warnings). `filing` is what was typed on the General tab, `defaults` what the job and the Organization master supply, `party` the exporter's master record."""
    warnings: list[str] = []
    f = lambda k: str(filing.get(k) if filing.get(k) not in (None, "") else defaults.get(k, "") or "").strip()
    invoices, items = list(data.get("invoices") or []), list(data.get("items") or [])
    if not invoices:
        warnings.append("No invoice has been entered")
    if not items:
        warnings.append("No item has been entered")
    seq = int(re.search(r"(\d+)\D*$", job.job_number or "0").group(1)) if re.search(r"(\d+)\D*$", job.job_number or "") else 0
    created = job.created_at.date() if getattr(job, "created_at", None) else now
    header = {"senderID": str((info.get("icegate_id") or info.get("cha_license") or "")).strip(), "receiverID": str(info.get("customs_location") or "").strip(), "indicator": "P", "messageID": "CACHE01",
              "sequenceOrControlNumber": f"{seq:05d}{created.year % 100:02d}", "jobNumber": str(seq), "jobDate": ymd(created), "messageType": "F"}
    if not header["senderID"]:
        warnings.append("ICEGATE ID is not set (header senderID)")
    if not header["receiverID"]:
        warnings.append("Customs house is not set (header receiverID)")
    # exchange rates: one row per currency used on the invoices
    exch, seen = [], set()
    for inv in invoices:
        ccy = (inv.get("currency") or "").strip().upper()
        if ccy and ccy not in seen:
            seen.add(ccy)
            exch.append({"invoiceCurrencyCode": ccy, "unitInRs": "1", "rate": str(inv.get("exchange_rate") or ("1" if ccy == "INR" else "")), "whetherStandardCurrency": "Y"})
            if ccy != "INR" and not inv.get("exchange_rate"):
                warnings.append(f"Exchange rate missing for {ccy}")
    inv_model = []
    for n, inv in enumerate(invoices, start=1):
        ccy = (inv.get("currency") or "").strip().upper()
        m: dict[str, str] = {"invoiceSerialNumber": str(n)}
        put(m, "actualInvoiceNo", inv.get("invoice_no")); put(m, "invoiceDate", ymd(inv.get("invoice_date"))); put(m, "invoiceCurrency", ccy); put(m, "natureOfContract", inv.get("terms"))
        put(m, "buyerName", inv.get("buyer_name") or ("SAME AS CONSIGNEE" if (inv.get("consignee_buyer_same") or "Yes") == "Yes" else "")); put(m, "buyerAddress1", inv.get("buyer_address1")); put(m, "buyerAddress2", inv.get("buyer_address2"))
        for key, pre in (("freight", "freight"), ("insurance", "insurance"), ("commission", "commission"), ("fob_disc", "discount"), ("other_ded", "otherDeductions")):
            put(m, pre + "Currency", (inv.get(key + "_ccy") or ccy));
            if inv.get(key + "_amt"):
                put(m, pre + "Amount", inv.get(key + "_amt"))
            if inv.get(key + "_pct") and float(inv.get(key + "_pct") or 0):
                put(m, pre + "Rate", inv.get(key + "_pct"))
        m["addFreight"] = "Y" if inv.get("unit_price_includes") == "Y" else "N"
        put(m, "exporterContractNumber", inv.get("contract_no")); put(m, "natureOfPayment", code(inv.get("payment_method")) or "NA")
        put(m, "thirdPartyName", inv.get("third_party_name")); put(m, "thirdPartyAddress1", inv.get("third_party_address1")); put(m, "thirdPartyAddress2", inv.get("third_party_address2")); put(m, "thirdPartyCity", inv.get("third_party_city"))
        put(m, "thirdPartyPin", inv.get("third_party_pin"))
        inv_model.append(m)
    sb: dict[str, str] = {}
    put(sb, "chaLicenseNumber", cha_licence); put(sb, "importerExporterCode", f("iec")); put(sb, "branchSrNoOfExporter", code(f("branch_sr_no")) or "0"); put(sb, "impExpName", f("exporter_name"))
    put(sb, "impExpAddress1", f("addr1")); put(sb, "impExpAddress2", f("addr2")); put(sb, "impExpCity", f("addr3")); put(sb, "impExpState", f("state")); put(sb, "impExpPin", f("pin"))
    put(sb, "typeOfExporter", code(f("exporter_type"))); put(sb, "exporterClass", code(f("exporter_class"))); put(sb, "stateOfOriginExporter", state_code(f("state_of_origin")))
    put(sb, "authorizedDealerCode", f("ad_code")); put(sb, "consigneeName", f("consignee")); put(sb, "consigneeAddress1", " ".join(x for x in (f("cons_addr1"), f("cons_addr2")) if x)); put(sb, "consigneeCountry", country2(f("cons_country")))
    put(sb, "portOfLoading", f("port_loading")); put(sb, "portOfFinalDestination", f("port_final_destination") or f("port_discharge")); put(sb, "countryOfFinalDestination", country2(f("country_final_destination") or f("country_destination")))
    put(sb, "countryOfDischarge", country2(f("country_discharge") or f("country_destination"))); put(sb, "portOfDischarge", f("port_discharge")); put(sb, "marksNumbers", f("marks"))
    put(sb, "gstnType", code(f("gstn_type"))); put(sb, "gstnId", f("gstn_id"))
    for key, label in (("importerExporterCode", "IEC"), ("impExpName", "Exporter name"), ("authorizedDealerCode", "AD code"), ("portOfLoading", "Port of loading"), ("portOfDischarge", "Port of discharge"), ("gstnType", "GSTN type"), ("gstnId", "GSTN ID")):
        if key not in sb:
            warnings.append(f"{label} is missing (General tab)")
    item_model, info_rows, per_invoice = [], [], {}
    for it in items:
        sr = str(int(float(it.get("invoice_sr") or 1))) if str(it.get("invoice_sr") or "").strip() else "1"
        per_invoice[sr] = per_invoice.get(sr, 0) + 1
        isn = per_invoice[sr]
        m = {"invoiceSerialNumber": sr, "itemSerialNumberInvoice": str(isn)}
        put(m, "schemeCode", (code(it.get("scheme")) or "00")[:2]); put(m, "ritcCode", it.get("ritc_no")); put(m, "descOfGoods1", it.get("description")); put(m, "unitOfMeasurement", it.get("qty_unit"))
        put(m, "quantity", num(it.get("qty"), 3)); put(m, "unitPrice", num(it.get("unit_price"), 5)); put(m, "unitOfRate", it.get("qty_unit")); put(m, "noOfUnit", str(int(float(it.get("per") or 1))))
        put(m, "presentMarketValue", num(it.get("amount"), 2)); m["thirdParty"] = "Y" if it.get("tp_exporter_name") else "N"; m["rewardItem"] = "Y" if (it.get("reward") or "No") == "Yes" else "N"
        put(m, "endUseOfItem", it.get("end_use")); put(m, "igstPaymentStatus", code(it.get("igst_payment_status")) if code(it.get("igst_payment_status")) not in ("", "NA") else "")
        item_model.append(m)
        if it.get("sw_info_type"):
            r = {"invoiceSerialNumber": sr, "itemSerialNumber": str(isn), "infoSrNo": str(len([x for x in info_rows if x["invoiceSerialNumber"] == sr and x["itemSerialNumber"] == str(isn)]) + 1)}
            put(r, "infoType", it.get("sw_info_type")); put(r, "infoQualifier", it.get("sw_info_qualifier")); put(r, "infoCode", it.get("sw_info_code")); put(r, "infoText", it.get("sw_info_text")); put(r, "infoMeasure", num(it.get("sw_info_msr"), 1) if it.get("sw_info_msr") else ""); put(r, "infouqc", it.get("sw_info_uqc"))
            info_rows.append(r)
        if not it.get("ritc_no"):
            warnings.append(f"Item {isn} of invoice {sr}: RITC no is missing")
    master: dict[str, Any] = {"exchangeModel": exch, "invoiceModel": inv_model}
    if info_rows:
        master["infoType"] = info_rows
    if item_model:
        master["itemModel"] = item_model
    master["sbModel"] = [sb]
    return {"headerField": header, "master": master}, warnings


def _split_desc(text: str, width: int = 60) -> tuple[str, str]:
    t = " ".join(str(text or "").split())
    if len(t) <= width:
        return t, ""
    cut = t.rfind(" ", 0, width)
    cut = cut if cut > 20 else width
    return t[:cut].strip(), t[cut:].strip()[:width]


def build_be(job, info: dict, filing: dict, data: dict, defaults: dict, party: dict, cha_licence: str, now: date) -> tuple[dict, list[str]]:
    """The ICEGATE Bill of Entry message (JSON) of an Import job: beModel, exchangeModel, invoiceModel, itemsModel, depbModel, sbedutyModel, ctxModel, infoTypeModel, statementModel (layout of the supplied BE sample files)."""
    warnings: list[str] = []
    f = lambda k: str(filing.get(k) if filing.get(k) not in (None, "") else defaults.get(k, "") or "").strip()
    invoices, items = list(data.get("invoices") or []), list(data.get("items") or [])
    if not invoices:
        warnings.append("No invoice has been entered")
    if not items:
        warnings.append("No item has been entered")
    m_ = re.search(r"(\d+)\D*$", job.job_number or "")
    seq = int(m_.group(1)) if m_ else 0
    created = job.created_at.date() if getattr(job, "created_at", None) else now
    header = {"senderID": str(info.get("icegate_id") or "").strip(), "receiverID": str(info.get("customs_location") or "").strip(), "indicator": "P", "messageID": "CACHI01",
              "sequenceOrControlNumber": f"{seq:05d}{created.year % 100:02d}", "jobNumber": str(seq), "jobDate": ymd(created), "messageType": "F"}
    if not header["senderID"]:
        warnings.append("ICEGATE ID is not set (header senderID)")
    if not header["receiverID"]:
        warnings.append("Customs house is not set (header receiverID)")
    be: dict[str, str] = {}
    put(be, "beType", code(f("be_type")) or "H"); put(be, "iecCode", f("iec")); put(be, "branchSrNo", code(f("branch_sr_no")) or "0"); put(be, "nameOfImporter", f("importer_name"))
    put(be, "address1", f("addr1")); put(be, "address2", f("addr2")); put(be, "city", f("addr3")); put(be, "state", f("state")); put(be, "pin", f("pin"))
    be["beClass"] = "N"; put(be, "modeOfTransport", code(f("mode_of_transport"))); put(be, "importerType", code(f("importer_type"))); be["kachchaBE"] = "N"
    be["highSeaSaleFlag"] = "Y" if code(f("high_sea_sale")).lower() in ("yes", "y") else "N"
    put(be, "portOfOrigin", f("port_origin")); put(be, "chaCode", cha_licence); put(be, "countryOfOrigin", country2(f("country_origin"))); put(be, "countryOfConsignment", country2(f("country_consignment")))
    put(be, "portOfShipment", f("port_shipment")); be["greenChannelRequested"] = "N"; be["section48Requested"] = "N"; be["whetherPriorBE"] = "Y" if code(f("filing_status")) == "Y" else "N"
    put(be, "authorizedEconomicDealerCode", f("ad_code")); be["firstCheckRequested"] = "N"; put(be, "paymentMethodCode", code(f("payment_method")) or "T")
    for key, label in (("iecCode", "IEC"), ("nameOfImporter", "Importer name"), ("modeOfTransport", "Mode of transport"), ("portOfOrigin", "Port of origin"), ("portOfShipment", "Port of shipment"), ("countryOfOrigin", "Country of origin"),
                       ("countryOfConsignment", "Country of consignment"), ("authorizedEconomicDealerCode", "AD code")):
        if key not in be:
            warnings.append(f"{label} is missing (General tab)")
    exch, seen = [], set()
    for inv in invoices:
        ccy = (inv.get("currency") or "").strip().upper()
        if ccy and ccy not in seen:
            seen.add(ccy)
            exch.append({"currencyCode": ccy, "standardCurrency": "Y", "unitInRs": "1", "rate": str(inv.get("exchange_rate") or ("1" if ccy == "INR" else ""))})
            if ccy != "INR" and not inv.get("exchange_rate"):
                warnings.append(f"Exchange rate missing for {ccy}")
    if "INR" not in seen and exch:
        exch.append({"currencyCode": "INR", "standardCurrency": "Y", "unitInRs": "1", "rate": "1"})
    inv_model = []
    for n, inv in enumerate(invoices, start=1):
        ccy = (inv.get("currency") or "").strip().upper()
        m: dict[str, str] = {"invoiceSerialNumber": str(n)}
        put(m, "invoiceDate", ymd(inv.get("invoice_date"))); put(m, "supplierName", inv.get("supplier_name")); put(m, "supplierAddress1", inv.get("supp_address1")); put(m, "supplierAddress2", inv.get("supp_address2"))
        put(m, "supplierCountryName", inv.get("supp_country")); put(m, "supplierPin", inv.get("supp_pin")); put(m, "invoiceValue", num(inv.get("invoice_value"), 2)); put(m, "termsOfInvoice", inv.get("terms")); put(m, "invoiceCurrency", ccy)
        for src, dst in (("freight", "freight"), ("insurance", "insurance"), ("misc", "misc")):
            if inv.get(src + "_charge"):
                put(m, dst + ("Value" if dst != "misc" else "Charges"), inv.get(src + "_charge"))
                put(m, dst + ("Currency" if dst != "misc" else "Currency"), inv.get(src + "_ccy") or ccy)
        put(m, "natureOfTransaction", code(inv.get("nature_of_trans")) or "S"); put(m, "paymentTerms", code(inv.get("nature_of_payment")) or "OTH"); put(m, "valuationMethodApplicable", inv.get("valuation_method") or "Rule 4 - Transaction Value")
        put(m, "actualInvoiceNumber", inv.get("invoice_no"))
        inv_model.append(m)
    item_model, depb, duty, info_rows, statements, per_invoice = [], [], [], [], [], {}
    for it in items:
        sr = str(int(float(it.get("invoice_sr") or 1))) if str(it.get("invoice_sr") or "").strip() else "1"
        per_invoice[sr] = per_invoice.get(sr, 0) + 1
        isn = str(per_invoice[sr])
        d1, d2 = _split_desc(it.get("description"))
        m = {"invoiceSerialNumber": sr, "itemSerialNumber": isn}
        put(m, "quantity", num(it.get("qty"), 6)); put(m, "unitQuantityCode", it.get("qty_unit")); put(m, "ritcCode", it.get("ritc_no") or it.get("cth_no")); put(m, "itemDescription1", d1); put(m, "itemDescription2", d2)
        put(m, "itemCategory", code(it.get("scheme_code"))); put(m, "genericDescriptionOfTheItem", it.get("generic_description") or it.get("description")); put(m, "manufacturerName", it.get("manufacturer")); put(m, "brandName", it.get("brand")); put(m, "model", it.get("model"))
        put(m, "endUseOfItem", it.get("end_use")); put(m, "countryOfOrigin", country2(it.get("country_origin") or f("country_origin"))); put(m, "cth", it.get("cth_no")); m["preferentialStandard"] = "S"; put(m, "ceth", it.get("ceth_no") or "NOEXCISE")
        put(m, "bcdNotification", it.get("basic_duty_notn")); put(m, "antiDumpingDutyNotification", it.get("add_notn_no")); put(m, "antiDumpingDutySerialNumber", it.get("add_notn_sl")); put(m, "unitPriceInvoiced", num(it.get("unit_price"), 6))
        m["rspApplicability"] = code(it.get("rsp_applicability")) or "N"; m["whetherReimport"] = "Y" if it.get("sb_no") else "N"; put(m, "transitCountry", it.get("transit_country")); m["accessoryStatus"] = "0"
        item_model.append(m)
        if not (it.get("ritc_no") or it.get("cth_no")):
            warnings.append(f"Item {isn} of invoice {sr}: RITC / CTH no is missing")
        if it.get("scheme_notn"):
            depb.append({"invoiceSerialNumber": sr, "itemSerialNumberInvoice": isn, "whetherExemption": "Y", "bcdNotification": it.get("scheme_notn"), "bcdNotificationSrNo": str(it.get("scheme_notn_sl") or "1")})
        for notn, sl, dtype, flag in ((it.get("levy_notn_no"), it.get("levy_notn_sl"), "G", "I"), (it.get("levy_cp_cess_notn_no"), "", "G", "P"), (it.get("add_notn_no"), it.get("add_notn_sl"), "C", "C"), (it.get("aid_cess_notn"), "", "C", "A")):
            if notn:
                r = {"invoiceSerialNumber": sr, "itemSerialNumber": isn, "notificationNo": notn, "dutyType": dtype, "addlDutyFlag": flag}
                put(r, "notificationSrNo", sl)
                duty.append(r)
        if it.get("sw_info_type") or it.get("info_msr"):
            r = {"invoiceSerialNumber": sr, "itemSerialNumber": isn, "infoType": it.get("sw_info_type") or "CHR", "infoQualifier": it.get("sw_info_qualifier") or "SQC"}
            put(r, "infoCode", it.get("sw_info_code")); put(r, "infoText", it.get("sw_info_text")); put(r, "infoMsr", num(it.get("sw_info_msr") or it.get("info_msr"), 6)); put(r, "infoUqc", it.get("sw_info_uqc") or it.get("info_uqc"))
            info_rows.append(r)
        if it.get("stmt_code"):
            r = {"declarationtype": "B", "invoiceSerialNumber": sr, "itemSerialNumber": isn, "statementType": it.get("stmt_type") or "DEC", "statementCode": it.get("stmt_code")}
            put(r, "statementText", it.get("stmt_text"))
            statements.append(r)
    if not statements and invoices:
        # the standard declarations of a Bill of Entry (job level CUG00 / CUG01, invoice level CUV01 - CUV03), as in the sample files
        for sr_, code_ in (("0", "CUG00"), ("0", "CUG01")) + tuple(("1", c) for c in ("CUV01", "CUV02", "CUV03")):
            statements.append({"declarationtype": "B", "invoiceSerialNumber": sr_, "itemSerialNumber": "0", "statementType": "DEC", "statementCode": code_})
        warnings.append("No declaration was entered: the standard declarations CUG00, CUG01 and CUV01-CUV03 were added")
    master: dict[str, Any] = {"beModel": [be], "exchangeModel": exch, "invoiceModel": inv_model}
    if item_model:
        master["itemsModel"] = item_model
    if depb:
        master["depbModel"] = depb
    if duty:
        master["sbedutyModel"] = duty
    ctx = {"stateCode": state_code(f("state")) or state_code(f("state_of_origin")), "commercialTaxType": code(f("commercial_tax_type")), "commercialTaxRegistrationNo": f("commercial_tax_reg_no")}
    master["ctxModel"] = [{k: v for k, v in ctx.items() if v}]
    if info_rows:
        master["infoTypeModel"] = info_rows
    if statements:
        master["statementModel"] = statements
    return {"headerField": header, "master": master}, warnings
