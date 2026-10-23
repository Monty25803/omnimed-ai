package main

import (
	"context"
	"fmt"
	"math"
	"net/http"
	"sort"
	"strconv"
	"time"
)

func (a *App) pharmacyInventory(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	if err := requireRole(p, "pharmacist"); err != nil {
		writeErr(w, err)
		return
	}
	rows, err := a.db.Query(r.Context(), `SELECT i.id, i.rxcui, d.ingredient, i.brand, i.strength_mg, i.form, i.is_generic, i.unit_price::float8, i.qty, i.reorder_level, i.barcode,
		coalesce((SELECT sum(ds.qty) FROM clinical.dispenses ds WHERE ds.inventory_id=i.id AND ds.dispensed_at > now() - interval '14 days'),0)
		FROM clinical.inventory i JOIN clinical.drugs d USING (rxcui) WHERE pharmacy_id=$1 ORDER BY d.ingredient, i.brand`, derefOr(p.Staff.PharmacyID, ""))
	if err != nil {
		writeErr(w, err)
		return
	}
	defer rows.Close()
	out := []map[string]any{}
	for rows.Next() {
		var id, qty, reorder int
		var used14 int64
		var rxcui, ing, brand, form, barcode string
		var strength, price float64
		var generic bool
		if err := rows.Scan(&id, &rxcui, &ing, &brand, &strength, &form, &generic, &price, &qty, &reorder, &barcode, &used14); err != nil {
			writeErr(w, err)
			return
		}
		perDay := float64(used14) / 14
		var cover any
		if perDay > 0 {
			cover = math.Round(float64(qty)/perDay*10) / 10
		}
		status := "ok"
		switch {
		case qty == 0:
			status = "out-of-stock"
		case qty <= reorder:
			status = "reorder"
		case perDay > 0 && float64(qty)/perDay < 7:
			status = "impending-stockout"
		}
		out = append(out, map[string]any{"id": id, "rxcui": rxcui, "ingredient": ing, "brand": brand, "strength_mg": strength, "form": form, "generic": generic,
			"unit_price": price, "qty": qty, "reorder_level": reorder, "barcode": barcode, "used_14d": used14, "days_of_cover": cover, "status": status})
	}
	writeJSON(w, 200, out)
}

// inventorySync applies counts pushed from the pharmacy management system.
func (a *App) inventorySync(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	if err := requireRole(p, "pharmacist"); err != nil {
		writeErr(w, err)
		return
	}
	var req struct {
		Items []struct {
			Barcode string `json:"barcode"`
			Qty     int    `json:"qty"`
		} `json:"items"`
	}
	if err := decode(r, &req); err != nil {
		writeErr(w, err)
		return
	}
	updated, unknown := 0, []string{}
	for _, it := range req.Items {
		if it.Qty < 0 {
			writeErr(w, errf(422, "quantities cannot be negative"))
			return
		}
		tag, err := a.db.Exec(r.Context(), `UPDATE clinical.inventory SET qty=$2, updated_at=now() WHERE barcode=$1 AND pharmacy_id=$3`, it.Barcode, it.Qty, derefOr(p.Staff.PharmacyID, ""))
		if err != nil {
			writeErr(w, err)
			return
		}
		if tag.RowsAffected() == 0 {
			unknown = append(unknown, it.Barcode)
		} else {
			updated++
		}
	}
	a.audit(r.Context(), AuditEntry{Actor: p.Username, Role: p.Role, Action: "inventory.sync", Resource: "Inventory", Facility: p.Facility(), Outcome: "allowed", Detail: map[string]any{"updated": updated, "unknown": unknown}})
	writeJSON(w, 200, map[string]any{"updated": updated, "unknown_barcodes": unknown})
}

func (a *App) substitutes(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	if err := requireRole(p, "pharmacist", "doctor"); err != nil {
		writeErr(w, err)
		return
	}
	rxcui := r.URL.Query().Get("rxcui")
	strength, _ := strconv.ParseFloat(r.URL.Query().Get("strength_mg"), 64)
	d, err := a.drug(r.Context(), rxcui)
	if err != nil {
		writeErr(w, err)
		return
	}
	lat, lon := a.prescriberLocation(r.Context(), p)
	rows, err := a.db.Query(r.Context(), `SELECT i.id, i.brand, i.is_generic, i.unit_price::float8, i.qty, i.form, ph.id, ph.name, ph.lat, ph.lon
		FROM clinical.inventory i JOIN clinical.pharmacies ph ON ph.id=i.pharmacy_id
		WHERE i.rxcui=$1 AND i.strength_mg=$2 AND i.bioequivalence_ok ORDER BY i.unit_price`, rxcui, strength)
	if err != nil {
		writeErr(w, err)
		return
	}
	defer rows.Close()
	type sub struct {
		InventoryID int     `json:"inventory_id"`
		Brand       string  `json:"brand"`
		Generic     bool    `json:"generic"`
		UnitPrice   float64 `json:"unit_price"`
		Qty         int     `json:"qty"`
		Form        string  `json:"form"`
		PharmacyID  string  `json:"pharmacy_id"`
		Pharmacy    string  `json:"pharmacy"`
		DistanceMi  float64 `json:"distance_miles"`
		SavingPct   float64 `json:"saving_vs_costliest_pct"`
	}
	subs := []sub{}
	maxPrice := 0.0
	for rows.Next() {
		var s sub
		var plat, plon float64
		if err := rows.Scan(&s.InventoryID, &s.Brand, &s.Generic, &s.UnitPrice, &s.Qty, &s.Form, &s.PharmacyID, &s.Pharmacy, &plat, &plon); err != nil {
			writeErr(w, err)
			return
		}
		s.DistanceMi = math.Round(haversineMiles(lat, lon, plat, plon)*100) / 100
		if s.UnitPrice > maxPrice {
			maxPrice = s.UnitPrice
		}
		subs = append(subs, s)
	}
	for i := range subs {
		if maxPrice > 0 {
			subs[i].SavingPct = math.Round((1-subs[i].UnitPrice/maxPrice)*1000) / 10
		}
	}
	writeJSON(w, 200, map[string]any{"molecule": d.Ingredient, "rxcui": rxcui, "strength_mg": strength,
		"basis": "Same active ingredient (RxNorm ingredient), same strength and form family, marked bioequivalent in the formulary.", "options": subs})
}

