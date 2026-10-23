package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/jackc/pgx/v5"
)

type Alert struct {
	ID            int            `json:"id"`
	PatientID     string         `json:"patient_id"`
	Kind          string         `json:"kind"`
	Severity      string         `json:"severity"`
	Message       string         `json:"message"`
	Recipient     string         `json:"recipient"`
	Tier          int            `json:"tier"`
	Status        string         `json:"status"`
	Payload       map[string]any `json:"payload"`
	SourceTS      *time.Time     `json:"source_ts"`
	CreatedAt     time.Time      `json:"created_at"`
	AckedAt       *time.Time     `json:"acked_at"`
	EscalatedFrom *int           `json:"escalated_from"`
}

// createAlert persists an alert and fans it out to the recipient's live channel.
func (a *App) createAlert(ctx context.Context, al Alert) (Alert, error) {
	err := a.db.QueryRow(ctx, `INSERT INTO clinical.alerts(patient_id, kind, severity, message, recipient, tier, payload, source_ts, escalated_from)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id, status, created_at`, al.PatientID, al.Kind, al.Severity, al.Message, al.Recipient, al.Tier, al.Payload, al.SourceTS, al.EscalatedFrom).
		Scan(&al.ID, &al.Status, &al.CreatedAt)
	if err != nil {
		return al, err
	}
	b, _ := json.Marshal(al)
	if err := a.rdb.Publish(ctx, "alerts:"+al.Recipient, b).Err(); err != nil {
		log.Printf("redis publish: %v", err)
	}
	return al, nil
}

const alertCols = `id, coalesce(patient_id,''), kind, severity, message, recipient, tier, status, payload, source_ts, created_at, acked_at, escalated_from`

func scanAlert(row pgx.Row) (Alert, error) {
	var al Alert
	err := row.Scan(&al.ID, &al.PatientID, &al.Kind, &al.Severity, &al.Message, &al.Recipient, &al.Tier, &al.Status, &al.Payload, &al.SourceTS, &al.CreatedAt, &al.AckedAt, &al.EscalatedFrom)
	return al, err
}

func (a *App) listAlerts(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	status := r.URL.Query().Get("status")
	rows, err := a.db.Query(r.Context(), `SELECT `+alertCols+` FROM clinical.alerts WHERE recipient=$1 AND ($2='' OR status=$2) ORDER BY created_at DESC LIMIT 100`, p.Username, status)
	if err != nil {
		writeErr(w, err)
		return
	}
	defer rows.Close()
	out := []Alert{}
	for rows.Next() {
		al, err := scanAlert(rows)
		if err != nil {
			writeErr(w, err)
			return
		}
		out = append(out, al)
	}
	writeJSON(w, 200, out)
}

func (a *App) ackAlert(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	id := chi.URLParam(r, "id")
	tag, err := a.db.Exec(r.Context(), `UPDATE clinical.alerts SET status='ack', acked_at=now() WHERE id=$1 AND recipient=$2 AND status IN ('open','escalated','unanswered')`, id, p.Username)
	if err != nil {
		writeErr(w, err)
		return
	}
	if tag.RowsAffected() == 0 {
		writeErr(w, errf(404, "alert not found for this user"))
		return
	}
	// Acknowledging any tier closes the whole escalation chain for that event.
	_, _ = a.db.Exec(r.Context(), `WITH RECURSIVE chain AS (
			SELECT id, escalated_from FROM clinical.alerts WHERE id=$1
			UNION SELECT a.id, a.escalated_from FROM clinical.alerts a JOIN chain c ON a.id=c.escalated_from OR a.escalated_from=c.id)
		UPDATE clinical.alerts SET status='ack', acked_at=coalesce(acked_at, now()) WHERE id IN (SELECT id FROM chain) AND status IN ('open','escalated','unanswered')`, id)
	a.audit(r.Context(), AuditEntry{Actor: p.Username, Role: p.Role, Action: "alert.ack", Resource: "Alert", ResourceID: id, Facility: p.Facility(), Outcome: "allowed"})
	writeJSON(w, 200, map[string]any{"id": id, "status": "ack"})
}

