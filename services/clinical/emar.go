package main

import (
	"math"
	"net/http"
	"sort"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
)

const adminWindow = 60 * time.Minute

func ist() *time.Location {
	loc, err := time.LoadLocation("Asia/Kolkata")
	if err != nil {
		return time.UTC
	}
	return loc
}

// scheduledToday returns today's dose times (IST) for a frequency code.
func scheduledToday(freq string, now time.Time) []time.Time {
	loc := ist()
	day := now.In(loc)
	out := []time.Time{}
	for _, hhmm := range freqTimes[freq] {
		t, _ := time.ParseInLocation("2006-01-02 15:04", day.Format("2006-01-02")+" "+hhmm, loc)
		out = append(out, t)
	}
	return out
}

type DoseRow struct {
	MedRequestID int        `json:"med_request_id"`
	PatientID    string     `json:"patient_id"`
	PatientName  string     `json:"patient_name"`
	MRN          string     `json:"mrn"`
	Bed          string     `json:"bed"`
	Drug         string     `json:"drug"`
	StrengthMg   float64    `json:"strength_mg"`
	Route        string     `json:"route"`
	ScheduledAt  time.Time  `json:"scheduled_at"`
	Status       string     `json:"status"`
	AdminAt      *time.Time `json:"administered_at"`
	AdminBy      *string    `json:"administered_by"`
	HoldReason   *string    `json:"hold_reason"`
}

func (a *App) emarDue(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	ctx := r.Context()
	if err := requireRole(p, "nurse", "doctor"); err != nil {
		writeErr(w, err)
		return
	}
	ward := r.URL.Query().Get("ward")
	if ward == "" {
		ward = derefOr(p.Staff.Ward, "W3")
	}
	rows, err := a.db.Query(ctx, `SELECT `+patientCols+` FROM clinical.patients WHERE facility_id=$1 AND ward=$2 ORDER BY bed`, p.Facility(), ward)
	if err != nil {
		writeErr(w, err)
		return
	}
	pts := []*PatientSummary{}
	for rows.Next() {
		ps, err := a.scanPatient(rows)
		if err != nil {
			rows.Close()
			writeErr(w, err)
			return
		}
		pts = append(pts, ps)
	}
	rows.Close()
	now := time.Now()
	out := []DoseRow{}
	for _, pt := range pts {
		meds, err := a.medications(ctx, "patient_id=$1 AND status IN ('active','on-hold')", pt.ID)
		if err != nil {
			writeErr(w, err)
			return
		}
		for _, m := range meds {
			for _, t := range scheduledToday(m.Frequency, now) {
				d := DoseRow{MedRequestID: m.ID, PatientID: pt.ID, PatientName: pt.Name, MRN: pt.MRN, Bed: derefOr(pt.Bed, ""), Drug: m.Drug,
					StrengthMg: m.StrengthMg, Route: m.Route, ScheduledAt: t, HoldReason: m.HoldReason}
				var st string
				err := a.db.QueryRow(ctx, `SELECT status, administered_at, administered_by FROM clinical.emar WHERE med_request_id=$1 AND scheduled_at=$2`, m.ID, t).Scan(&st, &d.AdminAt, &d.AdminBy)
				switch {
				case err == nil:
					d.Status = st
				case err != pgx.ErrNoRows:
					writeErr(w, err)
					return
				case m.Status == "on-hold":
					d.Status = "held"
				case now.After(t.Add(adminWindow)):
					d.Status = "late"
				case now.After(t.Add(-adminWindow)):
					d.Status = "due"
				default:
					d.Status = "upcoming"
				}
				out = append(out, d)
			}
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ScheduledAt.Before(out[j].ScheduledAt) })
	writeJSON(w, 200, out)
}