type RefillRisk struct {
	PatientID     string     `json:"patient_id"`
	PatientName   string     `json:"patient_name"`
	RxCUI         string     `json:"rxcui"`
	Drug          string     `json:"drug"`
	Fills         int        `json:"fills"`
	PDC           float64    `json:"pdc"`
	AvgLateDays   float64    `json:"avg_late_days"`
	RunOutDate    time.Time  `json:"run_out_date"`
	DaysUntilOut  int        `json:"days_until_out"`
	Risk          float64    `json:"risk"`
	RiskBand      string     `json:"risk_band"`
	LastFill      time.Time  `json:"last_fill"`
	NextReminder  *time.Time `json:"next_reminder,omitempty"`
}

// adherenceRisk is a logistic model over refill velocity features:
// proportion of days covered (PDC) over 180 days, mean refill lateness, and days until run-out.
func adherenceRisk(pdc, avgLate float64, daysUntilOut int) float64 {
	z := -1.2 + 4.0*(0.8-pdc) + 0.12*avgLate
	if daysUntilOut < 0 {
		z += 1.5
	} else if daysUntilOut <= 3 {
		z += 0.6
	}
	return 1 / (1 + math.Exp(-z))
}

func computeRefillRisk(fills []struct {
	at   time.Time
	days int
}, now time.Time) (pdc, avgLate float64, runOut time.Time) {
	sort.Slice(fills, func(i, j int) bool { return fills[i].at.Before(fills[j].at) })
	windowStart := now.AddDate(0, 0, -180)
	covered := map[string]bool{}
	var lateSum float64
	for i, f := range fills {
		for d := 0; d < f.days; d++ {
			day := f.at.AddDate(0, 0, d)
			if !day.Before(windowStart) && !day.After(now) {
				covered[day.Format("2006-01-02")] = true
			}
		}
		if i > 0 {
			expected := fills[i-1].at.AddDate(0, 0, fills[i-1].days)
			if late := f.at.Sub(expected).Hours() / 24; late > 0 {
				lateSum += late
			}
		}
	}
	first := fills[0].at
	if first.Before(windowStart) {
		first = windowStart
	}
	span := now.Sub(first).Hours() / 24
	if span < 1 {
		span = 1
	}
	pdc = math.Min(1, float64(len(covered))/span)
	if len(fills) > 1 {
		avgLate = lateSum / float64(len(fills)-1)
	}
	last := fills[len(fills)-1]
	runOut = last.at.AddDate(0, 0, last.days)
	return
}

func (a *App) refillRisks(ctx context.Context, p *Principal) ([]RefillRisk, error) {
	rows, err := a.db.Query(ctx, `SELECT h.patient_id, h.rxcui, d.ingredient, h.filled_at, h.days_supply FROM clinical.refill_history h
		JOIN clinical.drugs d USING (rxcui) JOIN clinical.patients pt ON pt.id=h.patient_id WHERE pt.facility_id=$1 ORDER BY h.patient_id, h.rxcui, h.filled_at`, p.Facility())
	if err != nil {
		return nil, err
	}
	type key struct{ pid, rx string }
	type fill = struct {
		at   time.Time
		days int
	}
	groups := map[key][]fill{}
	names := map[key]string{}
	order := []key{}
	for rows.Next() {
		var k key
		var drug string
		var f fill
		if err := rows.Scan(&k.pid, &k.rx, &drug, &f.at, &f.days); err != nil {
			rows.Close()
			return nil, err
		}
		if _, ok := groups[k]; !ok {
			order = append(order, k)
		}
		groups[k] = append(groups[k], f)
		names[k] = drug
	}
	rows.Close()
	now := time.Now()
	out := []RefillRisk{}
	for _, k := range order {
		fills := groups[k]
		pdc, late, runOut := computeRefillRisk(fills, now)
		daysOut := int(math.Floor(runOut.Sub(now).Hours() / 24))
		risk := adherenceRisk(pdc, late, daysOut)
		band := "low"
		if risk >= 0.6 {
			band = "high"
		} else if risk >= 0.35 {
			band = "medium"
		}
		pt, err := a.patientByID(ctx, k.pid)
		if err != nil {
			return nil, err
		}
		out = append(out, RefillRisk{PatientID: k.pid, PatientName: pt.Name, RxCUI: k.rx, Drug: names[k], Fills: len(fills), PDC: math.Round(pdc*100) / 100,
			AvgLateDays: math.Round(late*10) / 10, RunOutDate: runOut, DaysUntilOut: daysOut, Risk: math.Round(risk*100) / 100, RiskBand: band, LastFill: fills[len(fills)-1].at})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Risk > out[j].Risk })
	return out, nil
}