// alertStream is a Server-Sent Events feed of the caller's alerts.
func (a *App) alertStream(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	fl, ok := w.(http.Flusher)
	if !ok {
		writeErr(w, errf(500, "streaming unsupported"))
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	sub := a.rdb.Subscribe(r.Context(), "alerts:"+p.Username)
	defer sub.Close()
	if _, err := sub.Receive(r.Context()); err != nil {
		return
	}
	fmt.Fprintf(w, "event: ready\ndata: {\"user\":%q}\n\n", p.Username)
	fl.Flush()
	ch := sub.Channel()
	hb := time.NewTicker(15 * time.Second)
	defer hb.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case m, ok := <-ch:
			if !ok {
				return
			}
			fmt.Fprintf(w, "event: alert\ndata: %s\n\n", m.Payload)
			fl.Flush()
		case <-hb.C:
			fmt.Fprint(w, ": heartbeat\n\n")
			fl.Flush()
		}
	}
}

// nextTier returns who an unanswered alert escalates to:
// nurse -> senior nurse -> resident -> attending.
func (a *App) nextTier(ctx context.Context, al Alert) (string, bool) {
	var role, seniority, facility string
	if err := a.db.QueryRow(ctx, `SELECT role, coalesce(seniority,''), coalesce(facility_id,'') FROM clinical.staff WHERE username=$1`, al.Recipient).Scan(&role, &seniority, &facility); err != nil {
		return "", false
	}
	ladder := []string{"nurse", "senior_nurse", "resident", "attending"}
	idx := -1
	for i, l := range ladder {
		if l == seniority {
			idx = i
		}
	}
	if idx < 0 || idx == len(ladder)-1 {
		return "", false
	}
	for _, next := range ladder[idx+1:] {
		var u string
		err := a.db.QueryRow(ctx, `SELECT s.username FROM clinical.care_team c JOIN clinical.staff s USING (username)
			WHERE c.patient_id=$1 AND s.seniority=$2 AND s.on_shift ORDER BY s.username LIMIT 1`, al.PatientID, next).Scan(&u)
		if err == nil && u != al.Recipient {
			return u, true
		}
	}
	return "", false
}

func (a *App) escalationLoop(ctx context.Context) {
	t := time.NewTicker(3 * time.Second)
	defer t.Stop()
	for range t.C {
		rows, err := a.db.Query(ctx, `SELECT `+alertCols+` FROM clinical.alerts WHERE status='open' AND severity IN ('critical','high')
			AND created_at < now() - make_interval(secs => $1) LIMIT 50`, a.cfg.EscalationTimeout.Seconds())
		if err != nil {
			log.Printf("escalation query: %v", err)
			continue
		}
		var due []Alert
		for rows.Next() {
			if al, err := scanAlert(rows); err == nil {
				due = append(due, al)
			}
		}
		rows.Close()
		for _, al := range due {
			next, ok := a.nextTier(ctx, al)
			status := "escalated"
			if !ok {
				status = "unanswered"
			}
			tag, err := a.db.Exec(ctx, `UPDATE clinical.alerts SET status=$2 WHERE id=$1 AND status='open'`, al.ID, status)
			if err != nil || tag.RowsAffected() == 0 {
				continue
			}
			if !ok {
				continue
			}
			from := al.ID
			payload := al.Payload
			if payload == nil {
				payload = map[string]any{}
			}
			original, _ := payload["original_message"].(string)
			if original == "" {
				original = al.Message
			}
			payload["original_message"] = original
			payload["escalated_after_seconds"] = a.cfg.EscalationTimeout.Seconds()
			if _, ok := payload["original_recipient"]; !ok {
				payload["original_recipient"] = al.Recipient
			}
			_, err = a.createAlert(ctx, Alert{PatientID: al.PatientID, Kind: al.Kind, Severity: al.Severity, Recipient: next, Tier: al.Tier + 1,
				Message: fmt.Sprintf("ESCALATED to tier %d (no response from %s in %ds): %s", al.Tier+1, al.Recipient, int(a.cfg.EscalationTimeout.Seconds()), original),
				Payload: payload, SourceTS: al.SourceTS, EscalatedFrom: &from})
			if err != nil {
				log.Printf("escalation create: %v", err)
				continue
			}
			a.audit(ctx, AuditEntry{Actor: "system", Action: "alert.escalate", Resource: "Alert", ResourceID: itoa(al.ID), Outcome: "allowed", Detail: map[string]any{"from": al.Recipient, "to": next}})
		}
	}
}
