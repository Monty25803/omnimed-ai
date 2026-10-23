package main

import (
	"context"
	"encoding/json"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/jackc/pgx/v5"
)

type VitalsInput struct {
	HR   *int     `json:"hr"`
	SBP  *int     `json:"sbp"`
	DBP  *int     `json:"dbp"`
	RR   *int     `json:"rr"`
	Temp *float64 `json:"temp"`
	SpO2 *int     `json:"spo2"`
	GCS  *int     `json:"gcs"`
}

type Score struct {
	MEWS       int            `json:"mews"`
	QSOFA      int            `json:"qsofa"`
	RedAlert   bool           `json:"red_alert"`
	Components map[string]int `json:"components"`
}

// avpuFromGCS approximates the AVPU level MEWS expects from a GCS total.
func avpuFromGCS(gcs int) int {
	switch {
	case gcs >= 15:
		return 0
	case gcs >= 13:
		return 1
	case gcs >= 9:
		return 2
	default:
		return 3
	}
}

// ScoreVitals computes MEWS (Subbe et al.) and qSOFA (Sepsis-3).
func ScoreVitals(v VitalsInput) Score {
	c := map[string]int{}
	if v.RR != nil {
		rr := *v.RR
		switch {
		case rr < 9:
			c["rr"] = 2
		case rr <= 14:
			c["rr"] = 0
		case rr <= 20:
			c["rr"] = 1
		case rr <= 29:
			c["rr"] = 2
		default:
			c["rr"] = 3
		}
	}
	if v.HR != nil {
		hr := *v.HR
		switch {
		case hr <= 40:
			c["hr"] = 2
		case hr <= 50:
			c["hr"] = 1
		case hr <= 100:
			c["hr"] = 0
		case hr <= 110:
			c["hr"] = 1
		case hr <= 129:
			c["hr"] = 2
		default:
			c["hr"] = 3
		}
	}
	if v.SBP != nil {
		s := *v.SBP
		switch {
		case s <= 70:
			c["sbp"] = 3
		case s <= 80:
			c["sbp"] = 2
		case s <= 100:
			c["sbp"] = 1
		case s <= 199:
			c["sbp"] = 0
		default:
			c["sbp"] = 2
		}
	}
	if v.Temp != nil {
		t := *v.Temp
		switch {
		case t < 35:
			c["temp"] = 2
		case t < 38.5:
			c["temp"] = 0
		default:
			c["temp"] = 2
		}
	}
	if v.GCS != nil {
		c["avpu"] = avpuFromGCS(*v.GCS)
	}
	mews := 0
	for _, s := range c {
		mews += s
	}
	q := 0
	if v.RR != nil && *v.RR >= 22 {
		q++
	}
	if v.SBP != nil && *v.SBP <= 100 {
		q++
	}
	if v.GCS != nil && *v.GCS < 15 {
		q++
	}
	return Score{MEWS: mews, QSOFA: q, RedAlert: mews >= 5 || q >= 2, Components: c}
}

type VitalsRow struct {
	VitalsInput
	Score
	RecordedAt time.Time `json:"recorded_at"`
	RecordedBy *string   `json:"recorded_by"`
}

func (a *App) latestVitals(ctx context.Context, patientID string) (*VitalsRow, error) {
	v := &VitalsRow{}
	err := a.db.QueryRow(ctx, `SELECT hr, sbp, dbp, rr, temp, spo2, gcs, mews, qsofa, red_alert, recorded_at, recorded_by
		FROM clinical.vitals WHERE patient_id=$1 ORDER BY recorded_at DESC LIMIT 1`, patientID).
		Scan(&v.HR, &v.SBP, &v.DBP, &v.RR, &v.Temp, &v.SpO2, &v.GCS, &v.MEWS, &v.QSOFA, &v.RedAlert, &v.RecordedAt, &v.RecordedBy)
	if err == pgx.ErrNoRows {
		return nil, nil
	}
	return v, err
}

