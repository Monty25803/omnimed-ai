package main

import (
	"context"
	"net/http"
	"time"

	"github.com/jackc/pgx/v5"
)

type Decision struct {
	Allow  bool   `json:"allow"`
	Reason string `json:"reason"`
	Basis  string `json:"basis,omitempty"`
}

// Actions: read, write, dispense, administer, result.
func (a *App) decidePatient(ctx context.Context, p *Principal, patientID, action string) (Decision, error) {
	var facility string
	err := a.db.QueryRow(ctx, `SELECT facility_id FROM clinical.patients WHERE id=$1`, patientID).Scan(&facility)
	if err == pgx.ErrNoRows {
		return Decision{false, "patient not found", ""}, nil
	}
	if err != nil {
		return Decision{}, err
	}

	switch p.Role {
	case "mr":
		return Decision{false, "medical representatives never access patient data", ""}, nil
	case "admin":
		return Decision{false, "administrators have no clinical data access", ""}, nil
	case "patient":
		if p.Staff.PatientID != nil && *p.Staff.PatientID == patientID {
			return Decision{true, "own record", "self"}, nil
		}
		return Decision{false, "patients may only access their own record", ""}, nil
	}

	if action != "read" {
		if !p.Staff.OnShift || !withinShift(p.Staff.ShiftStart, p.Staff.ShiftEnd, time.Now()) {
			return Decision{false, "clinical writes require an active shift", ""}, nil
		}
	}

	if p.Facility() == facility {
		if action == "read" {
			return Decision{true, "same facility", "facility"}, nil
		}
		// Lab and pharmacy staff act on any patient of their facility; others must be on the care team.
		if p.Is("lab", "pharmacist") {
			return Decision{true, "facility service role", "facility"}, nil
		}
		var member bool
		if err := a.db.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM clinical.care_team WHERE patient_id=$1 AND username=$2)`, patientID, p.Username).Scan(&member); err != nil {
			return Decision{}, err
		}
		if member {
			return Decision{true, "care team member", "care-team"}, nil
		}
		return Decision{false, "not on this patient's care team", ""}, nil
	}

	if action == "read" {
		var consented bool
		err := a.db.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM clinical.consents WHERE patient_id=$1 AND grantee_facility=$2
			AND status='granted' AND (expires_at IS NULL OR expires_at > now()))`, patientID, p.Facility()).Scan(&consented)
		if err != nil {
			return Decision{}, err
		}
		if consented {
			return Decision{true, "patient consent for requesting facility", "consent"}, nil
		}
	}
	return Decision{false, "cross-facility access without patient consent", ""}, nil
}

func withinShift(start, end string, now time.Time) bool {
	loc, err := time.LoadLocation("Asia/Kolkata")
	if err != nil {
		loc = time.UTC
	}
	t := now.In(loc).Format("15:04:05")
	if start <= end {
		return t >= start && t <= end
	}
	return t >= start || t <= end
}

// authorizePatient records every PHI access decision in the audit ledger.
func (a *App) authorizePatient(ctx context.Context, p *Principal, patientID, action string) error {
	d, err := a.decidePatient(ctx, p, patientID, action)
	if err != nil {
		return err
	}
	outcome := "allowed"
	if !d.Allow {
		outcome = "denied"
	}
	a.audit(ctx, AuditEntry{Actor: p.Username, Role: p.Role, Action: "phi." + action, Resource: "Patient", ResourceID: patientID,
		Facility: p.Facility(), Outcome: outcome, Detail: map[string]any{"reason": d.Reason, "basis": d.Basis, "ip": p.ClientIP}})
	if !d.Allow {
		return errf(http.StatusForbidden, d.Reason)
	}
	return nil
}

// internalAuthz lets the Python services reuse one token verifier and one policy engine.
func (a *App) internalAuthz(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Token     string   `json:"token"`
		PatientID string   `json:"patient_id"`
		Action    string   `json:"action"`
		Roles     []string `json:"roles"`
	}
	if err := decode(r, &req); err != nil {
		writeErr(w, err)
		return
	}
	if req.Token == "" {
		a.audit(r.Context(), AuditEntry{Actor: "anonymous", Action: "authenticate", Resource: "internal", Outcome: "denied", Detail: map[string]any{"reason": "missing token"}})
		writeJSON(w, 403, map[string]any{"allow": false, "reason": "authentication required"})
		return
	}
	p, err := a.verifyToken(r.Context(), req.Token)
	if err != nil {
		writeJSON(w, 403, map[string]any{"allow": false, "reason": err.Error()})
		return
	}
	if len(req.Roles) > 0 && !p.Is(req.Roles...) {
		writeJSON(w, 403, map[string]any{"allow": false, "reason": "role " + p.Role + " may not perform this action", "principal": p})
		return
	}
	if req.PatientID != "" {
		if req.Action == "" {
			req.Action = "read"
		}
		if err := a.authorizePatient(r.Context(), p, req.PatientID, req.Action); err != nil {
			writeJSON(w, 403, map[string]any{"allow": false, "reason": err.Error(), "principal": p})
			return
		}
	}
	writeJSON(w, 200, map[string]any{"allow": true, "principal": p})
}
