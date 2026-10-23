package main

import (
	"context"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
)

// FHIR R4 resource builders. Internal rows map onto base R4 resources so the
// same bundle serves the doctor timeline, the patient health locker and ABDM exchange.

func coding(system, code, display string) map[string]any {
	return map[string]any{"system": system, "code": code, "display": display}
}

const (
	sysSNOMED = "http://snomed.info/sct"
	sysICD10  = "http://hl7.org/fhir/sid/icd-10"
	sysLOINC  = "http://loinc.org"
	sysRxNorm = "http://www.nlm.nih.gov/research/umls/rxnorm"
	sysABHA   = "https://healthid.abdm.gov.in"
)

func fhirPatient(p *PatientSummary) map[string]any {
	parts := strings.SplitN(p.Name, " ", 2)
	name := map[string]any{"text": p.Name, "given": []string{parts[0]}}
	if len(parts) > 1 {
		name["family"] = parts[1]
	}
	return map[string]any{
		"resourceType": "Patient", "id": p.ID,
		"identifier": []map[string]any{
			{"system": "urn:omnimed:mrn", "value": p.MRN},
			{"system": sysABHA, "value": p.ABHANumber},
		},
		"name": []any{name}, "gender": p.Gender, "birthDate": p.BirthDate.Format("2006-01-02"),
		"telecom":              []map[string]string{{"system": "phone", "value": p.Phone}},
		"managingOrganization": map[string]string{"reference": "Organization/" + p.FacilityID},
	}
}

