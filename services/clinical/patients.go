package main

import (
	"context"
	"net/http"
	"sort"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/jackc/pgx/v5"
)

type PatientSummary struct {
	ID          string    `json:"id"`
	MRN         string    `json:"mrn"`
	Name        string    `json:"name"`
	Phone       string    `json:"phone,omitempty"`
	ABHANumber  string    `json:"abha_number,omitempty"`
	ABHAAddress string    `json:"abha_address,omitempty"`
	BirthDate   time.Time `json:"birth_date"`
	Age         int       `json:"age"`
	Gender      string    `json:"gender"`
	FacilityID  string    `json:"facility_id"`
	Ward        *string   `json:"ward"`
	Bed         *string   `json:"bed"`
	Attending   *string   `json:"attending"`
	Lat         *float64  `json:"-"`
	Lon         *float64  `json:"-"`
}

func age(b time.Time) int {
	now := time.Now()
	y := now.Year() - b.Year()
	if now.YearDay() < b.YearDay() {
		y--
	}
	return y
}

const patientCols = `id, mrn, facility_id, birth_date, gender, ward, bed, attending, lat, lon, phi_enc, coalesce(phi_dek,'')`

func (a *App) scanPatient(row pgx.Row) (*PatientSummary, error) {
	p := &PatientSummary{}
	var blob []byte
	var dek string
	if err := row.Scan(&p.ID, &p.MRN, &p.FacilityID, &p.BirthDate, &p.Gender, &p.Ward, &p.Bed, &p.Attending, &p.Lat, &p.Lon, &blob, &dek); err != nil {
		return nil, err
	}
	phi, err := a.decryptPHI(blob, dek)
	if err != nil {
		return nil, err
	}
	p.Name, p.Phone, p.ABHANumber, p.ABHAAddress = phi.Name, phi.Phone, phi.ABHANumber, phi.ABHAAddress
	p.Age = age(p.BirthDate)
	return p, nil
}

func (a *App) patientByID(ctx context.Context, id string) (*PatientSummary, error) {
	p, err := a.scanPatient(a.db.QueryRow(ctx, `SELECT `+patientCols+` FROM clinical.patients WHERE id=$1`, id))
	if err == pgx.ErrNoRows {
		return nil, errf(404, "patient not found")
	}
	return p, err
}

func (a *App) listPatients(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	ctx := r.Context()
	var rows pgx.Rows
	var err error
	switch {
	case p.Is("patient"):
		rows, err = a.db.Query(ctx, `SELECT `+patientCols+` FROM clinical.patients WHERE id=$1`, derefOr(p.Staff.PatientID, ""))
	case p.Is("doctor", "nurse", "pharmacist", "lab"):
		rows, err = a.db.Query(ctx, `SELECT `+patientCols+` FROM clinical.patients pt WHERE facility_id=$1
			OR EXISTS (SELECT 1 FROM clinical.consents c WHERE c.patient_id=pt.id AND c.grantee_facility=$1 AND c.status='granted'
			AND (c.expires_at IS NULL OR c.expires_at > now())) ORDER BY id`, p.Facility())
	default:
		a.audit(ctx, AuditEntry{Actor: p.Username, Role: p.Role, Action: "phi.list", Resource: "Patient", Outcome: "denied"})
		writeErr(w, errf(403, "role "+p.Role+" has no access to patient lists"))
		return
	}
	if err != nil {
		writeErr(w, err)
		return
	}
	defer rows.Close()
	out := []*PatientSummary{}
	for rows.Next() {
		ps, err := a.scanPatient(rows)
		if err != nil {
			writeErr(w, err)
			return
		}
		out = append(out, ps)
	}
	a.audit(ctx, AuditEntry{Actor: p.Username, Role: p.Role, Action: "phi.list", Resource: "Patient", Facility: p.Facility(), Outcome: "allowed", Detail: map[string]any{"count": len(out)}})
	writeJSON(w, 200, out)
}

func derefOr(s *string, d string) string {
	if s == nil {
		return d
	}
	return *s
}

type Allergy struct {
	Substance string  `json:"substance"`
	DrugClass *string `json:"drug_class"`
	SNOMED    *string `json:"snomed"`
	Reaction  *string `json:"reaction"`
	Severity  string  `json:"severity"`
}

type Condition struct {
	ICD10   *string    `json:"icd10"`
	SNOMED  *string    `json:"snomed"`
	Display string     `json:"display"`
	Onset   *time.Time `json:"onset"`
	Status  string     `json:"status"`
}

