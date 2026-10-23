package main

import (
	"bytes"
	"crypto/rand"
	"testing"
	"time"
)

func ip(i int) *int           { return &i }
func fp(f float64) *float64   { return &f }

func TestScoreVitalsNormal(t *testing.T) {
	s := ScoreVitals(VitalsInput{HR: ip(80), SBP: ip(120), RR: ip(14), Temp: fp(36.8), SpO2: ip(98), GCS: ip(15)})
	if s.MEWS != 0 || s.QSOFA != 0 || s.RedAlert {
		t.Fatalf("expected normal score, got %+v", s)
	}
}

func TestScoreVitalsRedAlertByMEWS(t *testing.T) {
	// HR 115 (2) + RR 24 (2) + temp 38.9 (2) = 6
	s := ScoreVitals(VitalsInput{HR: ip(115), SBP: ip(130), RR: ip(24), Temp: fp(38.9), GCS: ip(15)})
	if s.MEWS != 6 || !s.RedAlert {
		t.Fatalf("expected MEWS 6 red alert, got %+v", s)
	}
}

func TestScoreVitalsRedAlertByQSOFA(t *testing.T) {
	s := ScoreVitals(VitalsInput{HR: ip(90), SBP: ip(98), RR: ip(22), Temp: fp(37), GCS: ip(15)})
	if s.QSOFA != 2 || !s.RedAlert {
		t.Fatalf("expected qSOFA 2 red alert, got %+v", s)
	}
}

func TestHaversine(t *testing.T) {
	d := haversineMiles(12.9716, 77.5946, 12.9698, 77.7500)
	if d < 10 || d > 11 {
		t.Fatalf("expected about 10.5 miles, got %v", d)
	}
}

func TestSealOpen(t *testing.T) {
	key := make([]byte, 32)
	_, _ = rand.Read(key)
	blob, err := seal(key, []byte("Lakshmi Iyer"))
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(blob, []byte("Lakshmi")) {
		t.Fatal("plaintext leaked into ciphertext")
	}
	out, err := open(key, blob)
	if err != nil || string(out) != "Lakshmi Iyer" {
		t.Fatalf("round trip failed: %v %q", err, out)
	}
	blob[len(blob)-1] ^= 1
	if _, err := open(key, blob); err == nil {
		t.Fatal("tampered ciphertext must not decrypt")
	}
}

func TestChainHashDetectsTamper(t *testing.T) {
	e := AuditEntry{Actor: "dr.rao", Action: "phi.read", Resource: "Patient", ResourceID: "P0001", Outcome: "allowed"}
	h1 := chainHash("GENESIS", "t", e, "{}")
	e.Outcome = "denied"
	if chainHash("GENESIS", "t", e, "{}") == h1 {
		t.Fatal("hash must change when a field changes")
	}
}

func TestAdherenceRisk(t *testing.T) {
	now := time.Now()
	type fill = struct {
		at   time.Time
		days int
	}
	good := []fill{{now.AddDate(0, 0, -90), 30}, {now.AddDate(0, 0, -60), 30}, {now.AddDate(0, 0, -30), 30}}
	pdc, late, _ := computeRefillRisk(good, now)
	if pdc < 0.95 || late > 0.5 {
		t.Fatalf("regular refills should be adherent: pdc=%v late=%v", pdc, late)
	}
	poor := []fill{{now.AddDate(0, 0, -150), 30}, {now.AddDate(0, 0, -100), 30}, {now.AddDate(0, 0, -45), 30}}
	pdc2, late2, runOut := computeRefillRisk(poor, now)
	daysOut := int(runOut.Sub(now).Hours() / 24)
	if adherenceRisk(pdc2, late2, daysOut) <= adherenceRisk(pdc, late, 0) {
		t.Fatal("irregular refills must score higher risk")
	}
}

func TestNarrowClasses(t *testing.T) {
	got := narrowClasses([]string{"macrolide", "antibiotic"})
	if len(got) != 1 || got[0] != "macrolide" {
		t.Fatalf("got %v", got)
	}
}

func TestWithinShift(t *testing.T) {
	loc := ist()
	at := time.Date(2026, 1, 1, 23, 0, 0, 0, loc)
	if !withinShift("20:00:00", "08:00:00", at) {
		t.Fatal("overnight shift should include 23:00")
	}
	if withinShift("08:00:00", "20:00:00", at) {
		t.Fatal("day shift should exclude 23:00")
	}
}
