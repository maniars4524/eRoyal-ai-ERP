"""Universal search (header search box): one keyword looks across every module the signed-in user may read and returns the matching records classified as
Department > Division (Import / Export / Domestic / International / Common) > Service type (Sea / Air / Road Freight, Customs Clearance, Freight Forwarding, Transportation ...) >
Transaction category (Jobs, Bookings, Quotations, Invoices, Vouchers ...) > the record itself.

Read-only: nothing is written and no table is changed. Every source is read only when the user holds the permission that opens that module, always inside the user's own company.
References are matched as typed AND with separators / prefixes ignored (JOB-0001, JOB0001 and job 0001 find the same record). The older flat search (/api/search) is untouched."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from datetime import timedelta

from sqlalchemy import DateTime, String, and_, cast, exists, func, or_, select
from sqlalchemy.orm import Session

from .access import customer_clause, scope_for, shipment_clause
from .auth import get_current_user, has_permission
from .db import get_db
from .models import (AccLedger, AccVoucher, Airline, Booking, CrmRecord, CustomsBill, Customer, Document, JobDetail, MasterRecord, Payment, Port, Quotation, Shipment, ShippingLine, User)

router = APIRouter(prefix="/api/search", tags=["search"])
CAP = 40                    # records fetched per source on the first answer; "View all" pages through the rest
DEPARTMENTS = ["Logistics & Customs", "Operations", "Accounts & Finance", "CRM", "Masters", "Other Modules"]
DIVISIONS = ["Import", "Export", "Domestic", "International", "Common"]
SERVICES = ["Sea Freight", "Air Freight", "Road Freight", "Customs Clearance", "Freight Forwarding", "Transportation", "Total Logistics", "Not applicable"]
CATEGORIES = ["Jobs", "Bookings", "Quotations", "Leads", "Enquiries", "Opportunities", "Follow-ups", "Invoices", "Debit Notes", "Credit Notes", "Vouchers", "Vendor Bills", "Receipts", "Payments", "Documents",
              "Customers", "Vendors", "Agents", "Parties", "Ledgers", "Master Records", "Users", "Other Transactions"]
DIVISION_OF = {"Import": "Import", "Export": "Export", "Domestic": "Domestic", "Cross-Trade": "International", "International": "International"}
GO_DOC = {"invoice": "invoice-list", "debit_note": "debit-note-list", "credit_note": "invoice-list", "purchase_invoice": "purchase-invoice-list", "purchase_credit_note": "purchase-invoice-list"}
DOC_LABEL = {"invoice": "Sales Invoice", "debit_note": "Debit Note", "credit_note": "Credit Note", "purchase_invoice": "Vendor Bill / Purchase Invoice", "purchase_credit_note": "Purchase Credit Note"}
DOC_CATEGORY = {"invoice": "Invoices", "debit_note": "Debit Notes", "credit_note": "Credit Notes", "purchase_invoice": "Vendor Bills", "purchase_credit_note": "Credit Notes"}
CRM_KIND = {"lead": ("Lead", "Leads", "crm-lead"), "enquiry": ("Enquiry", "Enquiries", "crm-enquiry"), "opportunity": ("Opportunity", "Opportunities", "crm-opportunity"),
            "followup": ("Follow-up", "Follow-ups", "crm-engagement"), "booking": ("CRM Booking", "Bookings", "booking")}
VOUCHER_GO = {"RECEIPT": "receipt-voucher", "PAYMENT": "payment-voucher", "JOURNAL": "journal-voucher", "CONTRA": "contra-voucher"}


def norm(v: str) -> str:
    return "".join(ch for ch in (v or "").lower() if ch.isalnum())


def service_of(job_type: str | None, mode: str | None) -> str:
    if job_type == "Customs":
        return "Customs Clearance"
    if job_type == "Transport":
        return "Transportation"
    return {"Sea": "Sea Freight", "Air": "Air Freight", "Road": "Road Freight"}.get(mode or "", "Freight Forwarding" if mode else "Total Logistics")


def service_of_mode(mode: str | None) -> str:
    return {"Sea": "Sea Freight", "Air": "Air Freight", "Road": "Road Freight"}.get(mode or "", "Freight Forwarding" if mode else "Not applicable")


def day(v) -> str | None:
    return v.date().isoformat() if hasattr(v, "date") else (v.isoformat() if v else None)


def matcher(term: str, tn: str, *cols):
    """term as typed (case-insensitive contains) OR the same reference with separators ignored."""
    parts = []
    for c in cols:
        c = cast(c, String) if not isinstance(getattr(c, "type", None), String) else c
        parts.append(c.ilike(f"%{term}%"))
        if len(tn) >= 2:
            parts.append(func.regexp_replace(func.lower(c), "[^a-z0-9]", "", "g").like(f"%{tn}%"))
    return or_(*parts)


class F:
    """The filters of one request."""
    def __init__(self, department, division, mode, service, category, party, status, date_from, date_to, created_by):
        self.department, self.division, self.mode, self.service, self.category = department, division, mode, service, category
        self.party, self.status, self.date_from, self.date_to, self.created_by = (party or "").strip(), (status or "").strip(), date_from, date_to, created_by


def _date_status_party(stmt, f: F, date_col=None, status_col=None, party_cols=(), created_col=None):
    if f.date_from and date_col is not None:
        stmt = stmt.where(date_col >= f.date_from)
    if f.date_to and date_col is not None:
        stmt = stmt.where(date_col < f.date_to + timedelta(days=1)) if isinstance(date_col.type, DateTime) else stmt.where(date_col <= f.date_to)
    if f.status and status_col is not None:
        stmt = stmt.where(status_col.ilike(f"%{f.status}%"))
    if f.party and party_cols:
        stmt = stmt.where(or_(*[c.ilike(f"%{f.party}%") for c in party_cols]))
    if f.created_by and created_col is not None:
        stmt = stmt.where(created_col == f.created_by)
    return stmt


def _division_filter(stmt, f: F, dir_col):
    if not f.division:
        return stmt
    if dir_col is None:
        return None if f.division != "Common" else stmt
    if f.division == "Common":
        return None
    raw = {"International": ["Cross-Trade", "International"]}.get(f.division, [f.division])
    return stmt.where(dir_col.in_(raw))


def _service_filter(stmt, f: F, job_type_col, mode_col):
    """Service-type filter pushed into the query for the sources that have a job type / mode."""
    if not f.service:
        return stmt
    if f.service == "Customs Clearance":
        return stmt.where(job_type_col == "Customs") if job_type_col is not None else None
    if f.service == "Transportation":
        return stmt.where(job_type_col == "Transport") if job_type_col is not None else None
    if f.service in ("Sea Freight", "Air Freight", "Road Freight"):
        m = {"Sea Freight": "Sea", "Air Freight": "Air", "Road Freight": "Road"}[f.service]
        s = stmt.where(mode_col == m) if mode_col is not None else None
        return s.where(job_type_col.notin_(["Customs", "Transport"])) if (s is not None and job_type_col is not None) else s
    if f.service == "Freight Forwarding":
        s = stmt.where(mode_col.notin_(["Sea", "Air", "Road"])) if mode_col is not None else None
        return s.where(job_type_col.notin_(["Customs", "Transport"])) if (s is not None and job_type_col is not None) else s
    return None


def _item(**kw) -> dict:
    kw.setdefault("reference", "")
    kw.setdefault("party", "")
    kw.setdefault("status", "")
    kw.setdefault("date", None)
    kw.setdefault("division", "Common")
    kw.setdefault("service", "Not applicable")
    kw.setdefault("mode", "")
    return kw


# ---------------------------------------------------------------- sources: each returns (statement, row -> item) or None when a filter rules it out
def src_jobs(db, org, term, tn, f: F):
    stmt = (select(Shipment, Customer.name).outerjoin(Customer, Customer.id == Shipment.customer_id).where(Shipment.organization_id == org))
    box = exists(select(JobDetail.id).where(JobDetail.shipment_id == Shipment.id, or_(cast(JobDetail.containers, String).ilike(f"%{term}%"), cast(JobDetail.bl, String).ilike(f"%{term}%"),
                                                                             cast(JobDetail.cargo, String).ilike(f"%{term}%"))))
    stmt = stmt.where(or_(matcher(term, tn, Shipment.job_number, Shipment.bl_awb, Shipment.consignee, Shipment.pol, Shipment.pod, Shipment.commodity, Shipment.salesperson, Shipment.branch, Customer.name), box))
    if f.mode:
        stmt = stmt.where(Shipment.mode == f.mode)
    stmt = _division_filter(stmt, f, Shipment.direction)
    stmt = _service_filter(stmt, f, Shipment.job_type, Shipment.mode) if stmt is not None else None
    if stmt is None:
        return None
    stmt = _date_status_party(stmt, f, Shipment.created_at, Shipment.status, (Customer.name, Shipment.consignee), Shipment.created_by).order_by(Shipment.id.desc())

    def row(r):
        s, cust = r
        return _item(ref=s.job_number, type="Customs Job" if s.job_type == "Customs" else "Transport Job" if s.job_type == "Transport" else f"{s.mode} {s.direction} Job", department="Logistics & Customs",
                     division=DIVISION_OF.get(s.direction, "Common"), service=service_of(s.job_type, s.mode), category="Jobs", mode=s.mode or "", party=cust or s.consignee or "", reference=s.bl_awb or "",
                     date=day(s.created_at), status=s.job_stage or s.status or "", go="job-detail", job=s.job_number, _jobid=s.id)
    return stmt, row


def src_customs(db, org, term, tn, f: F):
    stmt = select(CustomsBill, Shipment).join(Shipment, Shipment.id == CustomsBill.shipment_id).where(CustomsBill.organization_id == org, matcher(term, tn, CustomsBill.bill_no, CustomsBill.reference, CustomsBill.port_code))
    if f.mode:
        stmt = stmt.where(Shipment.mode == f.mode)
    stmt = _division_filter(stmt, f, Shipment.direction)
    if stmt is None or f.service not in ("", "Customs Clearance", None):
        return None
    stmt = _date_status_party(stmt, f, CustomsBill.bill_date, CustomsBill.status, (Shipment.consignee,), CustomsBill.created_by).order_by(CustomsBill.id.desc())

    def row(r):
        b, s = r
        return _item(ref=b.bill_no, type="Bill of Entry" if b.bill_type == "BE" else "Shipping Bill", department="Logistics & Customs", division=DIVISION_OF.get(s.direction, "Common"),
                     service="Customs Clearance", category="Documents", mode=s.mode or "", party=s.consignee or "", reference=f"Job {s.job_number}", date=day(b.bill_date), status=b.status or "", go="job-detail", job=s.job_number)
    return stmt, row


def src_bookings(db, org, term, tn, f: F):
    stmt = select(Booking).where(Booking.organization_id == org, matcher(term, tn, Booking.number, Booking.party_name, Booking.carrier, Booking.carrier_booking_no, Booking.vessel, Booking.job_number, Booking.pol, Booking.pod))
    if f.mode:
        stmt = stmt.where(Booking.mode == f.mode)
    stmt = _division_filter(stmt, f, Booking.direction)
    stmt = _service_filter(stmt, f, None, Booking.mode) if stmt is not None else None
    if stmt is None and f.service:
        return None
    if stmt is None:
        return None
    stmt = _date_status_party(stmt, f, Booking.booking_date, Booking.status, (Booking.party_name,), Booking.created_by).order_by(Booking.id.desc())

    def row(b):
        return _item(ref=b.number or "(draft booking)", type=f"{b.mode} {b.direction} Booking", department="Operations", division=DIVISION_OF.get(b.direction, "Common"), service=service_of_mode(b.mode),
                     category="Bookings", mode=b.mode or "", party=b.party_name or "", reference=b.carrier_booking_no or b.vessel or "", date=day(b.booking_date), status=b.status or "", go="booking", q=b.number or "", booking=b.id)
    return stmt, row


def src_quotations(db, org, term, tn, f: F):
    stmt = select(Quotation).where(Quotation.organization_id == org, matcher(term, tn, Quotation.number, Quotation.party_name, Quotation.pol, Quotation.pod, Quotation.job_number))
    if f.mode:
        stmt = stmt.where(Quotation.mode == f.mode)
    stmt = _division_filter(stmt, f, Quotation.direction)
    stmt = _service_filter(stmt, f, None, Quotation.mode) if stmt is not None else None
    if stmt is None:
        return None
    stmt = _date_status_party(stmt, f, Quotation.quote_date, Quotation.status, (Quotation.party_name,), Quotation.created_by).order_by(Quotation.id.desc())

    def row(q):
        return _item(ref=q.number or "(draft quotation)", type=f"{q.mode} {q.direction} Quotation", department="CRM", division=DIVISION_OF.get(q.direction, "Common"), service=service_of_mode(q.mode), category="Quotations",
                     mode=q.mode or "", party=q.party_name or "", reference=f"{q.pol or ''} → {q.pod or ''}".strip(" →"), date=day(q.quote_date), status=q.status or "", go="quotation", q=q.number or "")
    return stmt, row


def src_crm(db, org, term, tn, f: F):
    if f.division not in ("", "Common") or f.mode or f.service:
        return None
    stmt = select(CrmRecord).where(CrmRecord.organization_id == org, matcher(term, tn, CrmRecord.ref, CrmRecord.company, CrmRecord.status))
    stmt = _date_status_party(stmt, f, CrmRecord.created_at, CrmRecord.status, (CrmRecord.company,), CrmRecord.created_by).order_by(CrmRecord.id.desc())

    def row(r):
        label, cat, go = CRM_KIND.get(r.kind, (r.kind.title(), "Other Transactions", "crm-lead"))
        return _item(ref=r.ref, type=label, department="CRM", category=cat, party=r.company or "", date=day(r.created_at), status=r.status or "", go=go, q=r.ref)
    return stmt, row


def src_documents(db, org, term, tn, f: F):
    if f.mode or f.service:
        return None
    stmt = select(Document).where(Document.organization_id == org, matcher(term, tn, Document.doc_number, Document.party_name, Document.job_number, Document.party_gstin, Document.remarks, Document.header))
    stmt = _date_status_party(stmt, f, Document.doc_date, Document.status, (Document.party_name,), Document.created_by).order_by(Document.id.desc())
    if f.category:
        types = [t for t, c in DOC_CATEGORY.items() if c == f.category]
        stmt = stmt.where(Document.doc_type.in_(types or ["-"]))

    def row(d):
        pf = (d.header or {}).get("proforma_no") if isinstance(d.header, dict) else None
        return _item(ref=d.doc_number or pf or f"(draft #{d.id})", type=DOC_LABEL.get(d.doc_type, d.doc_type) + ("" if d.doc_number else " (Proforma/draft)"), department="Accounts & Finance",
                     category=DOC_CATEGORY.get(d.doc_type, "Other Transactions"), party=d.party_name or "", reference=("Job " + d.job_number) if d.job_number else "", date=day(d.doc_date), status=d.status or "",
                     go=GO_DOC.get(d.doc_type, "invoice-list"), q=d.doc_number or d.party_name or "", _job=d.job_number)
    return stmt, row


def src_payments(db, org, term, tn, f: F):
    if f.mode or f.service or f.division not in ("", "Common"):
        return None
    stmt = select(Payment).where(Payment.organization_id == org, matcher(term, tn, Payment.pay_number, Payment.party_name, Payment.reference))
    stmt = _date_status_party(stmt, f, Payment.pay_date, Payment.status, (Payment.party_name,), Payment.created_by).order_by(Payment.id.desc())

    def row(p):
        receipt = (p.direction or "").upper().startswith("R")
        return _item(ref=p.pay_number or "(payment)", type="Customer Receipt" if receipt else "Vendor Payment", department="Accounts & Finance", category="Receipts" if receipt else "Payments", party=p.party_name or "",
                     reference=p.reference or p.method or "", date=day(p.pay_date), status=p.status or "", go="receipt-voucher" if receipt else "payment-voucher", q=p.pay_number or "")
    return stmt, row


def src_vouchers(db, org, term, tn, f: F):
    if f.mode or f.service or f.division not in ("", "Common"):
        return None
    stmt = select(AccVoucher).where(AccVoucher.organization_id == org, matcher(term, tn, AccVoucher.number, AccVoucher.party_name, AccVoucher.narration, AccVoucher.job_number))
    stmt = _date_status_party(stmt, f, AccVoucher.vdate, AccVoucher.status, (AccVoucher.party_name,), AccVoucher.created_by).order_by(AccVoucher.id.desc())

    def row(v):
        return _item(ref=v.number, type=(v.voucher_type or "").title() + " Voucher", department="Accounts & Finance", category="Vouchers", party=v.party_name or "", reference=("Job " + v.job_number) if v.job_number else (v.narration or "")[:60],
                     date=day(v.vdate), status=v.status or "", go=VOUCHER_GO.get((v.voucher_type or "").upper(), "gl-reports"), q=v.number, _job=v.job_number)
    return stmt, row


def src_ledgers(db, org, term, tn, f: F):
    if f.mode or f.service or f.division not in ("", "Common") or f.status or f.party or f.date_from or f.date_to:
        return None
    stmt = select(AccLedger).where(AccLedger.organization_id == org, matcher(term, tn, AccLedger.code, AccLedger.name, AccLedger.alias)).order_by(AccLedger.name)

    def row(l):
        return _item(ref=l.name, type="Ledger", department="Accounts & Finance", category="Ledgers", reference=l.code or "", status=l.status or "", go="coa", q=l.name)
    return stmt, row


def src_parties(db, org, term, tn, f: F):
    if f.mode or f.service or f.division not in ("", "Common"):
        return None
    stmt = select(Customer).where(Customer.organization_id == org, matcher(term, tn, Customer.code, Customer.name, Customer.short_name, Customer.gstin, Customer.pan, Customer.country))
    stmt = _date_status_party(stmt, f, Customer.created_at, Customer.status, (Customer.name,), Customer.created_by).order_by(Customer.name)

    def row(c):
        roles = [str(x).lower() for x in (c.roles or [])]
        cat, label = ("Vendors", "Vendor") if any(r in ("vendor", "supplier", "carrier") for r in roles) and "client" not in roles and "shipper" not in roles else \
                     ("Agents", "Agent") if any("agent" in r for r in roles) and not any(r in ("client", "shipper", "consignee") for r in roles) else \
                     ("Customers", "Customer") if any(r in ("client", "shipper", "consignee", "customer") for r in roles) else ("Parties", "Organization")
        return _item(ref=c.name, type=label, department="CRM", category=cat, party=c.name, reference=" · ".join(x for x in (c.code, c.gstin) if x), date=day(c.created_at), status=c.status or "", go="org-list", q=c.name)
    return stmt, row


def src_masters(db, org, term, tn, f: F):
    if f.mode or f.service or f.division not in ("", "Common") or f.party or f.date_from or f.date_to:
        return None
    stmt = select(MasterRecord).where(MasterRecord.organization_id == org, matcher(term, tn, MasterRecord.code, MasterRecord.name)).order_by(MasterRecord.name)
    if f.status:
        stmt = stmt.where(MasterRecord.status.ilike(f"%{f.status}%"))

    def row(m):
        return _item(ref=m.name, type=(m.master_type or "").replace("_", " ").title() + " Master", department="Masters", category="Master Records", reference=m.code or "", status=m.status or "", go="masters", q=m.name)
    return stmt, row


def _network(model, label, go, dept="Masters"):
    def build(db, org, term, tn, f: F):
        if f.mode in ("Road", "Rail") or f.service or f.division not in ("", "Common") or f.party or f.date_from or f.date_to:
            return None
        cols = [model.code, model.name] + ([model.unlocode, model.city] if model is Port else [])
        stmt = select(model).where(matcher(term, tn, *cols)).order_by(model.name)
        if model is not Port:
            stmt = stmt.where(model.organization_id == org)
        if f.status:
            stmt = stmt.where(model.status.ilike(f"%{f.status}%"))

        def row(m):
            return _item(ref=m.name, type=label, department=dept, category="Master Records", reference=" · ".join(x for x in (m.code, getattr(m, "unlocode", None), getattr(m, "country", None)) if x), status=getattr(m, "status", "") or "", go=go, q=m.name)
        return stmt, row
    return build


def src_users(db, org, term, tn, f: F):
    if f.mode or f.service or f.division not in ("", "Common") or f.party or f.date_from or f.date_to:
        return None
    stmt = select(User).where(User.organization_id == org, matcher(term, tn, User.username, User.first_name, User.last_name, User.email)).order_by(User.username)

    def row(u):
        return _item(ref=((u.first_name or "") + " " + (u.last_name or "")).strip() or u.username, type="User", department="Other Modules", category="Users", reference=u.username, status=u.status or "", go="user-master", q=u.username)
    return stmt, row


SOURCE_CATEGORIES = {"jobs": {"Jobs"}, "customs": {"Documents"}, "bookings": {"Bookings"}, "quotations": {"Quotations"}, "crm": {"Leads", "Enquiries", "Opportunities", "Follow-ups", "Bookings"},
                     "documents": {"Invoices", "Debit Notes", "Credit Notes", "Vendor Bills"}, "payments": {"Receipts", "Payments"}, "vouchers": {"Vouchers"}, "ledgers": {"Ledgers"},
                     "parties": {"Customers", "Vendors", "Agents", "Parties"}, "masters": {"Master Records"}, "airlines": {"Master Records"}, "sealines": {"Master Records"}, "ports": {"Master Records"}, "users": {"Users"}}
OPEN_SOURCES = {"masters", "airlines", "sealines", "ports"}        # reference data every placed user may search; the rest need a record filter first

# key, department, permission(s), builder
SOURCES = [
    ("jobs", "Logistics & Customs", ("shipment.read",), src_jobs),
    ("customs", "Logistics & Customs", ("shipment.read",), src_customs),
    ("bookings", "Operations", ("booking.read",), src_bookings),
    ("quotations", "CRM", ("quote.read",), src_quotations),
    ("crm", "CRM", ("customer.read",), src_crm),
    ("documents", "Accounts & Finance", ("billing.read",), src_documents),
    ("payments", "Accounts & Finance", ("payment.read",), src_payments),
    ("vouchers", "Accounts & Finance", ("billing.read",), src_vouchers),
    ("ledgers", "Accounts & Finance", ("billing.read",), src_ledgers),
    ("parties", "CRM", ("customer.read", "organization.read"), src_parties),
    ("masters", "Masters", ("master.read",), src_masters),
    ("airlines", "Masters", ("master.read",), _network(Airline, "Airline Master", "net-airlines")),
    ("sealines", "Masters", ("master.read",), _network(ShippingLine, "Shipping Line Master", "net-sea-lines")),
    ("ports", "Masters", ("master.read",), _network(Port, "Port / Airport Master", "port-master")),
    ("users", "Other Modules", ("user.read",), src_users),
]


def _enrich_by_job(db: Session, org: int, items: list[dict]) -> None:
    """Invoices, vouchers ... that belong to a job take that job's Import / Export and Air / Sea / Road classification."""
    jobs = {i["_job"] for i in items if i.get("_job")}
    if not jobs:
        return
    by = {s.job_number: s for s in db.scalars(select(Shipment).where(Shipment.organization_id == org, Shipment.job_number.in_(jobs)))}
    for i in items:
        s = by.get(i.get("_job"))
        if s:
            i["division"], i["service"], i["mode"] = DIVISION_OF.get(s.direction, "Common"), service_of(s.job_type, s.mode), s.mode or ""