func (a *App) allergies(ctx context.Context, patientID string) ([]Allergy, error) {
	rows, err := a.db.Query(ctx, `SELECT substance, drug_class, snomed, reaction, severity FROM clinical.allergies WHERE patient_id=$1`, patientID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Allergy{}
	for rows.Next() {
		var al Allergy
		if err := rows.Scan(&al.Substance, &al.DrugClass, &al.SNOMED, &al.Reaction, &al.Severity); err != nil {
			return nil, err
		}
		out = append(out, al)
	}
	return out, nil
}

func (a *App) conditions(ctx context.Context, patientID string) ([]Condition, error) {
	rows, err := a.db.Query(ctx, `SELECT icd10, snomed, display, onset::timestamptz, status FROM clinical.conditions WHERE patient_id=$1 ORDER BY onset DESC NULLS LAST`, patientID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []Condition{}
	for rows.Next() {
		var c Condition
		if err := rows.Scan(&c.ICD10, &c.SNOMED, &c.Display, &c.Onset, &c.Status); err != nil {
			return nil, err
		}
		out = append(out, c)
	}
	return out, nil
}

type Observation struct {
	LOINC     string    `json:"loinc"`
	Display   string    `json:"display"`
	Value     *float64  `json:"value"`
	Unit      *string   `json:"unit"`
	Flag      *string   `json:"flag"`
	Source    string    `json:"source"`
	Effective time.Time `json:"effective_at"`
}

func (a *App) latestLabs(ctx context.Context, patientID string) (map[string]Observation, error) {
	rows, err := a.db.Query(ctx, `SELECT DISTINCT ON (loinc) loinc, display, value, unit, flag, source, effective_at
		FROM clinical.observations WHERE patient_id=$1 ORDER BY loinc, effective_at DESC`, patientID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := map[string]Observation{}
	for rows.Next() {
		var o Observation
		if err := rows.Scan(&o.LOINC, &o.Display, &o.Value, &o.Unit, &o.Flag, &o.Source, &o.Effective); err != nil {
			return nil, err
		}
		out[o.LOINC] = o
	}
	return out, nil
}

func (a *App) getPatient(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	id := chi.URLParam(r, "id")
	ctx := r.Context()
	if err := a.authorizePatient(ctx, p, id, "read"); err != nil {
		writeErr(w, err)
		return
	}
	pt, err := a.patientByID(ctx, id)
	if err != nil {
		writeErr(w, err)
		return
	}
	al, err := a.allergies(ctx, id)
	if err != nil {
		writeErr(w, err)
		return
	}
	cond, err := a.conditions(ctx, id)
	if err != nil {
		writeErr(w, err)
		return
	}
	meds, err := a.medications(ctx, "patient_id=$1", id)
	if err != nil {
		writeErr(w, err)
		return
	}
	labs, err := a.latestLabs(ctx, id)
	if err != nil {
		writeErr(w, err)
		return
	}
	vit, _ := a.latestVitals(ctx, id)
	care := []map[string]string{}
	rows, err := a.db.Query(ctx, `SELECT c.username, c.member_role, s.display_name FROM clinical.care_team c JOIN clinical.staff s USING (username) WHERE c.patient_id=$1`, id)
	if err == nil {
		for rows.Next() {
			var u, role, name string
			if rows.Scan(&u, &role, &name) == nil {
				care = append(care, map[string]string{"username": u, "role": role, "name": name})
			}
		}
		rows.Close()
	}
	writeJSON(w, 200, map[string]any{"patient": pt, "allergies": al, "conditions": cond, "medications": meds, "latest_labs": labs, "latest_vitals": vit, "care_team": care})
}

type TimelineEvent struct {
	At       time.Time      `json:"at"`
	Kind     string         `json:"kind"`
	Title    string         `json:"title"`
	Detail   string         `json:"detail"`
	Facility string         `json:"facility,omitempty"`
	Flag     string         `json:"flag,omitempty"`
	Data     map[string]any `json:"data,omitempty"`
}

func (a *App) patientTimeline(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	id := chi.URLParam(r, "id")
	ctx := r.Context()
	if err := a.authorizePatient(ctx, p, id, "read"); err != nil {
		writeErr(w, err)
		return
	}
	events := []TimelineEvent{}
	add := func(q string, fn func(rows pgx.Rows) (TimelineEvent, error)) error {
		rows, err := a.db.Query(ctx, q, id)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			ev, err := fn(rows)
			if err != nil {
				return err
			}
			events = append(events, ev)
		}
		return nil
	}
	err := add(`SELECT started_at, kind, coalesce(reason,''), coalesce(facility_id,''), ended_at, note FROM clinical.encounters WHERE patient_id=$1`, func(rows pgx.Rows) (TimelineEvent, error) {
		var ev TimelineEvent
		var ended *time.Time
		var note map[string]any
		err := rows.Scan(&ev.At, &ev.Kind, &ev.Title, &ev.Facility, &ended, &note)
		ev.Kind = "encounter:" + ev.Kind
		if ended != nil {
			ev.Detail = "Ended " + ended.Format("02 Jan 2006")
		} else {
			ev.Detail = "Ongoing"
		}
		if note != nil {
			ev.Data = note
		}
		return ev, err
	})
	if err == nil {
		err = add(`SELECT effective_at, display, value, coalesce(unit,''), coalesce(flag,'') FROM clinical.observations WHERE patient_id=$1 AND source='lab'`, func(rows pgx.Rows) (TimelineEvent, error) {
			var ev TimelineEvent
			var v *float64
			var unit string
			err := rows.Scan(&ev.At, &ev.Title, &v, &unit, &ev.Flag)
			ev.Kind = "lab"
			if v != nil {
				ev.Detail = trimFloat(*v) + " " + unit
			}
			return ev, err
		})
	}
	if err == nil {
		err = add(`SELECT recorded_at, hr, sbp, dbp, rr, temp, spo2, gcs, mews, qsofa, red_alert FROM clinical.vitals WHERE patient_id=$1`, func(rows pgx.Rows) (TimelineEvent, error) {
			var ev TimelineEvent
			var hr, sbp, dbp, rr, spo2, gcs *int
			var temp *float64
			var mews, qsofa int
			var red bool
			err := rows.Scan(&ev.At, &hr, &sbp, &dbp, &rr, &temp, &spo2, &gcs, &mews, &qsofa, &red)
			ev.Kind = "vitals"
			ev.Title = "Vitals"
			ev.Detail = "MEWS " + itoa(mews) + " / qSOFA " + itoa(qsofa)
			if red {
				ev.Flag = "RED"
			}
			ev.Data = map[string]any{"hr": hr, "sbp": sbp, "dbp": dbp, "rr": rr, "temp": temp, "spo2": spo2, "gcs": gcs, "mews": mews, "qsofa": qsofa}
			return ev, err
		})
	}
	if err == nil {
		err = add(`SELECT created_at, drug, strength_mg, frequency, status, coalesce(hold_reason,'') FROM clinical.medication_requests WHERE patient_id=$1`, func(rows pgx.Rows) (TimelineEvent, error) {
			var ev TimelineEvent
			var drug, freq, status, hold string
			var strength float64
			err := rows.Scan(&ev.At, &drug, &strength, &freq, &status, &hold)
			ev.Kind = "medication"
			ev.Title = drug + " " + trimFloat(strength) + " mg " + freq
			ev.Detail = status
			if hold != "" {
				ev.Detail += ": " + hold
				ev.Flag = "HOLD"
			}
			return ev, err
		})
	}
	if err == nil {
		err = add(`SELECT onset::timestamptz, display, coalesce(icd10,'') FROM clinical.conditions WHERE patient_id=$1 AND onset IS NOT NULL`, func(rows pgx.Rows) (TimelineEvent, error) {
			var ev TimelineEvent
			var icd string
			err := rows.Scan(&ev.At, &ev.Title, &icd)
			ev.Kind = "diagnosis"
			ev.Detail = "ICD-10 " + icd
			return ev, err
		})
	}
	if err == nil {
		err = add(`SELECT created_at, coalesce(description,'Imaging study'), study_uid FROM diag.study_patients WHERE patient_id=$1`, func(rows pgx.Rows) (TimelineEvent, error) {
			var ev TimelineEvent
			var uid string
			err := rows.Scan(&ev.At, &ev.Title, &uid)
			ev.Kind = "imaging"
			ev.Detail = "DICOM study"
			ev.Data = map[string]any{"study_uid": uid}
			return ev, err
		})
	}
	if err != nil {
		writeErr(w, err)
		return
	}
	sort.Slice(events, func(i, j int) bool { return events[i].At.After(events[j].At) })

	trend := []map[string]any{}
	rows, err := a.db.Query(ctx, `SELECT recorded_at, hr, sbp, spo2, rr, temp, mews FROM clinical.vitals WHERE patient_id=$1 ORDER BY recorded_at`, id)
	if err == nil {
		for rows.Next() {
			var at time.Time
			var hr, sbp, spo2, rr *int
			var temp *float64
			var mews int
			if rows.Scan(&at, &hr, &sbp, &spo2, &rr, &temp, &mews) == nil {
				trend = append(trend, map[string]any{"at": at, "hr": hr, "sbp": sbp, "spo2": spo2, "rr": rr, "temp": temp, "mews": mews})
			}
		}
		rows.Close()
	}
	writeJSON(w, 200, map[string]any{"events": events, "vitals_trend": trend})
}

func (a *App) saveEncounterNote(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	id := chi.URLParam(r, "id")
	ctx := r.Context()
	if err := requireRole(p, "doctor"); err != nil {
		writeErr(w, err)
		return
	}
	if err := a.authorizePatient(ctx, p, id, "write"); err != nil {
		writeErr(w, err)
		return
	}
	var req struct {
		Reason string         `json:"reason"`
		Note   map[string]any `json:"note"`
	}
	if err := decode(r, &req); err != nil {
		writeErr(w, err)
		return
	}
	accepted, rejected, err := a.groundNote(ctx, r.Header.Get("Authorization"), req.Note)
	if err != nil {
		writeErr(w, errf(503, "grounding engine unavailable; note not saved"))
		return
	}
	if !accepted {
		a.audit(ctx, AuditEntry{Actor: p.Username, Role: p.Role, Action: "encounter.create", Resource: "Encounter", Facility: p.Facility(), Outcome: "blocked",
			Detail: map[string]any{"patient": id, "rejected": rejected}})
		writeJSON(w, 422, map[string]any{"error": "only notes that pass the grounding engine can be saved", "rejected": rejected})
		return
	}
	var encID int
	err = a.db.QueryRow(ctx, `INSERT INTO clinical.encounters(patient_id, kind, facility_id, practitioner, reason, started_at, ended_at, note)
		VALUES ($1,'outpatient',$2,$3,$4,now(),now(),$5) RETURNING id`, id, p.Facility(), p.Username, req.Reason, req.Note).Scan(&encID)
	if err != nil {
		writeErr(w, err)
		return
	}
	if dx, ok := req.Note["assessment"].([]any); ok {
		for _, d := range dx {
			m, _ := d.(map[string]any)
			if m["certainty"] != "confirmed" {
				continue
			}
			icd, _ := m["icd10"].(string)
			sn, _ := m["snomed"].(string)
			disp, _ := m["display"].(string)
			if disp == "" {
				continue
			}
			_, _ = a.db.Exec(ctx, `INSERT INTO clinical.conditions(patient_id, icd10, snomed, display, onset, status)
				SELECT $1,$2,$3,$4,current_date,'active' WHERE NOT EXISTS (SELECT 1 FROM clinical.conditions WHERE patient_id=$1 AND icd10=$2)`, id, icd, sn, disp)
		}
	}
	a.audit(ctx, AuditEntry{Actor: p.Username, Role: p.Role, Action: "encounter.create", Resource: "Encounter", ResourceID: itoa(encID), Facility: p.Facility(), Outcome: "allowed", Detail: map[string]any{"patient": id}})
	writeJSON(w, 201, map[string]any{"encounter_id": encID})
}

func (a *App) listFacilities(w http.ResponseWriter, r *http.Request) {
	rows, err := a.db.Query(r.Context(), `SELECT id, name, coalesce(hfr_id,''), lat, lon FROM clinical.facilities ORDER BY id`)
	if err != nil {
		writeErr(w, err)
		return
	}
	defer rows.Close()
	out := []map[string]any{}
	for rows.Next() {
		var id, name, hfr string
		var lat, lon float64
		if err := rows.Scan(&id, &name, &hfr, &lat, &lon); err != nil {
			writeErr(w, err)
			return
		}
		out = append(out, map[string]any{"id": id, "name": name, "hfr_id": hfr, "lat": lat, "lon": lon})
	}
	writeJSON(w, 200, out)
}

func (a *App) listPractitioners(w http.ResponseWriter, r *http.Request) {
	rows, err := a.db.Query(r.Context(), `SELECT username, display_name, coalesce(specialty,''), coalesce(facility_id,''), coalesce(seniority,'') FROM clinical.staff WHERE role='doctor' ORDER BY username`)
	if err != nil {
		writeErr(w, err)
		return
	}
	defer rows.Close()
	out := []map[string]string{}
	for rows.Next() {
		var u, n, s, f, sen string
		if err := rows.Scan(&u, &n, &s, &f, &sen); err != nil {
			writeErr(w, err)
			return
		}
		out = append(out, map[string]string{"username": u, "name": n, "specialty": s, "facility_id": f, "seniority": sen})
	}
	writeJSON(w, 200, out)
}
