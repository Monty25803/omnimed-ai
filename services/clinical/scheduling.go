package main

import (
	"crypto/rand"
	"encoding/hex"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/jackc/pgx/v5"
)

func (a *App) listAvailability(w http.ResponseWriter, r *http.Request) {
	practitioner := r.URL.Query().Get("practitioner")
	rows, err := a.db.Query(r.Context(), `SELECT av.id, av.practitioner, s.display_name, coalesce(s.specialty,''), av.starts_at, av.duration_min, av.mode
		FROM clinical.availability av JOIN clinical.staff s ON s.username=av.practitioner
		WHERE NOT av.booked AND av.starts_at > now() AND ($1='' OR av.practitioner=$1) ORDER BY av.starts_at LIMIT 200`, practitioner)
	if err != nil {
		writeErr(w, err)
		return
	}
	defer rows.Close()
	out := []map[string]any{}
	for rows.Next() {
		var id, dur int
		var pr, name, spec, mode string
		var at time.Time
		if err := rows.Scan(&id, &pr, &name, &spec, &at, &dur, &mode); err != nil {
			writeErr(w, err)
			return
		}
		out = append(out, map[string]any{"id": id, "practitioner": pr, "name": name, "specialty": spec, "starts_at": at, "duration_min": dur, "mode": mode})
	}
	writeJSON(w, 200, out)
}

func (a *App) bookAppointment(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	ctx := r.Context()
	var req struct {
		SlotID    int    `json:"slot_id"`
		PatientID string `json:"patient_id"`
		Mode      string `json:"mode"`
		Reason    string `json:"reason"`
	}
	if err := decode(r, &req); err != nil {
		writeErr(w, err)
		return
	}
	if p.Is("patient") {
		req.PatientID = derefOr(p.Staff.PatientID, "")
	} else if err := requireRole(p, "doctor", "nurse"); err != nil {
		writeErr(w, err)
		return
	}
	action := "write"
	if p.Is("patient") {
		action = "read"
	}
	if err := a.authorizePatient(ctx, p, req.PatientID, action); err != nil {
		writeErr(w, err)
		return
	}
	if req.Mode != "video" {
		req.Mode = "in-person"
	}
	tx, err := a.db.Begin(ctx)
	if err != nil {
		writeErr(w, err)
		return
	}
	defer tx.Rollback(ctx)
	var practitioner, slotMode string
	var starts time.Time
	err = tx.QueryRow(ctx, `UPDATE clinical.availability SET booked=true WHERE id=$1 AND NOT booked AND starts_at > now() RETURNING practitioner, starts_at, mode`, req.SlotID).Scan(&practitioner, &starts, &slotMode)
	if err == pgx.ErrNoRows {
		writeErr(w, errf(409, "slot is no longer available"))
		return
	}
	if err != nil {
		writeErr(w, err)
		return
	}
	var video *string
	if req.Mode == "video" {
		b := make([]byte, 6)
		_, _ = rand.Read(b)
		u := "https://meet.jit.si/OmniMed-" + hex.EncodeToString(b)
		video = &u
	}
	var id int
	if err := tx.QueryRow(ctx, `INSERT INTO clinical.appointments(slot_id, patient_id, practitioner, mode, reason, video_url) VALUES ($1,$2,$3,$4,$5,$6) RETURNING id`,
		req.SlotID, req.PatientID, practitioner, req.Mode, req.Reason, video).Scan(&id); err != nil {
		writeErr(w, err)
		return
	}
	if err := tx.Commit(ctx); err != nil {
		writeErr(w, err)
		return
	}
	a.audit(ctx, AuditEntry{Actor: p.Username, Role: p.Role, Action: "appointment.book", Resource: "Appointment", ResourceID: itoa(id), Outcome: "allowed", Detail: map[string]any{"patient": req.PatientID, "practitioner": practitioner}})
	writeJSON(w, 201, map[string]any{"id": id, "practitioner": practitioner, "starts_at": starts, "mode": req.Mode, "video_url": video})
}

func (a *App) listAppointments(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	var rows pgx.Rows
	var err error
	q := `SELECT ap.id, ap.patient_id, ap.practitioner, s.display_name, av.starts_at, ap.mode, coalesce(ap.reason,''), ap.video_url, ap.status
		FROM clinical.appointments ap JOIN clinical.availability av ON av.id=ap.slot_id JOIN clinical.staff s ON s.username=ap.practitioner WHERE `
	switch {
	case p.Is("patient"):
		rows, err = a.db.Query(r.Context(), q+`ap.patient_id=$1 ORDER BY av.starts_at`, derefOr(p.Staff.PatientID, ""))
	case p.Is("doctor"):
		rows, err = a.db.Query(r.Context(), q+`ap.practitioner=$1 ORDER BY av.starts_at`, p.Username)
	default:
		writeErr(w, errf(403, "role "+p.Role+" has no appointment list"))
		return
	}
	if err != nil {
		writeErr(w, err)
		return
	}
	defer rows.Close()
	out := []map[string]any{}
	for rows.Next() {
		var id int
		var pid, pr, name, mode, reason, status string
		var video *string
		var at time.Time
		if err := rows.Scan(&id, &pid, &pr, &name, &at, &mode, &reason, &video, &status); err != nil {
			writeErr(w, err)
			return
		}
		out = append(out, map[string]any{"id": id, "patient_id": pid, "practitioner": pr, "practitioner_name": name, "starts_at": at, "mode": mode, "reason": reason, "video_url": video, "status": status})
	}
	writeJSON(w, 200, out)
}

func (a *App) cancelAppointment(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	var slot int
	var pid, pr string
	if err := a.db.QueryRow(ctx, `SELECT slot_id, patient_id, practitioner FROM clinical.appointments WHERE id=$1 AND status='booked'`, id).Scan(&slot, &pid, &pr); err != nil {
		writeErr(w, errf(404, "appointment not found"))
		return
	}
	if !(p.Is("patient") && derefOr(p.Staff.PatientID, "") == pid) && !(p.Is("doctor") && p.Username == pr) {
		writeErr(w, errf(403, "not your appointment"))
		return
	}
	_, _ = a.db.Exec(ctx, `UPDATE clinical.appointments SET status='cancelled' WHERE id=$1`, id)
	_, _ = a.db.Exec(ctx, `UPDATE clinical.availability SET booked=false WHERE id=$1`, slot)
	a.audit(ctx, AuditEntry{Actor: p.Username, Role: p.Role, Action: "appointment.cancel", Resource: "Appointment", ResourceID: id, Outcome: "allowed"})
	writeJSON(w, 200, map[string]any{"id": id, "status": "cancelled"})
}
