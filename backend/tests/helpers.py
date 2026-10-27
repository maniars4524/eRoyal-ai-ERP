"""Confirming a booking only issues its booking number. The freight job and the CHA job are made afterwards, each from the booking number (Freight module / CHA module); this helper does both steps for tests that need the jobs."""


class Confirmed:
    def __init__(self, response, body):
        self.status_code, self.text, self._body = response.status_code, response.text, body

    def json(self):
        return self._body


def confirm_with_jobs(client, h, booking_id, services=None):
    r = client.post(f"/api/bookings/{booking_id}/confirm", headers=h)
    if r.status_code != 200:
        return r
    body = r.json()
    booked = [x.strip() for x in str((body.get("data") or {}).get("services") or "Freight").split(",") if x.strip()]
    created = []
    for service in services or ("Freight", "CHA"):
        if service in booked:
            made = client.post(f"/api/bookings/{booking_id}/jobs", json={"service": service}, headers=h)
            assert made.status_code == 201, made.text
            created.append(made.json()["created_job"])
            body = made.json()
    body["created_jobs"] = created
    return Confirmed(r, body)
