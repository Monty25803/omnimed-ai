package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"net/http"
	"regexp"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
)

// ABDMGateway is the seam between OmniMed and the ABDM network (ABHA, HFR, HPR, consent manager).
// SandboxGateway answers from local registries; a production gateway would call the ABDM APIs
// with the facility's client credentials.
type ABDMGateway interface {
	VerifyABHA(ctx context.Context, number string) (map[string]any, error)
	CreateConsentArtefact(patientABHA, hiu, purpose string, hiTypes []string, expires time.Time) map[string]any
}

type SandboxGateway struct{ app *App }

var abhaPattern = regexp.MustCompile(`^\d{2}-\d{4}-\d{4}-\d{4}$`)

func (g SandboxGateway) VerifyABHA(ctx context.Context, number string) (map[string]any, error) {
	if !abhaPattern.MatchString(number) {
		return nil, errf(422, "ABHA number must be 14 digits formatted XX-XXXX-XXXX-XXXX")
	}
	rows, err := g.app.db.Query(ctx, `SELECT `+patientCols+` FROM clinical.patients`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		p, err := g.app.scanPatient(rows)
		if err != nil {
			return nil, err
		}
		if p.ABHANumber == number {
			return map[string]any{"verified": true, "abha_number": number, "abha_address": p.ABHAAddress, "name": p.Name,
				"gender": p.Gender, "year_of_birth": p.BirthDate.Year(), "gateway": "sandbox-local"}, nil
		}
	}
	return map[string]any{"verified": false, "abha_number": number, "gateway": "sandbox-local", "reason": "not found in sandbox registry"}, nil
}

func (g SandboxGateway) CreateConsentArtefact(patientABHA, hiu, purpose string, hiTypes []string, expires time.Time) map[string]any {
	b := make([]byte, 8)
	_, _ = rand.Read(b)
	now := time.Now().UTC()
	return map[string]any{
		"consentId": hex.EncodeToString(b), "schemaVersion": "v3", "createdAt": now.Format(time.RFC3339),
		"patient": map[string]string{"id": patientABHA}, "hiu": map[string]string{"id": hiu},
		"purpose":    map[string]string{"code": purpose, "refUri": "http://terminology.hl7.org/CodeSystem/v3-ActReason"},
		"hiTypes":    hiTypes,
		"permission": map[string]any{"accessMode": "VIEW", "dateRange": map[string]string{"from": now.AddDate(-5, 0, 0).Format(time.RFC3339), "to": now.Format(time.RFC3339)}, "dataEraseAt": expires.Format(time.RFC3339)},
		"gateway":    "sandbox-local",
	}
}

func (a *App) abdm() ABDMGateway { return SandboxGateway{app: a} }

func (a *App) verifyABHA(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	if err := requireRole(p, "doctor", "nurse", "patient"); err != nil {
		writeErr(w, err)
		return
	}
	var req struct {
		ABHANumber string `json:"abha_number"`
	}
	if err := decode(r, &req); err != nil {
		writeErr(w, err)
		return
	}
	res, err := a.abdm().VerifyABHA(r.Context(), strings.TrimSpace(req.ABHANumber))
	if err != nil {
		writeErr(w, err)
		return
	}
	a.audit(r.Context(), AuditEntry{Actor: p.Username, Role: p.Role, Action: "abdm.abha.verify", Resource: "ABHA", Outcome: "allowed", Detail: map[string]any{"verified": res["verified"]}})
	writeJSON(w, 200, res)
}

func (a *App) hfrRegistry(w http.ResponseWriter, r *http.Request) {
	a.listFacilities(w, r)
}