var vitalLOINC = []struct {
	code, display, unit string
	get                 func(v VitalsInput) *float64
}{
	{"8867-4", "Heart rate", "/min", func(v VitalsInput) *float64 { return intPtrF(v.HR) }},
	{"8480-6", "Systolic blood pressure", "mm[Hg]", func(v VitalsInput) *float64 { return intPtrF(v.SBP) }},
	{"8462-4", "Diastolic blood pressure", "mm[Hg]", func(v VitalsInput) *float64 { return intPtrF(v.DBP) }},
	{"9279-1", "Respiratory rate", "/min", func(v VitalsInput) *float64 { return intPtrF(v.RR) }},
	{"8310-5", "Body temperature", "Cel", func(v VitalsInput) *float64 { return v.Temp }},
	{"59408-5", "Oxygen saturation", "%", func(v VitalsInput) *float64 { return intPtrF(v.SpO2) }},
	{"9269-2", "Glasgow coma score total", "{score}", func(v VitalsInput) *float64 { return intPtrF(v.GCS) }},
}

func intPtrF(i *int) *float64 {
	if i == nil {
		return nil
	}
	f := float64(*i)
	return &f
}

func validateVitals(v VitalsInput) error {
	check := func(name string, val *int, lo, hi int) error {
		if val != nil && (*val < lo || *val > hi) {
			return errf(422, name+" is outside the physiologically possible range")
		}
		return nil
	}
	for _, e := range []error{check("hr", v.HR, 10, 300), check("sbp", v.SBP, 30, 300), check("dbp", v.DBP, 10, 200),
		check("rr", v.RR, 2, 80), check("spo2", v.SpO2, 40, 100), check("gcs", v.GCS, 3, 15)} {
		if e != nil {
			return e
		}
	}
	if v.Temp != nil && (*v.Temp < 25 || *v.Temp > 45) {
		return errf(422, "temp is outside the physiologically possible range")
	}
	return nil
}

func (a *App) recordVitals(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	id := chi.URLParam(r, "id")
	ctx := r.Context()
	if err := requireRole(p, "nurse", "doctor"); err != nil {
		writeErr(w, err)
		return
	}
	if err := a.authorizePatient(ctx, p, id, "write"); err != nil {
		writeErr(w, err)
		return
	}
	var v VitalsInput
	if err := decode(r, &v); err != nil {
		writeErr(w, err)
		return
	}
	if err := validateVitals(v); err != nil {
		writeErr(w, err)
		return
	}
	s := ScoreVitals(v)
	var vid int
	err := a.db.QueryRow(ctx, `INSERT INTO clinical.vitals(patient_id, hr, sbp, dbp, rr, temp, spo2, gcs, mews, qsofa, red_alert, recorded_by)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) RETURNING id`, id, v.HR, v.SBP, v.DBP, v.RR, v.Temp, v.SpO2, v.GCS, s.MEWS, s.QSOFA, s.RedAlert, p.Username).Scan(&vid)
	if err != nil {
		writeErr(w, err)
		return
	}
	for _, l := range vitalLOINC {
		if val := l.get(v); val != nil {
			_, _ = a.db.Exec(ctx, `INSERT INTO clinical.observations(patient_id, loinc, display, value, unit, source) VALUES ($1,$2,$3,$4,$5,'vitals')`, id, l.code, l.display, *val, l.unit)
		}
	}
	a.audit(ctx, AuditEntry{Actor: p.Username, Role: p.Role, Action: "vitals.record", Resource: "Observation", ResourceID: itoa(vid), Facility: p.Facility(), Outcome: "allowed", Detail: map[string]any{"patient": id, "mews": s.MEWS, "qsofa": s.QSOFA}})
	if s.RedAlert {
		evt, _ := json.Marshal(map[string]any{"patient_id": id, "mews": s.MEWS, "qsofa": s.QSOFA, "vitals": v, "recorded_by": p.Username, "at": time.Now().UTC()})
		if _, err := a.js.Publish("ward.redalert", evt); err != nil {
			writeErr(w, err)
			return
		}
	}
	writeJSON(w, 201, map[string]any{"id": vid, "score": s})
}

type Trend struct {
	SpO2Falling bool `json:"spo2_falling"`
	HRRising    bool `json:"hr_rising"`
	SBPFalling  bool `json:"sbp_falling"`
}

