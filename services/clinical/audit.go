package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/minio/minio-go/v7"
)

const genesisHash = "GENESIS"

var wormBucket = env("AUDIT_BUCKET", "audit-worm")

type AuditEntry struct {
	Actor      string         `json:"actor"`
	Role       string         `json:"role"`
	Action     string         `json:"action"`
	Resource   string         `json:"resource"`
	ResourceID string         `json:"resource_id"`
	Facility   string         `json:"facility"`
	Outcome    string         `json:"outcome"`
	Detail     map[string]any `json:"detail"`
}

func chainHash(prev, at string, e AuditEntry, detail string) string {
	h := sha256.New()
	for _, part := range []string{prev, at, e.Actor, e.Role, e.Action, e.Resource, e.ResourceID, e.Facility, e.Outcome, detail} {
		h.Write([]byte(part))
		h.Write([]byte{0x1f})
	}
	return hex.EncodeToString(h.Sum(nil))
}

// audit appends to the hash-chained ledger. Failures are logged, never swallowed silently.
func (a *App) audit(ctx context.Context, e AuditEntry) {
	if err := a.appendAudit(ctx, e); err != nil {
		log.Printf("AUDIT WRITE FAILED: %v (%+v)", err, e)
	}
}

func (a *App) appendAudit(ctx context.Context, e AuditEntry) error {
	if e.Detail == nil {
		e.Detail = map[string]any{}
	}
	detailBytes, _ := json.Marshal(e.Detail)
	detail := string(detailBytes)
	tx, err := a.db.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if _, err := tx.Exec(ctx, `SELECT pg_advisory_xact_lock(733100)`); err != nil {
		return err
	}
	prev := genesisHash
	_ = tx.QueryRow(ctx, `SELECT hash FROM audit.ledger ORDER BY seq DESC LIMIT 1`).Scan(&prev)
	at := time.Now().UTC().Format(time.RFC3339Nano)
	hash := chainHash(prev, at, e, detail)
	if _, err := tx.Exec(ctx, `INSERT INTO audit.ledger(at_raw, actor, role, action, resource, resource_id, facility, outcome, detail, prev_hash, hash)
		VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)`, at, e.Actor, e.Role, e.Action, e.Resource, e.ResourceID, e.Facility, e.Outcome, detail, prev, hash); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func ensureWormBucket(ctx context.Context, c *minio.Client) error {
	ok, err := c.BucketExists(ctx, wormBucket)
	if err != nil {
		return err
	}
	if !ok {
		if err := c.MakeBucket(ctx, wormBucket, minio.MakeBucketOptions{ObjectLocking: true}); err != nil {
			return err
		}
	}
	return nil
}

type ledgerRow struct {
	Seq        int64  `json:"seq"`
	At         string `json:"at"`
	Actor      string `json:"actor"`
	Role       string `json:"role"`
	Action     string `json:"action"`
	Resource   string `json:"resource"`
	ResourceID string `json:"resource_id"`
	Facility   string `json:"facility"`
	Outcome    string `json:"outcome"`
	Detail     string `json:"detail"`
	PrevHash   string `json:"prev_hash"`
	Hash       string `json:"hash"`
	Mirrored   bool   `json:"mirrored"`
}

const ledgerCols = `seq, at_raw, actor, coalesce(role,''), action, resource, coalesce(resource_id,''), coalesce(facility,''), outcome, detail, prev_hash, hash, mirrored`

func scanLedger(rows interface{ Scan(...any) error }) (ledgerRow, error) {
	var l ledgerRow
	err := rows.Scan(&l.Seq, &l.At, &l.Actor, &l.Role, &l.Action, &l.Resource, &l.ResourceID, &l.Facility, &l.Outcome, &l.Detail, &l.PrevHash, &l.Hash, &l.Mirrored)
	return l, err
}

// auditMirrorLoop copies ledger rows to an object-locked (WORM) bucket in compliance mode.
func (a *App) auditMirrorLoop(ctx context.Context) {
	t := time.NewTicker(2 * time.Second)
	defer t.Stop()
	for range t.C {
		rows, err := a.db.Query(ctx, `SELECT `+ledgerCols+` FROM audit.ledger WHERE NOT mirrored ORDER BY seq LIMIT 500`)
		if err != nil {
			log.Printf("audit mirror query: %v", err)
			continue
		}
		var batch []ledgerRow
		for rows.Next() {
			if l, err := scanLedger(rows); err == nil {
				batch = append(batch, l)
			}
		}
		rows.Close()
		for _, l := range batch {
			body, _ := json.Marshal(l)
			retain := time.Now().Add(7 * 24 * time.Hour)
			_, err := a.store.PutObject(ctx, wormBucket, fmt.Sprintf("ledger/%012d.json", l.Seq), bytes.NewReader(body), int64(len(body)),
				minio.PutObjectOptions{ContentType: "application/json", Mode: minio.Compliance, RetainUntilDate: retain})
			if err != nil {
				log.Printf("audit mirror put %d: %v", l.Seq, err)
				break
			}
			if _, err := a.db.Exec(ctx, `UPDATE audit.ledger SET mirrored=true WHERE seq=$1`, l.Seq); err != nil {
				log.Printf("audit mirror flag %d: %v", l.Seq, err)
				break
			}
		}
	}
}

func (a *App) listAudit(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	if err := requireRole(p, "admin"); err != nil {
		writeErr(w, err)
		return
	}
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	if limit <= 0 || limit > 1000 {
		limit = 200
	}
	q := `SELECT ` + ledgerCols + ` FROM audit.ledger`
	args := []any{}
	if o := r.URL.Query().Get("outcome"); o != "" {
		q += ` WHERE outcome=$1`
		args = append(args, o)
	}
	q += fmt.Sprintf(` ORDER BY seq DESC LIMIT %d`, limit)
	rows, err := a.db.Query(r.Context(), q, args...)
	if err != nil {
		writeErr(w, err)
		return
	}
	defer rows.Close()
	out := []ledgerRow{}
	for rows.Next() {
		l, err := scanLedger(rows)
		if err != nil {
			writeErr(w, err)
			return
		}
		out = append(out, l)
	}
	writeJSON(w, 200, out)
}

// verifyAudit recomputes the whole chain and cross-checks a sample against the WORM mirror.
func (a *App) verifyAudit(w http.ResponseWriter, r *http.Request) {
	p := principalFrom(r.Context())
	if err := requireRole(p, "admin"); err != nil {
		writeErr(w, err)
		return
	}
	ctx := r.Context()
	rows, err := a.db.Query(ctx, `SELECT `+ledgerCols+` FROM audit.ledger ORDER BY seq`)
	if err != nil {
		writeErr(w, err)
		return
	}
	defer rows.Close()
	prev := genesisHash
	count := 0
	var brokenAt int64
	var mirroredSample []ledgerRow
	for rows.Next() {
		l, err := scanLedger(rows)
		if err != nil {
			writeErr(w, err)
			return
		}
		count++
		e := AuditEntry{Actor: l.Actor, Role: l.Role, Action: l.Action, Resource: l.Resource, ResourceID: l.ResourceID, Facility: l.Facility, Outcome: l.Outcome}
		if l.PrevHash != prev || chainHash(prev, l.At, e, l.Detail) != l.Hash {
			brokenAt = l.Seq
			break
		}
		prev = l.Hash
		if l.Mirrored && len(mirroredSample) < 25 {
			mirroredSample = append(mirroredSample, l)
		}
	}
	mirrorMismatches := []int64{}
	for _, l := range mirroredSample {
		obj, err := a.store.GetObject(ctx, wormBucket, fmt.Sprintf("ledger/%012d.json", l.Seq), minio.GetObjectOptions{})
		if err != nil {
			mirrorMismatches = append(mirrorMismatches, l.Seq)
			continue
		}
		var m ledgerRow
		err = json.NewDecoder(obj).Decode(&m)
		obj.Close()
		if err != nil || m.Hash != l.Hash {
			mirrorMismatches = append(mirrorMismatches, l.Seq)
		}
	}
	writeJSON(w, 200, map[string]any{
		"valid": brokenAt == 0 && len(mirrorMismatches) == 0, "entries_checked": count, "broken_at_seq": brokenAt,
		"head_hash": prev, "worm_sampled": len(mirroredSample), "worm_mismatches": mirrorMismatches,
	})
}

func auditFromEvent(data []byte) (AuditEntry, error) {
	var e AuditEntry
	err := json.Unmarshal(data, &e)
	if e.Action == "" || !strings.Contains(e.Action, ".") && e.Resource == "" {
		return e, fmt.Errorf("invalid audit event")
	}
	return e, err
}