def _containers_note(db: Session, items: list[dict], term: str, tn: str) -> None:
    """A job found through a container / BL / HBL number shows that number as its reference."""
    ids = [i["_jobid"] for i in items if i.get("_jobid") and tn and tn not in norm(i["ref"])]
    if not ids:
        return
    for d in db.scalars(select(JobDetail).where(JobDetail.shipment_id.in_(ids))):
        hits = []
        for c in (d.containers or []):
            no = str((c or {}).get("container_no") or (c or {}).get("no") or "")
            if no and (term.lower() in no.lower() or tn in norm(no)):
                hits.append("Container " + no)
        for k, v in ((d.bl or {}).items() if isinstance(d.bl, dict) else []):
            if isinstance(v, str) and v and (term.lower() in v.lower() or tn in norm(v)) and len(v) < 40:
                hits.append(f"{k.upper()} {v}")
        if hits:
            for i in items:
                if i.get("_jobid") == d.shipment_id:
                    i["reference"] = "; ".join(hits[:2])


@router.get("/universal")
def universal(q: str = Query(min_length=2, max_length=80), department: str = "", division: str = "", mode: str = "", service: str = "", category: str = "", party: str = "", status: str = "",
              date_from: date | None = None, date_to: date | None = None, created_by: int | None = None, source: str = "", offset: int = Query(default=0, ge=0), limit: int = Query(default=CAP, ge=1, le=100),
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    org = user.organization_id
    term = q.strip().replace("%", "").replace("\\", "")
    tn = norm(term)
    if org is None or len(term) < 2:
        return {"query": q, "total": 0, "shown": 0, "sources": [], "items": []}
    f = F(department, division, mode, service, category, party, status, date_from, date_to, created_by)
    items: list[dict] = []
    meta: list[dict] = []
    scope = scope_for(db, user)
    job_clause, cust_clause = shipment_clause(db, user, scope), customer_clause(db, user, scope)
    for key, dept, perms, build in SOURCES:
        if source and key != source:
            continue
        if f.department and f.department != dept:
            continue
        if f.category and f.category not in SOURCE_CATEGORIES.get(key, set()):
            continue
        if not any(has_permission(user, p) for p in perms):                       # a module the user may not open is never searched
            continue
        built = build(db, org, term, tn, f)
        if not built:
            continue
        stmt, to_item = built
        if not scope.unrestricted:                                                  # a placed user: only what their role may see; sources without a filter yet stay closed
            if key in ("jobs", "customs"):
                stmt = stmt.where(job_clause)
            elif key == "parties":
                if cust_clause is not None:
                    stmt = stmt.where(cust_clause)
            elif key not in OPEN_SOURCES:
                continue
        total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
        if not total:
            continue
        rows = db.execute(stmt.offset(offset if source else 0).limit(limit if source else CAP)).all()
        got = [to_item(r if len(r) > 1 else r[0]) for r in rows]
        for g in got:
            g["source"], g["key"] = key, f"{key}:{g.get('job') or g.get('q') or g['ref']}:{g['ref']}:{len(items)}"
        if f.category:
            got = [g for g in got if g["category"] == f.category]
        items += got
        meta.append({"source": key, "department": dept, "total": total, "shown": len(got), "offset": (offset if source else 0)})
    _enrich_by_job(db, org, items)
    _containers_note(db, items, term, tn)
    if f.division:
        items = [i for i in items if i["division"] == f.division] if f.division in DIVISIONS else items
    if f.service:
        items = [i for i in items if i["service"] == f.service]
    if f.mode:
        items = [i for i in items if not i["mode"] or i["mode"] == f.mode]
    for i in items:
        i.pop("_job", None)
        i.pop("_jobid", None)
    return {"query": q, "total": sum(m["total"] for m in meta), "shown": len(items), "sources": meta, "items": items,
            "facets": {"departments": DEPARTMENTS, "divisions": DIVISIONS, "services": SERVICES, "categories": CATEGORIES, "modes": ["Sea", "Air", "Road"]}}
