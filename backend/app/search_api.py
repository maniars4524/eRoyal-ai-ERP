"""Global search box: one keyword looks across the company's jobs, parties, invoices and other documents, quotations, bookings, CRM records, payments, vouchers, masters and users.
Every group is returned only when the signed-in user may read it, and always inside the user's own company."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .auth import get_current_user, has_permission
from .db import get_db
from .models import AccLedger, AccVoucher, Airline, Booking, CrmRecord, Customer, Document, Payment, Quotation, Shipment, ShippingLine, User

router = APIRouter(prefix="/api/search", tags=["search"])
PER_GROUP = 6
DOC_SCREEN = {"invoice": ("invoice-list", "Invoice"), "debit_note": ("debit-note-list", "Debit Note"), "credit_note": ("invoice-list", "Credit Note"),
              "purchase_invoice": ("purchase-invoice-list", "Purchase Invoice"), "purchase_credit_note": ("purchase-invoice-list", "Purchase Credit Note")}
CRM_SCREEN = {"lead": "Lead", "enquiry": "Enquiry", "opportunity": "Opportunity", "followup": "Engagement", "booking": "Booking"}
CRM_GO = {"lead": "crm-lead", "enquiry": "crm-enquiry", "opportunity": "crm-opportunity", "followup": "crm-engagement", "booking": "booking"}
VOUCHER_SCREEN = {"RECEIPT": "receipt-voucher", "PAYMENT": "payment-voucher", "JOURNAL": "journal-voucher", "CONTRA": "contra-voucher"}


def _like(*cols, term: str):
    return or_(*[c.ilike(f"%{term}%") for c in cols])


@router.get("")
def search(q: str = Query(min_length=2, max_length=80), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    term = q.strip().replace("%", "").replace("_", r"\_")
    org = user.organization_id
    if org is None or len(term) < 2:
        return {"query": q, "groups": []}
    groups: list[dict] = []

    def add(title: str, items: list[dict]):
        if items:
            groups.append({"title": title, "items": items})

    if has_permission(user, "shipment.read"):
        rows = db.scalars(select(Shipment).where(Shipment.organization_id == org, _like(Shipment.job_number, Shipment.bl_awb, Shipment.consignee, Shipment.pol, Shipment.pod, Shipment.commodity, Shipment.salesperson, term=term))
                          .order_by(Shipment.id.desc()).limit(PER_GROUP))
        add("Jobs", [{"label": r.job_number, "sub": " · ".join(x for x in (r.mode, r.direction, f"{r.pol or ''} → {r.pod or ''}" if r.pol or r.pod else "", r.job_stage) if x), "go": "job-detail", "job": r.job_number} for r in rows])
    if has_permission(user, "customer.read") or has_permission(user, "organization.read"):
        rows = db.scalars(select(Customer).where(Customer.organization_id == org, _like(Customer.code, Customer.name, Customer.short_name, Customer.gstin, Customer.pan, Customer.country, term=term)).order_by(Customer.name).limit(PER_GROUP))
        add("Organizations", [{"label": r.name, "sub": " · ".join(x for x in (r.code, r.gstin, r.country) if x), "go": "org-list", "q": r.name} for r in rows])
    if has_permission(user, "billing.read"):
        rows = db.scalars(select(Document).where(Document.organization_id == org, _like(Document.doc_number, Document.party_name, Document.job_number, Document.party_gstin, Document.remarks, term=term)).order_by(Document.id.desc()).limit(PER_GROUP))
        add("Invoices & notes", [{"label": r.doc_number or "(draft)", "sub": " · ".join(x for x in (DOC_SCREEN.get(r.doc_type, ("", r.doc_type))[1], r.party_name, r.job_number, r.status) if x),
                                   "go": DOC_SCREEN.get(r.doc_type, ("invoice-list", ""))[0], "q": r.doc_number or r.party_name or ""} for r in rows])
    if has_permission(user, "quote.read"):
        rows = db.scalars(select(Quotation).where(Quotation.organization_id == org, _like(Quotation.number, Quotation.party_name, Quotation.pol, Quotation.pod, Quotation.job_number, term=term)).order_by(Quotation.id.desc()).limit(PER_GROUP))
        add("Quotations", [{"label": r.number, "sub": " · ".join(x for x in (r.party_name, r.mode, r.status) if x), "go": "quotation", "q": r.number} for r in rows])
    if has_permission(user, "booking.read"):
        rows = db.scalars(select(Booking).where(Booking.organization_id == org, _like(Booking.number, Booking.party_name, Booking.carrier, Booking.carrier_booking_no, Booking.vessel, Booking.job_number, Booking.pol, Booking.pod, term=term)).order_by(Booking.id.desc()).limit(PER_GROUP))
        add("Bookings", [{"label": r.number, "sub": " · ".join(x for x in (r.party_name, r.carrier, r.status) if x), "go": "booking", "q": r.number, "booking": r.id} for r in rows])
    if has_permission(user, "customer.read"):
        rows = db.scalars(select(CrmRecord).where(CrmRecord.organization_id == org, _like(CrmRecord.ref, CrmRecord.company, CrmRecord.status, term=term)).order_by(CrmRecord.id.desc()).limit(PER_GROUP))
        add("CRM", [{"label": r.ref, "sub": " · ".join(x for x in (CRM_SCREEN.get(r.kind, r.kind), r.company, r.status) if x), "go": CRM_GO.get(r.kind, "crm-lead"), "q": r.ref} for r in rows])
    if has_permission(user, "payment.read"):
        rows = db.scalars(select(Payment).where(Payment.organization_id == org, _like(Payment.pay_number, Payment.party_name, Payment.reference, term=term)).order_by(Payment.id.desc()).limit(PER_GROUP))
        add("Payments & receipts", [{"label": r.pay_number or "(payment)", "sub": " · ".join(x for x in (r.direction, r.party_name, r.method, r.status) if x), "go": "receipt-voucher" if (r.direction or "").upper() == "RECEIPT" else "payment-voucher", "q": r.pay_number or ""} for r in rows])
    if has_permission(user, "billing.read"):
        rows = db.scalars(select(AccVoucher).where(AccVoucher.organization_id == org, _like(AccVoucher.number, AccVoucher.party_name, AccVoucher.narration, AccVoucher.job_number, term=term)).order_by(AccVoucher.id.desc()).limit(PER_GROUP))
        add("Vouchers", [{"label": r.number, "sub": " · ".join(x for x in (r.voucher_type, r.party_name, r.narration) if x), "go": VOUCHER_SCREEN.get((r.voucher_type or "").upper(), "gl-reports"), "q": r.number} for r in rows])
        rows = db.scalars(select(AccLedger).where(AccLedger.organization_id == org, _like(AccLedger.code, AccLedger.name, AccLedger.alias, term=term)).order_by(AccLedger.name).limit(PER_GROUP))
        add("Ledgers", [{"label": r.name, "sub": r.code or "", "go": "coa", "q": r.name} for r in rows])
    if has_permission(user, "master.read"):
        rows = db.scalars(select(Airline).where(Airline.organization_id == org, _like(Airline.code, Airline.name, Airline.num_code, Airline.icao_code, term=term)).order_by(Airline.name).limit(PER_GROUP))
        add("Airlines", [{"label": r.name, "sub": " · ".join(x for x in (r.code, r.num_code) if x), "go": "net-airlines", "q": r.name} for r in rows])
        rows = db.scalars(select(ShippingLine).where(ShippingLine.organization_id == org, _like(ShippingLine.code, ShippingLine.name, ShippingLine.scac_code, term=term)).order_by(ShippingLine.name).limit(PER_GROUP))
        add("Sea lines", [{"label": r.name, "sub": " · ".join(x for x in (r.code, r.scac_code) if x), "go": "net-sea-lines", "q": r.name} for r in rows])
    if has_permission(user, "user.read"):
        rows = db.scalars(select(User).where(User.organization_id == org, _like(User.username, User.first_name, User.last_name, User.email, term=term)).order_by(User.username).limit(PER_GROUP))
        add("Users", [{"label": ((r.first_name or "") + " " + (r.last_name or "")).strip() or r.username, "sub": f"{r.username} · {r.email}", "go": "user-master", "q": r.username} for r in rows])
    return {"query": q, "groups": groups}
