package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

var aiClient = &http.Client{Timeout: 10 * time.Second}

// noteProposal flattens every coded or free-text clinical claim in a SOAP note into the
// shape the AI guardrail grounds: diagnoses, medications, tests and narrative sections.
func noteProposal(note map[string]any) map[string]any {
	list := func(v any) []any {
		if l, ok := v.([]any); ok {
			return l
		}
		return nil
	}
	obj := func(v any) map[string]any {
		if m, ok := v.(map[string]any); ok {
			return m
		}
		return map[string]any{}
	}
	plan, subj := obj(note["plan"]), obj(note["subjective"])

	assessment := append([]any{}, list(note["assessment"])...)
	for _, h := range list(subj["history"]) {
		// Allergies are SNOMED findings without an ICD-10 pairing; the guardrail grounds diagnoses only.
		if hm := obj(h); hm["icd10"] != nil {
			assessment = append(assessment, map[string]any{"display": hm["display"], "snomed": hm["snomed"], "icd10": hm["icd10"]})
		}
	}
	meds := append([]any{}, list(plan["medications"])...)
	for _, m := range list(subj["reported_medications"]) {
		mm := obj(m)
		meds = append(meds, map[string]any{"drug": mm["drug"], "rxcui": mm["rxcui"], "action": "continue"})
	}
	return map[string]any{"assessment": assessment, "medications": meds, "tests": list(plan["tests"]), "narrative": note["narrative"]}
}

// groundNote re-runs the guardrail server-side; the client's own grounding flag is never trusted.
func (a *App) groundNote(ctx context.Context, authorization string, note map[string]any) (bool, []any, error) {
	body, _ := json.Marshal(map[string]any{"proposal": noteProposal(note)})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, a.cfg.AIURL+"/guardrail/check", bytes.NewReader(body))
	if err != nil {
		return false, nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", authorization)
	resp, err := aiClient.Do(req)
	if err != nil {
		return false, nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return false, nil, fmt.Errorf("guardrail returned %d", resp.StatusCode)
	}
	var out struct {
		Accepted bool  `json:"accepted"`
		Rejected []any `json:"rejected"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return false, nil, err
	}
	return out.Accepted, out.Rejected, nil
}
