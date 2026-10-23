package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"strings"
	"time"

	"github.com/nats-io/nats.go"
)

const streamName = "SYNAPSE"

var streamSubjects = []string{"lab.panic", "lab.result", "ward.redalert", "rx.created", "audit.record"}

func (a *App) ensureStream() error {
	cfg := &nats.StreamConfig{Name: streamName, Subjects: streamSubjects, Storage: nats.FileStorage, MaxAge: 7 * 24 * time.Hour}
	if _, err := a.js.StreamInfo(streamName); err == nil {
		_, err = a.js.UpdateStream(cfg)
		return err
	}
	_, err := a.js.AddStream(cfg)
	return err
}

type PanicEvent struct {
	PatientID  string    `json:"patient_id"`
	LOINC      string    `json:"loinc"`
	Analyte    string    `json:"analyte"`
	Value      float64   `json:"value"`
	Unit       string    `json:"unit"`
	Flag       string    `json:"flag"`
	ResultID   int       `json:"result_id"`
	MessageID  string    `json:"message_id"`
	ReceivedAt time.Time `json:"received_at"`
	EnteredBy  string    `json:"entered_by"`
}

// holdRule maps a panic direction on an analyte to drug classes that must be held
// and the stat ward task that must be raised.
type holdRule struct {
	classes []string
	task    string
	review  []string
}

var panicRules = map[string]holdRule{
	"2823-3:HH":  {classes: []string{"potassium-sparing", "potassium-supplement"}, task: "stat_ecg", review: []string{"ace-inhibitor", "arb"}},
	"2823-3:LL":  {task: "stat_ecg"},
	"2345-7:LL":  {classes: []string{"insulin", "sulfonylurea"}, task: "stat_glucose"},
	"6301-6:HH":  {classes: []string{"anticoagulant"}, task: "bleeding_assessment"},
	"777-3:LL":   {classes: []string{"anticoagulant", "antiplatelet"}, task: "bleeding_assessment"},
	"10839-9:HH": {task: "stat_ecg"},
}

var taskText = map[string]string{
	"stat_ecg":            "Perform STAT 12-lead ECG and attach to chart",
	"stat_glucose":        "Bedside glucose now; start hypoglycaemia protocol",
	"bleeding_assessment": "Bleeding assessment and repeat vitals every 15 minutes",
	"stat_review":         "Urgent bedside review of critical lab value",
}

func (a *App) startSynapse() error {
	subs := []struct {
		subject, durable string
		fn               func(context.Context, []byte) error
	}{
		{"lab.panic", "clinical-lab-panic", a.onLabPanic},
		{"lab.result", "clinical-lab-result", a.onLabResult},
		{"ward.redalert", "clinical-ward-redalert", a.onRedAlert},
		{"audit.record", "clinical-audit", a.onAuditRecord},
	}
	for _, s := range subs {
		s := s
		_, err := a.js.Subscribe(s.subject, func(m *nats.Msg) {
			ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
			defer cancel()
			if err := s.fn(ctx, m.Data); err != nil {
				log.Printf("synapse %s: %v", s.subject, err)
				_ = m.NakWithDelay(2 * time.Second)
				return
			}
			_ = m.Ack()
		}, nats.Durable(s.durable), nats.ManualAck(), nats.AckWait(30*time.Second), nats.MaxDeliver(5), nats.DeliverAll())
		if err != nil {
			return fmt.Errorf("subscribe %s: %w", s.subject, err)
		}
	}
	_, err := a.nc.QueueSubscribe("pharmacy.stock.query", "pharmacy-network", func(m *nats.Msg) {
		var q StockQuery
		if err := json.Unmarshal(m.Data, &q); err != nil {
			_ = m.Respond([]byte(`{"error":"bad query"}`))
			return
		}
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		ans, err := a.stockLookup(ctx, q)
		if err != nil {
			b, _ := json.Marshal(map[string]string{"error": err.Error()})
			_ = m.Respond(b)
			return
		}
		b, _ := json.Marshal(ans)
		_ = m.Respond(b)
	})
	if err != nil {
		return err
	}
	log.Printf("clinical synapse subscribed to %s and pharmacy.stock.query", strings.Join(streamSubjects, ", "))
	return nil
}

type careMember struct {
	username, role, seniority, display string
}

