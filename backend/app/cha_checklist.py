"""The CHECK LIST of a CHA job before it is filed: Bill of Entry (Import) or Shipping Bill (Export). It is calculated from what is entered on the job (valuation, duties, FOB, IGST) and laid out like the checklists the CHA team already uses.
Assessable value (Import): FOB of the item in INR plus its share (by value) of freight, insurance (1.125% of the invoice value when none is entered) and miscellaneous charges.
Duties: BCD = AV x BCD %; Social Welfare Surcharge = BCD x SWS %; AIDC = AV x %; ADD = AV x %; IGST and compensation cess on (AV + BCD + SWS + AIDC + ADD)."""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from .cha_icegate import code, num

BE_TYPES = {"H": ("HOME", "Home Consumption"), "W": ("WAREHOUSE", "Warehouse"), "X": ("EX-BOND", "Ex-bond"), "Z": ("SEZ", "SEZ (FTA->SEZ)"), "T": ("SEZ", "SEZ (DTA Sales - Trading)"), "M": ("SEZ", "SEZ (DTA Sales - Manufacturing)"),
            "V": ("SEZ", "SEZ Goods Movement"), "S": ("SEZ", "SEZ (DTA Sales - Stock Clearance)")}
STANDARD_STATEMENTS = {
    "CUG00": "I/We declare that the contents of this Bill of Entry for goods imported against above mentioned Bill of Lading/ Airway Bill /Lorry Receipt/Railway Receipt numbers are in accordance with the above mentioned invoice(s) No(s)and other documents presented herewith.",
    "CUG01": "I/We declare that the contents of the above mentioned invoice(s) and documents are true and correct in every respect.I/We have not received and do not know of any other documents or information showing a different description, quantity, price, value, of the said goods and that if at any time hereafter I/We discover any document / information showing different facts, I/We will immediately make the same known to the Commissioner of Customs.",
    "CUV01": "I/We declare that all conditions or restrictions, if any, imposed by the seller of any third party on the disposition or use of the imported goods [as per proviso to Rule 3(2)) of the Customs Valuation Rules, 2007] are specified above.",
    "CUV02": "I/We declare that the price paid or payable by the importer is as per the details provided above, and any price paid or payable in addition to the above will be settled with the seller at the end of a defined period by means of debit note / credit note (post - import price adjustment), which are as per the contract attached as a supporting document.",
    "CUV03": "I/We declare that there are no payments actually paid or payable for the imported goods by way of cost and services [in terms of Rules 10(1)(a)(i), Rule 10(1)(a)(ii), Rule 10 (1) (a) (iii) and Rule 10 (1) (b) of Customs Valuation Rules, 2007] Royalty / Licence Fee / subsequent resale or use of goods /other payment as a condition of sale [(Please see Rule 10 (1) (c), (d) & (e) of Customs Valuation Rules, 2007] other than those declared in the invoice which are mentioned as miscellaneous charges in this Bill of Entry.",
}
EXPORT_DECLARATION = ("I/We declare that the particulars given herein are true and are correct. I/We undertake to abide by provisions of Foreign Exchange Management Act,1999, as amended from time to time including realisation / repatriation of foreign exchange to / from India.")
CIF_LIKE = ("CIF", "CFR", "CNF", "C&I", "CIP", "CPT", "DAP", "DDP")


def f(v: Any, default: float = 0.0) -> float:
    try:
        return float(str(v).replace(",", "")) if str(v).strip() != "" else default
    except (TypeError, ValueError):
        return default


def r2(v: float) -> float:
    return round(v + 1e-9, 2)


def dmy(v: Any) -> str:
    s = str(v or "")
    return f"{s[8:10]}/{s[5:7]}/{s[:4]}" if re.match(r"^\d{4}-\d{2}-\d{2}", s) else s