// emarAdminister enforces the two-barcode check: patient wristband (MRN) and medication package.
func (a *App) emarAdminister(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	ctx := r.Context()
	if err := requireRole(p, "nurse"); err != nil {
		writeErr(w, err)
		return
	}
	var req struct {
		Wristband      string `json:"wristband"`
		MedBarcode     string `json:"med_barcode"`
		OverrideReason string `json:"override_reason"`
	}
	if err := decode(r, &req); err != nil {
		writeErr(w, err)
		return
	}
	var patientID string
	if err := a.db.QueryRow(ctx, `SELECT id FROM clinical.patients WHERE mrn=$1`, strings.TrimSpace(req.Wristband)).Scan(&patientID); err != nil {
		writeErr(w, errf(404, "wristband does not match any admitted patient"))
		return
	}
	if err := a.authorizePatient(ctx, p, patientID, "administer"); err != nil {
		writeErr(w, err)
		return
	}
	var rxcui, brand string
	var strength float64
	if err := a.db.QueryRow(ctx, `SELECT rxcui, strength_mg, brand FROM clinical.inventory WHERE barcode=$1`, strings.TrimSpace(req.MedBarcode)).Scan(&rxcui, &strength, &brand); err != nil {
		writeErr(w, errf(404, "medication barcode not recognised"))
		return
	}
	meds, err := a.medications(ctx, "patient_id=$1 AND rxcui=$2 AND status IN ('active','on-hold')", patientID, rxcui)
	if err != nil {
		writeErr(w, err)
		return
	}
	reject := func(status int, reason string) {
		a.audit(ctx, AuditEntry{Actor: p.Username, Role: p.Role, Action: "emar.administer", Resource: "MedicationAdministration", Facility: p.Facility(), Outcome: "blocked",
			Detail: map[string]any{"patient": patientID, "barcode": req.MedBarcode, "reason": reason}})
		writeErr(w, errf(status, reason))
	}
	if len(meds) == 0 {
		reject(409, "WRONG MEDICATION: "+brand+" is not prescribed for this patient")
		return
	}
	m := meds[0]
	if m.StrengthMg != strength {
		reject(409, "WRONG STRENGTH: package is "+trimFloat(strength)+" mg, order is "+trimFloat(m.StrengthMg)+" mg")
		return
	}
	if m.Status == "on-hold" {
		reject(409, "ORDER ON HOLD: "+derefOr(m.HoldReason, ""))
		return
	}
	now := time.Now()
	slots := scheduledToday(m.Frequency, now)
	if len(slots) == 0 {
		slots = []time.Time{now.Truncate(time.Minute)}
	}
	best := slots[0]
	for _, t := range slots {
		if math.Abs(now.Sub(t).Minutes()) < math.Abs(now.Sub(best).Minutes()) {
			best = t
		}
	}
	var already bool
	_ = a.db.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM clinical.emar WHERE med_request_id=$1 AND scheduled_at=$2 AND status='given')`, m.ID, best).Scan(&already)
	if already {
		reject(409, "DUPLICATE DOSE: the "+best.In(ist()).Format("15:04")+" dose was already given")
		return
	}
	outside := math.Abs(now.Sub(best).Minutes()) > adminWindow.Minutes()
	if outside && strings.TrimSpace(req.OverrideReason) == "" {
		reject(409, "OUTSIDE WINDOW: nearest scheduled dose is "+best.In(ist()).Format("15:04")+"; document an override reason to proceed")
		return
	}
	_, err = a.db.Exec(ctx, `INSERT INTO clinical.emar(med_request_id, scheduled_at, administered_at, administered_by, status, override_reason)
		VALUES ($1,$2,now(),$3,'given',nullif($4,''))`, m.ID, best, p.Username, req.OverrideReason)
	if err != nil {
		writeErr(w, err)
		return
	}
	a.audit(ctx, AuditEntry{Actor: p.Username, Role: p.Role, Action: "emar.administer", Resource: "MedicationAdministration", ResourceID: itoa(m.ID), Facility: p.Facility(), Outcome: "allowed",
		Detail: map[string]any{"patient": patientID, "scheduled": best, "override": req.OverrideReason}})
	writeJSON(w, 201, map[string]any{"status": "given", "patient_id": patientID, "drug": m.Drug, "strength_mg": m.StrengthMg, "scheduled_at": best, "override": outside})
}