func (a *App) careTeam(ctx context.Context, patientID string) ([]careMember, error) {
	rows, err := a.db.Query(ctx, `SELECT c.username, s.role, coalesce(s.seniority,''), s.display_name FROM clinical.care_team c
		JOIN clinical.staff s USING (username) WHERE c.patient_id=$1`, patientID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []careMember{}
	for rows.Next() {
		var m careMember
		if err := rows.Scan(&m.username, &m.role, &m.seniority, &m.display); err != nil {
			return nil, err
		}
		out = append(out, m)
	}
	return out, nil
}

func (a *App) onLabPanic(ctx context.Context, data []byte) error {
	var ev PanicEvent
	if err := json.Unmarshal(data, &ev); err != nil {
		log.Printf("dropping malformed panic event: %v", err)
		return nil
	}
	var exists bool
	if err := a.db.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM clinical.alerts WHERE kind='lab.panic' AND payload->>'result_id'=$1)`, itoa(ev.ResultID)).Scan(&exists); err != nil {
		return err
	}
	if exists {
		return nil
	}
	pt, err := a.patientByID(ctx, ev.PatientID)
	if err != nil {
		return err
	}
	team, err := a.careTeam(ctx, ev.PatientID)
	if err != nil {
		return err
	}
	loc := derefOr(pt.Ward, "OPD")
	if pt.Bed != nil {
		loc += "-" + *pt.Bed
	}
	direction := "HIGH"
	if ev.Flag == "LL" {
		direction = "LOW"
	}
	headline := fmt.Sprintf("PANIC %s: %s %s %s for %s (%s, %s)", direction, ev.Analyte, trimFloat(ev.Value), ev.Unit, pt.Name, pt.MRN, loc)
	rule, hasRule := panicRules[ev.LOINC+":"+ev.Flag]
	task := "stat_review"
	if hasRule && rule.task != "" {
		task = rule.task
	}

	held := []string{}
	review := []string{}
	if hasRule {
		meds, err := a.medications(ctx, "patient_id=$1 AND status='active'", ev.PatientID)
		if err != nil {
			return err
		}
		for _, m := range meds {
			d, err := a.drug(ctx, m.RxCUI)
			if err != nil {
				continue
			}
			for _, c := range rule.classes {
				if hasClass(d.Classes, c) {
					reason := fmt.Sprintf("Auto-hold by Clinical Synapse: %s %s %s", ev.Analyte, trimFloat(ev.Value), ev.Unit)
					if _, err := a.db.Exec(ctx, `UPDATE clinical.medication_requests SET status='on-hold', hold_reason=$2 WHERE id=$1 AND status='active'`, m.ID, reason); err != nil {
						return err
					}
					held = append(held, fmt.Sprintf("%s %s mg (order %d)", m.Drug, trimFloat(m.StrengthMg), m.ID))
					a.audit(ctx, AuditEntry{Actor: "synapse", Action: "rx.hold", Resource: "MedicationRequest", ResourceID: itoa(m.ID), Facility: pt.FacilityID, Outcome: "allowed", Detail: map[string]any{"reason": reason, "result_id": ev.ResultID}})
					break
				}
			}
			for _, c := range rule.review {
				if hasClass(d.Classes, c) {
					review = append(review, fmt.Sprintf("%s %s mg", m.Drug, trimFloat(m.StrengthMg)))
				}
			}
		}
	}

	var taskID int
	if err := a.db.QueryRow(ctx, `INSERT INTO clinical.tasks(patient_id, kind, detail, facility_id, ward) VALUES ($1,$2,$3,$4,$5) RETURNING id`,
		ev.PatientID, task, taskText[task]+" ("+ev.Analyte+" "+trimFloat(ev.Value)+" "+ev.Unit+")", pt.FacilityID, pt.Ward).Scan(&taskID); err != nil {
		return err
	}

	payload := map[string]any{"result_id": ev.ResultID, "loinc": ev.LOINC, "value": ev.Value, "unit": ev.Unit, "flag": ev.Flag, "held": held, "review": review,
		"task_id": taskID, "lab_received_at": ev.ReceivedAt, "synapse_at": time.Now().UTC()}
	src := ev.ReceivedAt
	sent := 0
	for _, m := range team {
		var msg, severity string
		switch m.role {
		case "doctor":
			msg, severity = headline, "critical"
			if len(held) > 0 {
				msg += ". Auto-held: " + strings.Join(held, ", ")
			}
			if len(review) > 0 {
				msg += ". Review: " + strings.Join(review, ", ")
			}
		case "nurse":
			msg, severity = headline+". "+taskText[task], "critical"
		case "pharmacist":
			if len(held) == 0 && len(review) == 0 {
				continue
			}
			msg, severity = headline+". Orders placed on hold: "+strings.Join(append(held, review...), ", "), "high"
		default:
			continue
		}
		if _, err := a.createAlert(ctx, Alert{PatientID: ev.PatientID, Kind: "lab.panic", Severity: severity, Message: msg, Recipient: m.username, Payload: payload, SourceTS: &src}); err != nil {
			return err
		}
		sent++
	}
	a.audit(ctx, AuditEntry{Actor: "synapse", Action: "synapse.panic", Resource: "Observation", ResourceID: itoa(ev.ResultID), Facility: pt.FacilityID, Outcome: "allowed",
		Detail: map[string]any{"patient": ev.PatientID, "alerts": sent, "held": held, "task": taskID, "latency_ms": time.Since(ev.ReceivedAt).Milliseconds()}})
	return nil
}

func (a *App) onLabResult(ctx context.Context, data []byte) error {
	var ev struct {
		PatientID  string    `json:"patient_id"`
		LOINC      string    `json:"loinc"`
		Analyte    string    `json:"analyte"`
		Value      float64   `json:"value"`
		Unit       string    `json:"unit"`
		Flag       string    `json:"flag"`
		ReceivedAt time.Time `json:"received_at"`
	}
	if err := json.Unmarshal(data, &ev); err != nil {
		return nil
	}
	flag := ev.Flag
	if flag == "HH" {
		flag = "H"
	} else if flag == "LL" {
		flag = "L"
	}
	_, err := a.db.Exec(ctx, `INSERT INTO clinical.observations(patient_id, loinc, display, value, unit, flag, source, effective_at) VALUES ($1,$2,$3,$4,$5,$6,'lab',$7)`,
		ev.PatientID, ev.LOINC, ev.Analyte, ev.Value, ev.Unit, flag, ev.ReceivedAt)
	return err
}

func (a *App) onRedAlert(ctx context.Context, data []byte) error {
	var ev struct {
		PatientID string      `json:"patient_id"`
		MEWS      int         `json:"mews"`
		QSOFA     int         `json:"qsofa"`
		Vitals    VitalsInput `json:"vitals"`
		At        time.Time   `json:"at"`
	}
	if err := json.Unmarshal(data, &ev); err != nil {
		return nil
	}
	pt, err := a.patientByID(ctx, ev.PatientID)
	if err != nil {
		return err
	}
	team, err := a.careTeam(ctx, ev.PatientID)
	if err != nil {
		return err
	}
	// On-call doctor: the resident if on the care team, otherwise the attending.
	var onCall string
	for _, m := range team {
		if m.role == "doctor" && m.seniority == "resident" {
			onCall = m.username
		}
	}
	if onCall == "" {
		for _, m := range team {
			if m.role == "doctor" {
				onCall = m.username
			}
		}
	}
	msg := fmt.Sprintf("LEVEL-1 RED ALERT: %s (%s-%s) MEWS %d, qSOFA %d", pt.Name, derefOr(pt.Ward, ""), derefOr(pt.Bed, ""), ev.MEWS, ev.QSOFA)
	payload := map[string]any{"mews": ev.MEWS, "qsofa": ev.QSOFA, "vitals": ev.Vitals}
	at := ev.At
	if onCall != "" {
		if _, err := a.createAlert(ctx, Alert{PatientID: ev.PatientID, Kind: "ward.redalert", Severity: "critical", Message: msg, Recipient: onCall, Payload: payload, SourceTS: &at}); err != nil {
			return err
		}
	}
	for _, m := range team {
		if m.role == "nurse" && m.seniority == "nurse" {
			if _, err := a.createAlert(ctx, Alert{PatientID: ev.PatientID, Kind: "ward.redalert", Severity: "high", Message: msg + ". Repeat observations every 15 minutes.", Recipient: m.username, Payload: payload, SourceTS: &at}); err != nil {
				return err
			}
		}
	}
	return nil
}

func (a *App) onAuditRecord(ctx context.Context, data []byte) error {
	e, err := auditFromEvent(data)
	if err != nil {
		log.Printf("dropping invalid audit event: %s", string(data))
		return nil
	}
	return a.appendAudit(ctx, e)
}