def words(n: float) -> str:
    """Indian rupees in words: 1034842 -> Ten Lakh Thirty Four Thousand Eight Hundred Forty Two."""
    ones = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    def two(x: int) -> str:
        return ones[x] if x < 20 else (tens[x // 10] + (" " + ones[x % 10] if x % 10 else ""))

    def three(x: int) -> str:
        return (ones[x // 100] + " Hundred" + (" " if x % 100 else "") if x >= 100 else "") + (two(x % 100) if x % 100 else "")

    n = int(round(n))
    if n == 0:
        return "Zero"
    parts = []
    for div, name in ((10000000, "Crore"), (100000, "Lakh"), (1000, "Thousand")):
        if n >= div:
            parts.append(two(n // div) + " " + name)
            n %= div
    if n:
        parts.append(three(n))
    return " ".join(parts)


def _common(job, row, info, filing, defaults, cha_name: str, cha_licence: str, created_by: str, customs_desc: str) -> dict:
    g = lambda k: str(filing.get(k) if filing.get(k) not in (None, "") else defaults.get(k, "") or "").strip()
    return {"job_no": job.job_number, "job_date": dmy(str(job.created_at.date()) if getattr(job, "created_at", None) else date.today().isoformat()), "printed_on": date.today().strftime("%d/%m/%Y"), "created_by": created_by,
            "file_ref": info.get("file_ref") or "", "cha_licence": cha_licence, "cha_name": cha_name, "customs_house": (info.get("customs_location") or "") + (" - " + customs_desc if customs_desc else ""), "get": g}


def build_import(job, row, info, filing, defaults, data, cha_name, cha_licence, created_by, customs_desc, pd) -> dict:
    c = _common(job, row, info, filing, defaults, cha_name, cha_licence, created_by, customs_desc)
    g = c.pop("get")
    be_code = code(g("be_type")) or "H"
    title_kind, be_label = BE_TYPES.get(be_code, ("HOME", "Home Consumption"))
    invoices, items = list(data.get("invoices") or []), list(data.get("items") or [])
    inv_out, item_out, per_invoice = [], [], {}
    totals = {"av": 0.0, "bcd": 0.0, "sws": 0.0, "aidc": 0.0, "add": 0.0, "igst": 0.0, "cess": 0.0}
    for n, inv in enumerate(invoices, start=1):
        ccy = (inv.get("currency") or "").upper()
        rate = f(inv.get("exchange_rate"), 1.0) if ccy != "INR" else 1.0
        rate_of = lambda c2, rate=rate, ccy=ccy: 1.0 if (c2 or ccy).upper() == "INR" else rate
        mine = [it for it in items if (str(int(f(it.get("invoice_sr"), 1)))) == str(n)]
        amts = [f(it.get("amount")) or f(it.get("qty")) * f(it.get("unit_price")) for it in mine]
        fob_inr = f(inv.get("invoice_value")) * rate
        freight = f(inv.get("freight_charge")) * rate_of(inv.get("freight_ccy"))
        misc = f(inv.get("misc_charge")) * rate_of(inv.get("misc_ccy"))
        ins_given = f(inv.get("insurance_charge")) * rate_of(inv.get("insurance_ccy"))
        ins = ins_given if ins_given else f(inv.get("invoice_value")) * 0.01125 * rate
        load = freight + ins + misc
        total_amt = sum(amts) or 1.0
        inv_out.append({"sl": n, "no": inv.get("invoice_no"), "date": dmy(inv.get("invoice_date")), "value": r2(f(inv.get("invoice_value"))), "currency": ccy, "terms": inv.get("terms"), "freight": inv.get("freight_charge"), "freight_ccy": inv.get("freight_ccy") or ccy,
                        "freight_rate": rate_of(inv.get("freight_ccy")), "insurance": (inv.get("insurance_charge") or (f"1.125% ({r2(f(inv.get('invoice_value')) * 0.01125)} {ccy})")), "insurance_ccy": inv.get("insurance_ccy") or "", "misc": inv.get("misc_charge"),
                        "misc_ccy": inv.get("misc_ccy") or "", "svb": "Y" if inv.get("svb_relation") == "1" else "N", "nature": f"{code(inv.get('nature_of_trans')) or 'S'} & {code(inv.get('nature_of_payment')) or 'OTH'}", "exchange_rate": rate,
                        "supplier": [inv.get("supplier_name"), inv.get("supp_address1"), inv.get("supp_address2"), " ".join(x for x in (inv.get("supp_country"), inv.get("supp_pin")) if x)], "misc_inr": r2(misc), "fob_inr": r2(fob_inr)})
        for it, amt in zip(mine, amts):
            per_invoice[n] = per_invoice.get(n, 0) + 1
            share = amt / total_amt
            av = r2(amt * rate + share * load)
            bcd = r2(av * f(it.get("basic_duty_pct")) / 100)
            sws = r2(bcd * f(it.get("social_welfare_pct"), 10.0) / 100)
            aidc = r2(av * f(it.get("aid_cess_pct")) / 100)
            add = r2(av * f(it.get("add_pct")) / 100)
            base = av + bcd + sws + aidc + add
            igst = r2(base * f(it.get("levy_pct")) / 100)
            cess = r2(base * f(it.get("levy_cp_cess_pct")) / 100)
            for k, v in (("av", av), ("bcd", bcd), ("sws", sws), ("aidc", aidc), ("add", add), ("igst", igst), ("cess", cess)):
                totals[k] += v
            item_out.append({"inv": n, "sl": per_invoice[n], "ritc": it.get("ritc_no") or it.get("cth_no"), "description": it.get("description"), "scheme": code(it.get("scheme_code")), "qty": num(it.get("qty"), 6), "unit": it.get("qty_unit"), "unit_price": num(it.get("unit_price"), 6),
                             "coo": it.get("country_origin") or g("country_origin"), "cth": it.get("cth_no"), "ceth": it.get("ceth_no") or "NOEXCISE", "bcd_notn": it.get("basic_duty_notn"), "bcd_pct": f(it.get("basic_duty_pct")), "bcd": bcd, "av": av,
                             "end_use": it.get("end_use"), "sws_pct": f(it.get("social_welfare_pct"), 10.0), "sws": sws, "aidc_notn": it.get("aid_cess_notn"), "aidc_pct": f(it.get("aid_cess_pct")), "aidc": aidc, "add_notn": it.get("add_notn_no"), "add_pct": f(it.get("add_pct")), "add": add,
                             "igst_notn": " ".join(x for x in (it.get("levy_notn_no"), it.get("levy_notn_sl")) if x), "igst_pct": f(it.get("levy_pct")), "igst": igst, "cess_notn": it.get("levy_cp_cess_notn_no"), "cess_pct": f(it.get("levy_cp_cess_pct")), "cess": cess,
                             "scheme_notn": ((it.get("scheme_notn") or "") + (" " + it.get("scheme_notn_sl") if it.get("scheme_notn_sl") else "")).strip(), "sw": [{"type": it.get("sw_info_type") or "CHR", "qualifier": it.get("sw_info_qualifier") or "SQC", "code": it.get("sw_info_code"), "text": it.get("sw_info_text"),
                                                                                                                                                       "msr": it.get("sw_info_msr") or it.get("info_msr"), "uqc": it.get("sw_info_uqc") or it.get("info_uqc")}] if (it.get("sw_info_type") or it.get("info_msr")) else [],
                             "statements": [{"type": it.get("stmt_type") or "DEC", "code": it.get("stmt_code"), "text": it.get("stmt_text") or STANDARD_STATEMENTS.get(it.get("stmt_code"), "")}] if it.get("stmt_code") else []})
    customs_duty = r2(totals["bcd"] + totals["sws"] + totals["aidc"] + totals["add"])
    duty_total = r2(customs_duty + totals["igst"] + totals["cess"])
    statements = [s for it in item_out for s in [dict(x, inv=it["inv"], item=it["sl"]) for x in it["statements"]]]
    if not statements and invoices:
        statements = [{"inv": 1, "item": 1, "type": "DEC", "code": k, "text": STANDARD_STATEMENTS[k]} for k in ("CUG00", "CUG01", "CUV01", "CUV02", "CUV03")]
    igm = [{"igm_no": x.get("igm_no"), "igm_date": dmy(x.get("igm_date")), "inward": dmy(x.get("flight_date")), "mbl": x.get("mawb_no"), "mbl_date": dmy(x.get("mawb_date")), "hbl": x.get("hawb_no"), "hbl_date": dmy(x.get("hawb_date")), "packages": x.get("packages"),
            "package_unit": x.get("package_unit"), "weight": x.get("gross_weight"), "weight_unit": x.get("weight_unit"), "marks": x.get("marks")} for x in (data.get("igm") or [])]
    return {**c, "direction": "Import", "title": f"CHECK LIST - BILL OF ENTRY FOR {title_kind}", "be_type": be_label, "transport_mode": code(g("mode_of_transport")), "section46": "N", "green_channel": "N", "prior_be": "Y" if code(g("filing_status")) == "Y" else "N", "first_check": "N",
            "govt_private": code(g("importer_type")) or "P", "importer": {"name": g("importer_name"), "iec": g("iec"), "branch": code(g("branch_sr_no")) or "0", "address": [g("addr1"), g("addr2"), g("addr3"), (g("state") + (" - " + g("pin") if g("pin") else ""))], "ad_code": g("ad_code"),
                                                                                                                      "gstin": g("commercial_tax_reg_no"), "pan": (pd.get("pan") or "")}, "payment_method": "Transaction" if (code(g("payment_method")) or "T") == "T" else "Deferred", "port_origin": g("port_origin"), "port_shipment": g("port_shipment"),
            "country_origin": g("country_origin"), "country_consignment": g("country_consignment"), "igm": igm, "invoices": inv_out, "items": item_out, "totals": {k: r2(v) for k, v in totals.items()} | {"customs_duty": customs_duty, "duty_total": duty_total,
                                                                                                                                                                                                  "net_amount": r2(totals["av"]), "duty_payable": round(duty_total)},
            "duty_words": "Rupees " + words(duty_total) + " Only", "bonds": [{"no": b.get("bond_no"), "type": b.get("bond_type"), "location": b.get("bond_location")} for b in (data.get("bonds") or [])],
            "certificates": [{"no": x.get("cert_no"), "date": dmy(x.get("cert_date")), "type": x.get("cert_type")} for x in (data.get("certs") or [])], "containers": info.get("containers") or "",
            "gstin_rows": [{"state": g("state"), "type": code(g("commercial_tax_type")), "reg": g("commercial_tax_reg_no"), "igst_ass": r2(totals["av"]), "igst_amt": r2(totals["igst"]), "cess": r2(totals["cess"])}] if g("commercial_tax_reg_no") else [],
            "sw_info": [dict(s, inv=it["inv"], item=it["sl"]) for it in item_out for s in it["sw"]], "statements": statements, "signatory": g("importer_name")}


def build_export(job, row, info, filing, defaults, data, cha_name, cha_licence, created_by, customs_desc, pd) -> dict:
    c = _common(job, row, info, filing, defaults, cha_name, cha_licence, created_by, customs_desc)
    g = c.pop("get")
    invoices, items = list(data.get("invoices") or []), list(data.get("items") or [])
    inv_out, per_invoice, tot = [], {}, {"qty": 0.0, "fob": 0.0, "pmv": 0.0, "igst_val": 0.0, "igst_amt": 0.0}
    for n, inv in enumerate(invoices, start=1):
        ccy = (inv.get("currency") or "").upper()
        rate = f(inv.get("exchange_rate"), 1.0) if ccy != "INR" else 1.0
        mine = [it for it in items if (str(int(f(it.get("invoice_sr"), 1)))) == str(n)]
        amts = [f(it.get("amount")) or f(it.get("qty")) * f(it.get("unit_price")) for it in mine]
        gross = sum(amts)
        less = 0.0
        if (inv.get("terms") or "").upper() in CIF_LIKE:
            less = f(inv.get("freight_amt")) + f(inv.get("insurance_amt")) + f(inv.get("commission_amt")) + f(inv.get("fob_disc_amt")) + f(inv.get("other_ded_amt"))
        fob_fc_total = max(gross - less, 0.0)
        rows = []
        for it, amt in zip(mine, amts):
            per_invoice[n] = per_invoice.get(n, 0) + 1
            fob_fc = fob_fc_total * (amt / gross) if gross else 0.0
            fob_inr = r2(fob_fc * rate)
            qty = f(it.get("qty"))
            pmv_unit = f(it.get("pmv"))
            igst_val, igst_amt = f(it.get("taxable_value")), f(it.get("igst_amount"))
            tot["qty"] += qty; tot["fob"] += fob_inr; tot["pmv"] += pmv_unit * qty; tot["igst_val"] += igst_val; tot["igst_amt"] += igst_amt
            rows.append({"sl": per_invoice[n], "ritc": it.get("ritc_no"), "description": it.get("description"), "qty": num(qty, 3), "unit": it.get("qty_unit"), "rate": num(it.get("unit_price"), 5), "per": it.get("per") or "1", "value_fc": r2(amt),
                         "fob_inr": fob_inr, "sch_cd": (code(it.get("scheme")) or "00")[:2], "reward": "Y" if (it.get("reward") or "No") == "Yes" else "N", "scheme": (it.get("scheme") or "").split("-", 1)[-1], "end_use": it.get("end_use"), "pmv": pmv_unit,
                         "total_pmv": r2(pmv_unit * qty), "igst_status": code(it.get("igst_payment_status")), "igst_val": igst_val, "igst_amt": igst_amt, "sqc_qty": it.get("info_msr") or "", "sqc_unit": it.get("info_uqc") or it.get("qty_unit"),
                         "state_origin": it.get("state_of_origin"), "district": it.get("district"), "cess": it.get("gst_cess_amt") or "0", "pta": it.get("under_rta_fta"),
                         "sw": [{"type": it.get("sw_info_type"), "qualifier": it.get("sw_info_qualifier"), "code": it.get("sw_info_code"), "text": it.get("sw_info_text"), "msr": it.get("sw_info_msr"), "uqc": it.get("sw_info_uqc")}] if it.get("sw_info_type") else []})
        inv_out.append({"sl": n, "no": inv.get("invoice_no"), "date": dmy(inv.get("invoice_date")), "terms": inv.get("terms"), "payment": code(inv.get("payment_method")) or "NA", "period": inv.get("period_of_payment"), "contract": " / ".join(x for x in (inv.get("contract_no"), dmy(inv.get("contract_date"))) if x),
                        "currency": ccy, "rate": inv.get("exchange_rate") or rate, "value_fc": r2(fob_fc_total), "value_inr": round(fob_fc_total * rate, 3), "fob_fc": r2(fob_fc_total), "unit_price_includes": inv.get("unit_price_includes") or "N",
                        "buyer": inv.get("buyer_name") or ("SAME AS CONSIGNEE" if (inv.get("consignee_buyer_same") or "Yes") == "Yes" else ""),
                        "charges": [(label, inv.get(k + "_pct") or "0.00", inv.get(k + "_ccy") or ccy, inv.get(k + "_amt") or "0.00") for label, k in (("Insurance", "insurance"), ("Freight", "freight"), ("Commission", "commission"), ("Discount", "fob_disc"), ("Other Ded", "other_ded"), ("Package Charges", "pack_misc"))],
                        "third_party": [x for x in (inv.get("third_party_name"), inv.get("third_party_address1"), inv.get("third_party_address2"), inv.get("third_party_city")) if x], "items": rows})
    sez = "sez" in str((pd.get("unit_kind") or "")).lower()
    sw_info = [dict(s, inv=r["sl"], item=i["sl"]) for r in inv_out for i in r["items"] for s in i["sw"]]
    declarations = [{"inv": 1, "item": 1, "type": "DEC", "code": "CUG01", "text": ""}] if invoices else []
    return {**c, "direction": "Export", "title": "CHECK LIST - Shipping Bill" + (" for SEZ" if sez else ""), "state_of_origin": g("state_of_origin"), "sb_type": g("sb_type"),
            "exporter": {"iec": g("iec"), "name": g("exporter_name"), "branch": code(g("branch_sr_no")) or "0", "address": [g("addr1"), g("addr2"), g("addr3"), g("state") + (" " + g("pin") if g("pin") else "")], "gstn": f"{code(g('gstn_type'))} / {g('gstn_id')}",
                         "type": g("exporter_type"), "ad_code": g("ad_code"), "bank_account": g("bank_account"), "dbk_account": g("drawback_account"), "ifsc": g("ifsc_code")},
            "consignee": {"name": g("consignee"), "address": [g("cons_addr1"), g("cons_addr2"), g("cons_addr3")], "country": g("cons_country")}, "port_discharge": g("port_discharge"), "country_discharge": g("country_discharge") or g("country_destination"),
            "port_final": g("port_final_destination"), "country_final": g("country_final_destination"), "nfei": g("nfei"), "nfei_type": g("nfei_type"), "rbi_waiver": g("rbi_waiver_no"), "rbi_waiver_date": g("rbi_waiver_date"), "marks": g("marks"),
            "mbl": info.get("mbl") or "", "hbl": info.get("hbl") or "", "port_loading": g("port_loading"), "invoices": inv_out, "totals": {"qty": r2(tot["qty"]), "fob": r2(tot["fob"]), "pmv": r2(tot["pmv"]), "igst_val": r2(tot["igst_val"]), "igst_amt": r2(tot["igst_amt"])},
            "sw_info": sw_info, "declarations": declarations, "declaration_text": EXPORT_DECLARATION, "signatory": g("exporter_name")}