func (a *App) refillRisk(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	if err := requireRole(p, "pharmacist"); err != nil {
		writeErr(w, err)
		return
	}
	risks, err := a.refillRisks(r.Context(), p)
	if err != nil {
		writeErr(w, err)
		return
	}
	a.audit(r.Context(), AuditEntry{Actor: p.Username, Role: p.Role, Action: "phi.list", Resource: "RefillRisk", Facility: p.Facility(), Outcome: "allowed"})
	writeJSON(w, 200, risks)
}

func maskPhone(s string) string {
	if len(s) < 4 {
		return "****"
	}
	return "******" + s[len(s)-4:]
}

// runReminders queues SMS and WhatsApp reminders for high-risk or soon-to-run-out refills.
// Messages go to the outbox; a provider adapter would deliver them.
func (a *App) runReminders(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	if err := requireRole(p, "pharmacist"); err != nil {
		writeErr(w, err)
		return
	}
	ctx := r.Context()
	risks, err := a.refillRisks(ctx, p)
	if err != nil {
		writeErr(w, err)
		return
	}
	queued := 0
	for _, rr := range risks {
		if rr.RiskBand != "high" && rr.DaysUntilOut > 3 {
			continue
		}
		var recent bool
		_ = a.db.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM clinical.outbox WHERE patient_id=$1 AND message LIKE $2 AND created_at > now() - interval '1 day')`, rr.PatientID, "%"+rr.Drug+"%").Scan(&recent)
		if recent {
			continue
		}
		pt, err := a.patientByID(ctx, rr.PatientID)
		if err != nil {
			writeErr(w, err)
			return
		}
		var msg string
		if rr.DaysUntilOut < 0 {
			msg = fmt.Sprintf("Hello %s, your %s ran out %d day(s) ago. Please collect your refill or reply HELP to talk to our pharmacist.", pt.Name, rr.Drug, -rr.DaysUntilOut)
		} else {
			msg = fmt.Sprintf("Hello %s, your %s will run out in %d day(s). Reply REFILL to have it ready for pickup.", pt.Name, rr.Drug, rr.DaysUntilOut)
		}
		for _, ch := range []string{"sms", "whatsapp"} {
			if _, err := a.db.Exec(ctx, `INSERT INTO clinical.outbox(patient_id, channel, destination, message) VALUES ($1,$2,$3,$4)`, rr.PatientID, ch, maskPhone(pt.Phone), msg); err != nil {
				writeErr(w, err)
				return
			}
			queued++
		}
	}
	a.audit(ctx, AuditEntry{Actor: p.Username, Role: p.Role, Action: "reminders.run", Resource: "Outbox", Facility: p.Facility(), Outcome: "allowed", Detail: map[string]any{"queued": queued}})
	writeJSON(w, 200, map[string]any{"queued": queued})
}

func (a *App) listOutbox(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	ctx := r.Context()
	pid := r.URL.Query().Get("patient_id")
	if p.Is("patient") {
		pid = derefOr(p.Staff.PatientID, "")
	} else if err := requireRole(p, "pharmacist", "doctor"); err != nil {
		writeErr(w, err)
		return
	}
	rows, err := a.db.Query(ctx, `SELECT o.id, coalesce(o.patient_id,''), o.channel, coalesce(o.destination,''), o.message, o.status, o.created_at FROM clinical.outbox o
		LEFT JOIN clinical.patients pt ON pt.id=o.patient_id WHERE ($1='' OR o.patient_id=$1) AND ($2='' OR pt.facility_id=$2) ORDER BY o.created_at DESC LIMIT 200`, pid, map[bool]string{true: "", false: p.Facility()}[p.Is("patient")])
	if err != nil {
		writeErr(w, err)
		return
	}
	defer rows.Close()
	out := []map[string]any{}
	for rows.Next() {
		var id int
		var pat, ch, dest, msg, st string
		var at time.Time
		if err := rows.Scan(&id, &pat, &ch, &dest, &msg, &st, &at); err != nil {
			writeErr(w, err)
			return
		}
		out = append(out, map[string]any{"id": id, "patient_id": pat, "channel": ch, "destination": dest, "message": msg, "status": st, "created_at": at})
	}
	writeJSON(w, 200, out)
}