func (a *App) fhirBundle(ctx context.Context, patientID string, facilityFilter string) (map[string]any, error) {
	pt, err := a.patientByID(ctx, patientID)
	if err != nil {
		return nil, err
	}
	entries := []map[string]any{{"fullUrl": "Patient/" + pt.ID, "resource": fhirPatient(pt)}}
	ref := map[string]string{"reference": "Patient/" + pt.ID}

	cond, err := a.conditions(ctx, patientID)
	if err != nil {
		return nil, err
	}
	for i, c := range cond {
		codings := []map[string]any{}
		if c.SNOMED != nil {
			codings = append(codings, coding(sysSNOMED, *c.SNOMED, c.Display))
		}
		if c.ICD10 != nil {
			codings = append(codings, coding(sysICD10, *c.ICD10, c.Display))
		}
		res := map[string]any{"resourceType": "Condition", "id": fmt.Sprintf("%s-c%d", pt.ID, i), "subject": ref,
			"clinicalStatus": map[string]any{"coding": []any{coding("http://terminology.hl7.org/CodeSystem/condition-clinical", c.Status, c.Status)}},
			"code":           map[string]any{"coding": codings, "text": c.Display}}
		if c.Onset != nil {
			res["onsetDateTime"] = c.Onset.Format("2006-01-02")
		}
		entries = append(entries, map[string]any{"resource": res})
	}

	al, err := a.allergies(ctx, patientID)
	if err != nil {
		return nil, err
	}
	for i, x := range al {
		res := map[string]any{"resourceType": "AllergyIntolerance", "id": fmt.Sprintf("%s-a%d", pt.ID, i), "patient": ref,
			"criticality": map[string]string{"severe": "high", "moderate": "low", "mild": "low"}[x.Severity],
			"code":        map[string]any{"text": x.Substance}}
		if x.SNOMED != nil {
			res["code"] = map[string]any{"coding": []any{coding(sysSNOMED, *x.SNOMED, x.Substance)}, "text": x.Substance}
		}
		if x.Reaction != nil {
			res["reaction"] = []map[string]any{{"description": *x.Reaction}}
		}
		entries = append(entries, map[string]any{"resource": res})
	}

	q := `SELECT id, loinc, display, value, coalesce(unit,''), coalesce(flag,''), source, effective_at FROM clinical.observations WHERE patient_id=$1 ORDER BY effective_at`
	rows, err := a.db.Query(ctx, q, patientID)
	if err != nil {
		return nil, err
	}
	for rows.Next() {
		var id int
		var loinc, disp, unit, flag, source string
		var v *float64
		var at time.Time
		if err := rows.Scan(&id, &loinc, &disp, &v, &unit, &flag, &source, &at); err != nil {
			rows.Close()
			return nil, err
		}
		cat := "laboratory"
		if source == "vitals" {
			cat = "vital-signs"
		}
		res := map[string]any{"resourceType": "Observation", "id": "obs-" + strconv.Itoa(id), "status": "final", "subject": ref,
			"category":          []any{map[string]any{"coding": []any{coding("http://terminology.hl7.org/CodeSystem/observation-category", cat, cat)}}},
			"code":              map[string]any{"coding": []any{coding(sysLOINC, loinc, disp)}, "text": disp},
			"effectiveDateTime": at.Format(time.RFC3339)}
		if v != nil {
			res["valueQuantity"] = map[string]any{"value": *v, "unit": unit, "system": "http://unitsofmeasure.org", "code": unit}
		}
		if flag != "" && flag != "N" {
			res["interpretation"] = []any{map[string]any{"coding": []any{coding("http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation", flag, flag)}}}
		}
		entries = append(entries, map[string]any{"resource": res})
	}
	rows.Close()

	meds, err := a.medications(ctx, "patient_id=$1", patientID)
	if err != nil {
		return nil, err
	}
	for _, m := range meds {
		status := map[string]string{"active": "active", "on-hold": "on-hold", "cancelled": "cancelled", "completed": "completed", "dispensed": "active"}[m.Status]
		res := map[string]any{"resourceType": "MedicationRequest", "id": "mr-" + strconv.Itoa(m.ID), "status": status, "intent": "order", "subject": ref,
			"medicationCodeableConcept": map[string]any{"coding": []any{coding(sysRxNorm, m.RxCUI, m.Drug)}, "text": fmt.Sprintf("%s %s mg", m.Drug, trimFloat(m.StrengthMg))},
			"authoredOn":                m.CreatedAt.Format(time.RFC3339),
			"requester":                 map[string]string{"reference": "Practitioner/" + derefOr(m.Prescriber, "unknown")},
			"dosageInstruction":         []any{map[string]any{"text": fmt.Sprintf("%d unit(s) %s %s for %d days", m.DoseUnits, m.Frequency, m.Route, m.DurationDays)}}}
		if m.HoldReason != nil {
			res["statusReason"] = map[string]any{"text": *m.HoldReason}
		}
		entries = append(entries, map[string]any{"resource": res})
	}

	erows, err := a.db.Query(ctx, `SELECT id, kind, coalesce(reason,''), coalesce(facility_id,''), started_at, ended_at FROM clinical.encounters WHERE patient_id=$1 AND ($2='' OR facility_id=$2)`, patientID, facilityFilter)
	if err != nil {
		return nil, err
	}
	for erows.Next() {
		var id int
		var kind, reason, fac string
		var start time.Time
		var end *time.Time
		if err := erows.Scan(&id, &kind, &reason, &fac, &start, &end); err != nil {
			erows.Close()
			return nil, err
		}
		class := map[string]string{"inpatient": "IMP", "outpatient": "AMB", "emergency": "EMER"}[kind]
		period := map[string]string{"start": start.Format(time.RFC3339)}
		status := "in-progress"
		if end != nil {
			period["end"] = end.Format(time.RFC3339)
			status = "finished"
		}
		entries = append(entries, map[string]any{"resource": map[string]any{"resourceType": "Encounter", "id": "enc-" + strconv.Itoa(id), "status": status,
			"class": coding("http://terminology.hl7.org/CodeSystem/v3-ActCode", class, kind), "subject": ref, "period": period,
			"reasonCode": []any{map[string]string{"text": reason}}, "serviceProvider": map[string]string{"reference": "Organization/" + fac}}})
	}
	erows.Close()

	return map[string]any{"resourceType": "Bundle", "type": "collection", "timestamp": time.Now().UTC().Format(time.RFC3339), "total": len(entries), "entry": entries}, nil
}

func (a *App) patientFHIR(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	id := chi.URLParam(r, "id")
	if err := a.authorizePatient(r.Context(), p, id, "read"); err != nil {
		writeErr(w, err)
		return
	}
	b, err := a.fhirBundle(r.Context(), id, "")
	if err != nil {
		writeErr(w, err)
		return
	}
	w.Header().Set("Content-Type", "application/fhir+json")
	writeJSON(w, 200, b)
}

func (a *App) capabilityStatement(w http.ResponseWriter, r *http.Request) {
	resources := []map[string]any{}
	for _, t := range []string{"Patient", "Encounter", "Observation", "Condition", "MedicationRequest", "AllergyIntolerance", "DiagnosticReport"} {
		resources = append(resources, map[string]any{"type": t, "interaction": []map[string]string{{"code": "read"}, {"code": "search-type"}}})
	}
	writeJSON(w, 200, map[string]any{"resourceType": "CapabilityStatement", "status": "active", "fhirVersion": "4.0.1", "kind": "instance",
		"format": []string{"json"}, "software": map[string]string{"name": "OmniMed AI Clinical Service"},
		"rest": []any{map[string]any{"mode": "server", "resource": resources}}})
}

func itoa(i int) string { return strconv.Itoa(i) }

func trimFloat(f float64) string { return strconv.FormatFloat(f, 'f', -1, 64) }