func (a *App) hprRegistry(w http.ResponseWriter, r *http.Request) {
	rows, err := a.db.Query(r.Context(), `SELECT username, display_name, role, coalesce(hpr_id,''), coalesce(facility_id,''), coalesce(specialty,'') FROM clinical.staff WHERE hpr_id IS NOT NULL ORDER BY username`)
	if err != nil {
		writeErr(w, err)
		return
	}
	defer rows.Close()
	out := []map[string]string{}
	for rows.Next() {
		var u, n, role, hpr, f, s string
		if err := rows.Scan(&u, &n, &role, &hpr, &f, &s); err != nil {
			writeErr(w, err)
			return
		}
		out = append(out, map[string]string{"username": u, "name": n, "role": role, "hpr_id": hpr, "facility_id": f, "specialty": s})
	}
	writeJSON(w, 200, out)
}

func (a *App) requirePatientSelf(p *Principal) (string, error) {
	if !p.Is("patient") || p.Staff.PatientID == nil {
		return "", errf(403, "health locker is available to patients only")
	}
	return *p.Staff.PatientID, nil
}

func (a *App) locker(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	pid, err := a.requirePatientSelf(p)
	if err != nil {
		writeErr(w, err)
		return
	}
	ctx := r.Context()
	if err := a.authorizePatient(ctx, p, pid, "read"); err != nil {
		writeErr(w, err)
		return
	}
	bundle, err := a.fhirBundle(ctx, pid, "")
	if err != nil {
		writeErr(w, err)
		return
	}
	links := []map[string]any{}
	rows, err := a.db.Query(ctx, `SELECT id, abha_address, hip_id, hip_name, care_context, linked_at FROM clinical.locker_links WHERE patient_id=$1 ORDER BY linked_at`, pid)
	if err != nil {
		writeErr(w, err)
		return
	}
	for rows.Next() {
		var id int
		var abha, hip, hipName, cc string
		var at time.Time
		if rows.Scan(&id, &abha, &hip, &hipName, &cc, &at) == nil {
			links = append(links, map[string]any{"id": id, "abha_address": abha, "hip_id": hip, "hip_name": hipName, "care_context": cc, "linked_at": at})
		}
	}
	rows.Close()
	a.audit(ctx, AuditEntry{Actor: p.Username, Role: p.Role, Action: "locker.view", Resource: "Bundle", ResourceID: pid, Outcome: "allowed"})
	writeJSON(w, 200, map[string]any{"bundle": bundle, "links": links})
}

func (a *App) lockerLink(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	pid, err := a.requirePatientSelf(p)
	if err != nil {
		writeErr(w, err)
		return
	}
	var req struct {
		HIPID string `json:"hip_id"`
	}
	if err := decode(r, &req); err != nil {
		writeErr(w, err)
		return
	}
	ctx := r.Context()
	var hipName string
	if err := a.db.QueryRow(ctx, `SELECT name FROM clinical.facilities WHERE id=$1`, req.HIPID).Scan(&hipName); err != nil {
		writeErr(w, errf(404, "health information provider not found in HFR"))
		return
	}
	pt, err := a.patientByID(ctx, pid)
	if err != nil {
		writeErr(w, err)
		return
	}
	var count int
	_ = a.db.QueryRow(ctx, `SELECT count(*) FROM clinical.encounters WHERE patient_id=$1 AND facility_id=$2`, pid, req.HIPID).Scan(&count)
	cc := pt.MRN + "@" + req.HIPID
	var id int
	if err := a.db.QueryRow(ctx, `INSERT INTO clinical.locker_links(patient_id, abha_address, hip_id, hip_name, care_context) VALUES ($1,$2,$3,$4,$5) RETURNING id`,
		pid, pt.ABHAAddress, req.HIPID, hipName, cc).Scan(&id); err != nil {
		writeErr(w, err)
		return
	}
	a.audit(ctx, AuditEntry{Actor: p.Username, Role: p.Role, Action: "locker.link", Resource: "CareContext", ResourceID: cc, Outcome: "allowed"})
	writeJSON(w, 201, map[string]any{"id": id, "care_context": cc, "hip_name": hipName, "encounters_found": count})
}