func (a *App) wardGrid(w http.ResponseWriter, r *http.Request) {
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
	var pts []*PatientSummary
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
	out := []map[string]any{}
	for _, pt := range pts {
		hist := []VitalsRow{}
		vr, err := a.db.Query(ctx, `SELECT hr, sbp, spo2, mews, qsofa, red_alert, recorded_at FROM clinical.vitals WHERE patient_id=$1 ORDER BY recorded_at DESC LIMIT 3`, pt.ID)
		if err != nil {
			writeErr(w, err)
			return
		}
		for vr.Next() {
			var v VitalsRow
			if err := vr.Scan(&v.HR, &v.SBP, &v.SpO2, &v.MEWS, &v.QSOFA, &v.RedAlert, &v.RecordedAt); err == nil {
				hist = append(hist, v)
			}
		}
		vr.Close()
		latest, _ := a.latestVitals(ctx, pt.ID)
		var tr Trend
		if len(hist) >= 2 {
			newest, oldest := hist[0], hist[len(hist)-1]
			if newest.SpO2 != nil && oldest.SpO2 != nil && *oldest.SpO2-*newest.SpO2 >= 3 {
				tr.SpO2Falling = true
			}
			if newest.HR != nil && oldest.HR != nil && *newest.HR-*oldest.HR >= 15 {
				tr.HRRising = true
			}
			if newest.SBP != nil && oldest.SBP != nil && *oldest.SBP-*newest.SBP >= 20 {
				tr.SBPFalling = true
			}
		}
		var openTasks, openAlerts int
		_ = a.db.QueryRow(ctx, `SELECT count(*) FROM clinical.tasks WHERE patient_id=$1 AND status='open'`, pt.ID).Scan(&openTasks)
		_ = a.db.QueryRow(ctx, `SELECT count(*) FROM clinical.alerts WHERE patient_id=$1 AND status='open'`, pt.ID).Scan(&openAlerts)
		out = append(out, map[string]any{"patient": pt, "latest": latest, "trend": tr, "open_tasks": openTasks, "open_alerts": openAlerts})
	}
	a.audit(ctx, AuditEntry{Actor: p.Username, Role: p.Role, Action: "phi.list", Resource: "WardGrid", ResourceID: ward, Facility: p.Facility(), Outcome: "allowed"})
	writeJSON(w, 200, map[string]any{"ward": ward, "patients": out})
}

func (a *App) listTasks(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	if err := requireRole(p, "nurse", "doctor"); err != nil {
		writeErr(w, err)
		return
	}
	status := r.URL.Query().Get("status")
	if status == "" {
		status = "open"
	}
	rows, err := a.db.Query(r.Context(), `SELECT id, coalesce(patient_id,''), kind, coalesce(detail,''), coalesce(ward,''), status, created_at, coalesce(done_by,'')
		FROM clinical.tasks WHERE facility_id=$1 AND ($2='all' OR status=$2) ORDER BY created_at DESC LIMIT 100`, p.Facility(), status)
	if err != nil {
		writeErr(w, err)
		return
	}
	defer rows.Close()
	out := []map[string]any{}
	for rows.Next() {
		var id int
		var pid, kind, detail, ward, st, by string
		var at time.Time
		if err := rows.Scan(&id, &pid, &kind, &detail, &ward, &st, &at, &by); err != nil {
			writeErr(w, err)
			return
		}
		out = append(out, map[string]any{"id": id, "patient_id": pid, "kind": kind, "detail": detail, "ward": ward, "status": st, "created_at": at, "done_by": by})
	}
	writeJSON(w, 200, out)
}

func (a *App) completeTask(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	if err := requireRole(p, "nurse", "doctor"); err != nil {
		writeErr(w, err)
		return
	}
	id := chi.URLParam(r, "id")
	var pid string
	err := a.db.QueryRow(r.Context(), `UPDATE clinical.tasks SET status='done', done_by=$2, done_at=now() WHERE id=$1 AND facility_id=$3 AND status='open' RETURNING coalesce(patient_id,'')`, id, p.Username, p.Facility()).Scan(&pid)
	if err == pgx.ErrNoRows {
		writeErr(w, errf(404, "open task not found"))
		return
	}
	if err != nil {
		writeErr(w, err)
		return
	}
	a.audit(r.Context(), AuditEntry{Actor: p.Username, Role: p.Role, Action: "task.complete", Resource: "Task", ResourceID: id, Facility: p.Facility(), Outcome: "allowed", Detail: map[string]any{"patient": pid}})
	writeJSON(w, 200, map[string]any{"id": id, "status": "done"})
}
