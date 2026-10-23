package main

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/http"
	"sort"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/jackc/pgx/v5"
)

type MedRequest struct {
	ID           int       `json:"id"`
	PatientID    string    `json:"patient_id"`
	RxCUI        string    `json:"rxcui"`
	Drug         string    `json:"drug"`
	StrengthMg   float64   `json:"strength_mg"`
	DoseUnits    int       `json:"dose_units"`
	Route        string    `json:"route"`
	Frequency    string    `json:"frequency"`
	DurationDays int       `json:"duration_days"`
	Status       string    `json:"status"`
	HoldReason   *string   `json:"hold_reason"`
	Prescriber   *string   `json:"prescriber"`
	FacilityID   *string   `json:"facility_id"`
	CreatedAt    time.Time `json:"created_at"`
	Dispensed    bool      `json:"dispensed"`
}

func (a *App) medications(ctx context.Context, where string, args ...any) ([]MedRequest, error) {
	rows, err := a.db.Query(ctx, `SELECT id, patient_id, rxcui, drug, strength_mg, dose_units, route, frequency, duration_days, status,
		hold_reason, prescriber, facility_id, created_at, EXISTS(SELECT 1 FROM clinical.dispenses d WHERE d.med_request_id=m.id)
		FROM clinical.medication_requests m WHERE `+where+` ORDER BY created_at DESC`, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []MedRequest{}
	for rows.Next() {
		var m MedRequest
		if err := rows.Scan(&m.ID, &m.PatientID, &m.RxCUI, &m.Drug, &m.StrengthMg, &m.DoseUnits, &m.Route, &m.Frequency, &m.DurationDays,
			&m.Status, &m.HoldReason, &m.Prescriber, &m.FacilityID, &m.CreatedAt, &m.Dispensed); err != nil {
			return nil, err
		}
		out = append(out, m)
	}
	return out, nil
}

var freqPerDay = map[string]int{"OD": 1, "BD": 2, "TDS": 3, "QID": 4, "HS": 1, "PRN": 1, "STAT": 1}

var freqTimes = map[string][]string{"OD": {"08:00"}, "BD": {"08:00", "20:00"}, "TDS": {"08:00", "14:00", "20:00"}, "QID": {"06:00", "12:00", "18:00", "22:00"}, "HS": {"22:00"}}

type Drug struct {
	RxCUI           string   `json:"rxcui"`
	Ingredient      string   `json:"ingredient"`
	Classes         []string `json:"classes"`
	MaxDailyMg      *float64 `json:"max_daily_mg"`
	RenalMinEGFR    *float64 `json:"renal_min_egfr"`
	RaisesPotassium bool     `json:"raises_potassium"`
}

func (a *App) drug(ctx context.Context, rxcui string) (*Drug, error) {
	d := &Drug{}
	err := a.db.QueryRow(ctx, `SELECT rxcui, ingredient, drug_class, max_daily_mg, renal_min_egfr, raises_potassium FROM clinical.drugs WHERE rxcui=$1`, rxcui).
		Scan(&d.RxCUI, &d.Ingredient, &d.Classes, &d.MaxDailyMg, &d.RenalMinEGFR, &d.RaisesPotassium)
	if err == pgx.ErrNoRows {
		return nil, errf(422, "RxCUI "+rxcui+" is not in the formulary")
	}
	return d, err
}

func (a *App) listDrugs(w http.ResponseWriter, r *http.Request) {
	rows, err := a.db.Query(r.Context(), `SELECT rxcui, ingredient, drug_class, max_daily_mg, renal_min_egfr, raises_potassium FROM clinical.drugs ORDER BY ingredient`)
	if err != nil {
		writeErr(w, err)
		return
	}
	defer rows.Close()
	out := []Drug{}
	for rows.Next() {
		var d Drug
		if err := rows.Scan(&d.RxCUI, &d.Ingredient, &d.Classes, &d.MaxDailyMg, &d.RenalMinEGFR, &d.RaisesPotassium); err != nil {
			writeErr(w, err)
			return
		}
		out = append(out, d)
	}
	writeJSON(w, 200, out)
}

type RxInput struct {
	PatientID      string  `json:"patient_id"`
	RxCUI          string  `json:"rxcui"`
	StrengthMg     float64 `json:"strength_mg"`
	DoseUnits      int     `json:"dose_units"`
	Route          string  `json:"route"`
	Frequency      string  `json:"frequency"`
	DurationDays   int     `json:"duration_days"`
	OverrideReason string  `json:"override_reason"`
}

type SafetyAlert struct {
	Severity string `json:"severity"` // hard | soft
	Kind     string `json:"kind"`
	Message  string `json:"message"`
}

func hasClass(classes []string, c string) bool {
	for _, x := range classes {
		if x == c {
			return true
		}
	}
	return false
}

// safetyCheck runs the allergy, interaction, renal, potassium, dose and duplicate rules.
func (a *App) safetyCheck(ctx context.Context, in RxInput) (*Drug, []SafetyAlert, error) {
	d, err := a.drug(ctx, in.RxCUI)
	if err != nil {
		return nil, nil, err
	}
	if _, ok := freqPerDay[in.Frequency]; !ok {
		return nil, nil, errf(422, "frequency must be one of OD, BD, TDS, QID, HS, PRN, STAT")
	}
	if in.DoseUnits <= 0 {
		in.DoseUnits = 1
	}
	alerts := []SafetyAlert{}

	al, err := a.allergies(ctx, in.PatientID)
	if err != nil {
		return nil, nil, err
	}
	for _, x := range al {
		cls := derefOr(x.DrugClass, "")
		if cls == "" {
			continue
		}
		if hasClass(d.Classes, cls) {
			alerts = append(alerts, SafetyAlert{"hard", "allergy", fmt.Sprintf("Documented %s allergy to %s (%s). %s belongs to this class.", x.Severity, x.Substance, derefOr(x.Reaction, "reaction not recorded"), d.Ingredient)})
		} else if cls == "penicillin" && hasClass(d.Classes, "cephalosporin") {
			alerts = append(alerts, SafetyAlert{"soft", "allergy", fmt.Sprintf("Penicillin allergy (%s): cephalosporin cross-reactivity is possible.", x.Severity)})
		}
	}

	active, err := a.medications(ctx, "patient_id=$1 AND status IN ('active','on-hold')", in.PatientID)
	if err != nil {
		return nil, nil, err
	}
	for _, m := range active {
		if m.RxCUI == d.RxCUI {
			alerts = append(alerts, SafetyAlert{"soft", "duplicate", fmt.Sprintf("Patient already has an active order for %s %s mg %s.", m.Drug, trimFloat(m.StrengthMg), m.Frequency)})
			continue
		}
		var sev, effect string
		err := a.db.QueryRow(ctx, `SELECT severity, effect FROM clinical.interactions WHERE (a=$1 AND b=$2) OR (a=$2 AND b=$1) LIMIT 1`, d.RxCUI, m.RxCUI).Scan(&sev, &effect)
		if err == nil {
			alerts = append(alerts, SafetyAlert{sev, "interaction", fmt.Sprintf("With active %s: %s", m.Drug, effect)})
		} else if err != pgx.ErrNoRows {
			return nil, nil, err
		}
	}

	labs, err := a.latestLabs(ctx, in.PatientID)
	if err != nil {
		return nil, nil, err
	}
	if egfr, ok := labs["33914-3"]; ok && egfr.Value != nil && d.RenalMinEGFR != nil && *egfr.Value < *d.RenalMinEGFR {
		alerts = append(alerts, SafetyAlert{"hard", "renal", fmt.Sprintf("Latest eGFR %s is below %s, the minimum for %s.", trimFloat(*egfr.Value), trimFloat(*d.RenalMinEGFR), d.Ingredient)})
	} else if ok && egfr.Value != nil && d.RenalMinEGFR != nil && *egfr.Value < *d.RenalMinEGFR+15 {
		alerts = append(alerts, SafetyAlert{"soft", "renal", fmt.Sprintf("Latest eGFR %s: consider renal dose adjustment for %s.", trimFloat(*egfr.Value), d.Ingredient)})
	}
	if k, ok := labs["2823-3"]; ok && k.Value != nil && d.RaisesPotassium {
		if *k.Value >= 5.5 {
			alerts = append(alerts, SafetyAlert{"hard", "potassium", fmt.Sprintf("Latest potassium %s mmol/L; %s raises potassium.", trimFloat(*k.Value), d.Ingredient)})
		} else if *k.Value > 5.0 {
			alerts = append(alerts, SafetyAlert{"soft", "potassium", fmt.Sprintf("Latest potassium %s mmol/L is high-normal; %s raises potassium.", trimFloat(*k.Value), d.Ingredient)})
		}
	}
	units := in.DoseUnits
	if units <= 0 {
		units = 1
	}
	daily := in.StrengthMg * float64(units) * float64(freqPerDay[in.Frequency])
	if d.MaxDailyMg != nil && daily > *d.MaxDailyMg {
		alerts = append(alerts, SafetyAlert{"hard", "dose", fmt.Sprintf("Daily dose %s mg exceeds the maximum %s mg for %s.", trimFloat(daily), trimFloat(*d.MaxDailyMg), d.Ingredient)})
	}
	return d, alerts, nil
}

type StockQuery struct {
	RxCUI       string  `json:"rxcui"`
	StrengthMg  float64 `json:"strength_mg"`
	Lat         float64 `json:"lat"`
	Lon         float64 `json:"lon"`
	RadiusMiles float64 `json:"radius_miles"`
}

type StockOption struct {
	PharmacyID  string  `json:"pharmacy_id"`
	Pharmacy    string  `json:"pharmacy"`
	DistanceMi  float64 `json:"distance_miles"`
	InventoryID int     `json:"inventory_id"`
	Brand       string  `json:"brand"`
	RxCUI       string  `json:"rxcui"`
	Ingredient  string  `json:"ingredient"`
	StrengthMg  float64 `json:"strength_mg"`
	Generic     bool    `json:"generic"`
	UnitPrice   float64 `json:"unit_price"`
	Qty         int     `json:"qty"`
}

type StockAnswer struct {
	RequestedInStock bool          `json:"requested_in_stock"`
	OnSiteInStock    bool          `json:"on_site_in_stock"`
	InRadius         []StockOption `json:"in_radius"`
	Bioequivalents   []StockOption `json:"bioequivalents"`
	SameClass        []StockOption `json:"same_class_alternatives"`
	NearestOutside   *StockOption  `json:"nearest_outside_radius"`
	RadiusMiles      float64       `json:"radius_miles"`
	AnsweredBy       string        `json:"answered_by"`
}

func haversineMiles(lat1, lon1, lat2, lon2 float64) float64 {
	const r = 3958.8
	toRad := func(d float64) float64 { return d * math.Pi / 180 }
	dLat, dLon := toRad(lat2-lat1), toRad(lon2-lon1)
	h := math.Sin(dLat/2)*math.Sin(dLat/2) + math.Cos(toRad(lat1))*math.Cos(toRad(lat2))*math.Sin(dLon/2)*math.Sin(dLon/2)
	return 2 * r * math.Asin(math.Sqrt(h))
}

func (a *App) stockLookup(ctx context.Context, q StockQuery) (StockAnswer, error) {
	ans := StockAnswer{RadiusMiles: q.RadiusMiles, InRadius: []StockOption{}, Bioequivalents: []StockOption{}, SameClass: []StockOption{}, AnsweredBy: "pharmacy.stock.query"}
	d, err := a.drug(ctx, q.RxCUI)
	if err != nil {
		return ans, err
	}
	rows, err := a.db.Query(ctx, `SELECT p.id, p.name, p.lat, p.lon, i.id, i.brand, i.rxcui, dr.ingredient, i.strength_mg, i.is_generic, i.unit_price::float8, i.qty, dr.drug_class
		FROM clinical.inventory i JOIN clinical.pharmacies p ON p.id=i.pharmacy_id JOIN clinical.drugs dr ON dr.rxcui=i.rxcui
		WHERE i.qty > 0 AND i.bioequivalence_ok AND (i.rxcui=$1 OR dr.drug_class && $2)`, q.RxCUI, narrowClasses(d.Classes))
	if err != nil {
		return ans, err
	}
	defer rows.Close()
	for rows.Next() {
		var o StockOption
		var lat, lon float64
		var classes []string
		if err := rows.Scan(&o.PharmacyID, &o.Pharmacy, &lat, &lon, &o.InventoryID, &o.Brand, &o.RxCUI, &o.Ingredient, &o.StrengthMg, &o.Generic, &o.UnitPrice, &o.Qty, &classes); err != nil {
			return ans, err
		}
		o.DistanceMi = math.Round(haversineMiles(q.Lat, q.Lon, lat, lon)*100) / 100
		sameMolecule := o.RxCUI == q.RxCUI && o.StrengthMg == q.StrengthMg
		sameTherapeuticClass := o.RxCUI != q.RxCUI && hasTherapeuticOverlap(narrowClasses(d.Classes), classes)
		if o.DistanceMi <= q.RadiusMiles {
			if sameMolecule {
				ans.RequestedInStock = true
				ans.InRadius = append(ans.InRadius, o)
				ans.Bioequivalents = append(ans.Bioequivalents, o)
			} else if sameTherapeuticClass {
				ans.SameClass = append(ans.SameClass, o)
			}
		} else if sameMolecule && (ans.NearestOutside == nil || o.DistanceMi < ans.NearestOutside.DistanceMi) {
			oc := o
			ans.NearestOutside = &oc
		}
	}
	byDistance := func(s []StockOption) {
		sort.Slice(s, func(i, j int) bool { return s[i].DistanceMi < s[j].DistanceMi })
	}
	byDistance(ans.InRadius)
	byDistance(ans.Bioequivalents)
	byDistance(ans.SameClass)
	for _, o := range ans.InRadius {
		if o.DistanceMi < 0.05 {
			ans.OnSiteInStock = true
		}
	}
	return ans, nil
}

var broadClasses = map[string]bool{"antibiotic": true, "antihypertensive": true, "analgesic": true, "diuretic": true, "antidiabetic": true, "antipyretic": true}

// narrowClasses drops umbrella classes so "antibiotic" never suggests a penicillin for a macrolide.
func narrowClasses(classes []string) []string {
	out := []string{}
	for _, c := range classes {
		if !broadClasses[c] {
			out = append(out, c)
		}
	}
	return out
}

func hasTherapeuticOverlap(a, b []string) bool {
	for _, x := range a {
		for _, y := range b {
			if x == y {
				return true
			}
		}
	}
	return false
}

func (a *App) prescriberLocation(ctx context.Context, p *Principal) (float64, float64) {
	var lat, lon float64
	_ = a.db.QueryRow(ctx, `SELECT lat, lon FROM clinical.facilities WHERE id=$1`, p.Facility()).Scan(&lat, &lon)
	return lat, lon
}

// queryStock asks the pharmacy network over the synapse bus (NATS request/reply).
func (a *App) queryStock(ctx context.Context, p *Principal, rxcui string, strength float64) (*StockAnswer, error) {
	lat, lon := a.prescriberLocation(ctx, p)
	body, _ := json.Marshal(StockQuery{RxCUI: rxcui, StrengthMg: strength, Lat: lat, Lon: lon, RadiusMiles: 5})
	msg, err := a.nc.RequestWithContext(ctx, "pharmacy.stock.query", body)
	if err != nil {
		return nil, err
	}
	var ans StockAnswer
	if err := json.Unmarshal(msg.Data, &ans); err != nil {
		return nil, err
	}
	return &ans, nil
}

func (a *App) checkPrescription(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	ctx := r.Context()
	if err := requireRole(p, "doctor", "pharmacist"); err != nil {
		writeErr(w, err)
		return
	}
	var in RxInput
	if err := decode(r, &in); err != nil {
		writeErr(w, err)
		return
	}
	if err := a.authorizePatient(ctx, p, in.PatientID, "read"); err != nil {
		writeErr(w, err)
		return
	}
	d, alerts, err := a.safetyCheck(ctx, in)
	if err != nil {
		writeErr(w, err)
		return
	}
	stock, err := a.queryStock(ctx, p, in.RxCUI, in.StrengthMg)
	if err != nil {
		writeErr(w, err)
		return
	}
	hard := false
	for _, al := range alerts {
		if al.Severity == "hard" {
			hard = true
		}
	}
	writeJSON(w, 200, map[string]any{"drug": d, "alerts": alerts, "blocking": hard, "stock": stock})
}

func (a *App) createPrescription(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	ctx := r.Context()
	if err := requireRole(p, "doctor"); err != nil {
		writeErr(w, err)
		return
	}
	var in RxInput
	if err := decode(r, &in); err != nil {
		writeErr(w, err)
		return
	}
	if err := a.authorizePatient(ctx, p, in.PatientID, "write"); err != nil {
		writeErr(w, err)
		return
	}
	if in.DoseUnits <= 0 {
		in.DoseUnits = 1
	}
	if in.DurationDays <= 0 {
		in.DurationDays = 7
	}
	if in.Route == "" {
		in.Route = "oral"
	}
	d, alerts, err := a.safetyCheck(ctx, in)
	if err != nil {
		writeErr(w, err)
		return
	}
	var hard []SafetyAlert
	for _, al := range alerts {
		if al.Severity == "hard" {
			hard = append(hard, al)
		}
	}
	if len(hard) > 0 && strings.TrimSpace(in.OverrideReason) == "" {
		a.audit(ctx, AuditEntry{Actor: p.Username, Role: p.Role, Action: "rx.create", Resource: "MedicationRequest", Facility: p.Facility(), Outcome: "blocked", Detail: map[string]any{"patient": in.PatientID, "rxcui": in.RxCUI, "alerts": hard}})
		writeJSON(w, 409, map[string]any{"error": "hard safety alerts require a documented override reason", "alerts": alerts})
		return
	}
	var id int
	err = a.db.QueryRow(ctx, `INSERT INTO clinical.medication_requests(patient_id, rxcui, drug, strength_mg, dose_units, route, frequency, duration_days, prescriber, facility_id, override_reason)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,nullif($11,'')) RETURNING id`, in.PatientID, d.RxCUI, d.Ingredient, in.StrengthMg, in.DoseUnits, in.Route, in.Frequency, in.DurationDays, p.Username, p.Facility(), in.OverrideReason).Scan(&id)
	if err != nil {
		writeErr(w, err)
		return
	}
	evt, _ := json.Marshal(map[string]any{"id": id, "patient_id": in.PatientID, "rxcui": d.RxCUI, "drug": d.Ingredient, "strength_mg": in.StrengthMg, "prescriber": p.Username, "at": time.Now().UTC()})
	_, _ = a.js.Publish("rx.created", evt)
	a.audit(ctx, AuditEntry{Actor: p.Username, Role: p.Role, Action: "rx.create", Resource: "MedicationRequest", ResourceID: itoa(id), Facility: p.Facility(), Outcome: "allowed",
		Detail: map[string]any{"patient": in.PatientID, "rxcui": d.RxCUI, "overridden": len(hard) > 0, "override_reason": in.OverrideReason}})
	writeJSON(w, 201, map[string]any{"id": id, "alerts": alerts})
}

func (a *App) listPrescriptions(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	ctx := r.Context()
	q := r.URL.Query()
	if pid := q.Get("patient_id"); pid != "" {
		if err := a.authorizePatient(ctx, p, pid, "read"); err != nil {
			writeErr(w, err)
			return
		}
		meds, err := a.medications(ctx, "patient_id=$1", pid)
		if err != nil {
			writeErr(w, err)
			return
		}
		writeJSON(w, 200, meds)
		return
	}
	if err := requireRole(p, "pharmacist", "doctor", "nurse"); err != nil {
		writeErr(w, err)
		return
	}
	meds, err := a.medications(ctx, "facility_id=$1 AND status IN ('active','on-hold')", p.Facility())
	if err != nil {
		writeErr(w, err)
		return
	}
	a.audit(ctx, AuditEntry{Actor: p.Username, Role: p.Role, Action: "phi.list", Resource: "MedicationRequest", Facility: p.Facility(), Outcome: "allowed", Detail: map[string]any{"count": len(meds)}})
	writeJSON(w, 200, meds)
}

func (a *App) medByID(ctx context.Context, id string) (*MedRequest, error) {
	meds, err := a.medications(ctx, "id=$1", id)
	if err != nil {
		return nil, err
	}
	if len(meds) == 0 {
		return nil, errf(404, "medication request not found")
	}
	return &meds[0], nil
}

func (a *App) holdPrescription(w http.ResponseWriter, r *http.Request) {
	a.setHold(w, r, true)
}

func (a *App) releasePrescription(w http.ResponseWriter, r *http.Request) {
	a.setHold(w, r, false)
}

// Pharmacists and doctors may hold; only a doctor may release a hold.
func (a *App) setHold(w http.ResponseWriter, r *http.Request, hold bool) {
	p := principalFrom(r.Context())
	ctx := r.Context()
	roles := []string{"doctor"}
	if hold {
		roles = append(roles, "pharmacist")
	}
	if err := requireRole(p, roles...); err != nil {
		writeErr(w, err)
		return
	}
	m, err := a.medByID(ctx, chi.URLParam(r, "id"))
	if err != nil {
		writeErr(w, err)
		return
	}
	if err := a.authorizePatient(ctx, p, m.PatientID, "write"); err != nil {
		writeErr(w, err)
		return
	}
	var req struct {
		Reason string `json:"reason"`
	}
	_ = decode(r, &req)
	if strings.TrimSpace(req.Reason) == "" {
		writeErr(w, errf(422, "a reason is required"))
		return
	}
	var status string
	var reason any
	if hold {
		status, reason = "on-hold", req.Reason+" ("+p.Username+")"
	} else {
		if m.Status != "on-hold" {
			writeErr(w, errf(409, "order is not on hold"))
			return
		}
		status, reason = "active", nil
	}
	if _, err := a.db.Exec(ctx, `UPDATE clinical.medication_requests SET status=$2, hold_reason=$3 WHERE id=$1`, m.ID, status, reason); err != nil {
		writeErr(w, err)
		return
	}
	a.audit(ctx, AuditEntry{Actor: p.Username, Role: p.Role, Action: map[bool]string{true: "rx.hold", false: "rx.release"}[hold], Resource: "MedicationRequest", ResourceID: itoa(m.ID), Facility: p.Facility(), Outcome: "allowed", Detail: map[string]any{"reason": req.Reason}})
	writeJSON(w, 200, map[string]any{"id": m.ID, "status": status})
}

func (a *App) dispensePrescription(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	ctx := r.Context()
	if err := requireRole(p, "pharmacist"); err != nil {
		writeErr(w, err)
		return
	}
	m, err := a.medByID(ctx, chi.URLParam(r, "id"))
	if err != nil {
		writeErr(w, err)
		return
	}
	if err := a.authorizePatient(ctx, p, m.PatientID, "dispense"); err != nil {
		writeErr(w, err)
		return
	}
	if m.Status == "on-hold" {
		writeErr(w, errf(409, "order is on hold: "+derefOr(m.HoldReason, "")))
		return
	}
	if m.Status != "active" {
		writeErr(w, errf(409, "order is "+m.Status))
		return
	}
	var req struct {
		InventoryID int `json:"inventory_id"`
	}
	_ = decode(r, &req)
	days := m.DurationDays
	if days > 30 {
		days = 30
	}
	qty := m.DoseUnits * freqPerDay[m.Frequency] * days
	if m.Frequency == "PRN" || m.Route == "inhaled" || strings.Contains(m.Drug, "albuterol") {
		qty = 1
	}
	tx, err := a.db.Begin(ctx)
	if err != nil {
		writeErr(w, err)
		return
	}
	defer tx.Rollback(ctx)
	var invID, stock int
	var brand string
	err = tx.QueryRow(ctx, `SELECT id, qty, brand FROM clinical.inventory WHERE pharmacy_id=$1 AND rxcui=$2 AND strength_mg=$3 AND ($4=0 OR id=$4)
		ORDER BY is_generic DESC, qty DESC LIMIT 1 FOR UPDATE`, derefOr(p.Staff.PharmacyID, ""), m.RxCUI, m.StrengthMg, req.InventoryID).Scan(&invID, &stock, &brand)
	if err == pgx.ErrNoRows || (err == nil && stock < qty) {
		ans, _ := a.queryStock(ctx, p, m.RxCUI, m.StrengthMg)
		writeJSON(w, 409, map[string]any{"error": fmt.Sprintf("insufficient stock for %s %s mg (need %d)", m.Drug, trimFloat(m.StrengthMg), qty), "network": ans})
		return
	}
	if err != nil {
		writeErr(w, err)
		return
	}
	if _, err := tx.Exec(ctx, `UPDATE clinical.inventory SET qty=qty-$2, updated_at=now() WHERE id=$1`, invID, qty); err != nil {
		writeErr(w, err)
		return
	}
	if _, err := tx.Exec(ctx, `INSERT INTO clinical.dispenses(med_request_id, inventory_id, qty, dispensed_by) VALUES ($1,$2,$3,$4)`, m.ID, invID, qty, p.Username); err != nil {
		writeErr(w, err)
		return
	}
	if _, err := tx.Exec(ctx, `INSERT INTO clinical.refill_history(patient_id, rxcui, filled_at, days_supply) VALUES ($1,$2,now(),$3)`, m.PatientID, m.RxCUI, days); err != nil {
		writeErr(w, err)
		return
	}
	if err := tx.Commit(ctx); err != nil {
		writeErr(w, err)
		return
	}
	a.audit(ctx, AuditEntry{Actor: p.Username, Role: p.Role, Action: "rx.dispense", Resource: "MedicationRequest", ResourceID: itoa(m.ID), Facility: p.Facility(), Outcome: "allowed", Detail: map[string]any{"inventory": invID, "qty": qty}})
	writeJSON(w, 200, map[string]any{"id": m.ID, "dispensed_qty": qty, "brand": brand, "remaining_stock": stock - qty, "low_stock": stock-qty <= 20})
}