func (a *App) listConsents(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	pid, err := a.requirePatientSelf(p)
	if err != nil {
		writeErr(w, err)
		return
	}
	rows, err := a.db.Query(r.Context(), `SELECT c.id, c.grantee_facility, f.name, c.purpose, c.status, c.artefact, c.expires_at, c.created_at
		FROM clinical.consents c JOIN clinical.facilities f ON f.id=c.grantee_facility WHERE patient_id=$1 ORDER BY c.created_at DESC`, pid)
	if err != nil {
		writeErr(w, err)
		return
	}
	defer rows.Close()
	out := []map[string]any{}
	for rows.Next() {
		var id int
		var fac, name, purpose, status string
		var art map[string]any
		var exp *time.Time
		var at time.Time
		if err := rows.Scan(&id, &fac, &name, &purpose, &status, &art, &exp, &at); err != nil {
			writeErr(w, err)
			return
		}
		out = append(out, map[string]any{"id": id, "grantee_facility": fac, "facility_name": name, "purpose": purpose, "status": status, "artefact": art, "expires_at": exp, "created_at": at})
	}
	writeJSON(w, 200, out)
}

func (a *App) grantConsent(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	pid, err := a.requirePatientSelf(p)
	if err != nil {
		writeErr(w, err)
		return
	}
	var req struct {
		GranteeFacility string   `json:"grantee_facility"`
		Purpose         string   `json:"purpose"`
		Days            int      `json:"days"`
		HITypes         []string `json:"hi_types"`
	}
	if err := decode(r, &req); err != nil {
		writeErr(w, err)
		return
	}
	if req.Days <= 0 || req.Days > 365 {
		req.Days = 30
	}
	if req.Purpose == "" {
		req.Purpose = "CAREMGT"
	}
	if len(req.HITypes) == 0 {
		req.HITypes = []string{"OPConsultation", "DiagnosticReport", "Prescription"}
	}
	ctx := r.Context()
	pt, err := a.patientByID(ctx, pid)
	if err != nil {
		writeErr(w, err)
		return
	}
	expires := time.Now().AddDate(0, 0, req.Days)
	art := a.abdm().CreateConsentArtefact(pt.ABHAAddress, req.GranteeFacility, req.Purpose, req.HITypes, expires)
	var id int
	if err := a.db.QueryRow(ctx, `INSERT INTO clinical.consents(patient_id, grantee_facility, purpose, status, artefact, expires_at) VALUES ($1,$2,$3,'granted',$4,$5) RETURNING id`,
		pid, req.GranteeFacility, req.Purpose, art, expires).Scan(&id); err != nil {
		writeErr(w, errf(422, "unknown facility"))
		return
	}
	a.audit(ctx, AuditEntry{Actor: p.Username, Role: p.Role, Action: "consent.grant", Resource: "Consent", ResourceID: itoa(id), Outcome: "allowed", Detail: map[string]any{"grantee": req.GranteeFacility, "days": req.Days}})
	writeJSON(w, 201, map[string]any{"id": id, "artefact": art})
}

func (a *App) revokeConsent(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	pid, err := a.requirePatientSelf(p)
	if err != nil {
		writeErr(w, err)
		return
	}
	id := chi.URLParam(r, "id")
	tag, err := a.db.Exec(r.Context(), `UPDATE clinical.consents SET status='revoked' WHERE id=$1 AND patient_id=$2 AND status='granted'`, id, pid)
	if err != nil {
		writeErr(w, err)
		return
	}
	if tag.RowsAffected() == 0 {
		writeErr(w, errf(404, "active consent not found"))
		return
	}
	a.audit(r.Context(), AuditEntry{Actor: p.Username, Role: p.Role, Action: "consent.revoke", Resource: "Consent", ResourceID: id, Outcome: "allowed"})
	writeJSON(w, 200, map[string]any{"id": id, "status": "revoked"})
}
