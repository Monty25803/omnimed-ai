package main

import (
	"context"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"math/big"
	"net"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/jackc/pgx/v5"
)

// ---------------------------------------------------------------- JWKS

type JWKS struct {
	url  string
	mu   sync.RWMutex
	keys map[string]*rsa.PublicKey
	last time.Time
}

func NewJWKS(url string) *JWKS { return &JWKS{url: url, keys: map[string]*rsa.PublicKey{}} }

func (j *JWKS) refresh() error {
	resp, err := http.Get(j.url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	var body struct {
		Keys []struct {
			Kid string `json:"kid"`
			Kty string `json:"kty"`
			Use string `json:"use"`
			N   string `json:"n"`
			E   string `json:"e"`
		} `json:"keys"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		return err
	}
	keys := map[string]*rsa.PublicKey{}
	for _, k := range body.Keys {
		if k.Kty != "RSA" || (k.Use != "" && k.Use != "sig") {
			continue
		}
		n, err1 := base64.RawURLEncoding.DecodeString(k.N)
		e, err2 := base64.RawURLEncoding.DecodeString(k.E)
		if err1 != nil || err2 != nil {
			continue
		}
		keys[k.Kid] = &rsa.PublicKey{N: new(big.Int).SetBytes(n), E: int(new(big.Int).SetBytes(e).Int64())}
	}
	j.mu.Lock()
	j.keys, j.last = keys, time.Now()
	j.mu.Unlock()
	return nil
}

func (j *JWKS) Key(kid string) (*rsa.PublicKey, error) {
	j.mu.RLock()
	k, ok := j.keys[kid]
	stale := time.Since(j.last) > 10*time.Second
	j.mu.RUnlock()
	if ok {
		return k, nil
	}
	if stale {
		if err := j.refresh(); err != nil {
			return nil, err
		}
		j.mu.RLock()
		k, ok = j.keys[kid]
		j.mu.RUnlock()
		if ok {
			return k, nil
		}
	}
	return nil, fmt.Errorf("unknown signing key %q", kid)
}

// ---------------------------------------------------------------- principal

type Staff struct {
	Username    string  `json:"username"`
	Role        string  `json:"role"`
	DisplayName string  `json:"display_name"`
	FacilityID  *string `json:"facility_id"`
	HprID       *string `json:"hpr_id"`
	Seniority   *string `json:"seniority"`
	Specialty   *string `json:"specialty"`
	Ward        *string `json:"ward"`
	OnShift     bool    `json:"on_shift"`
	ShiftStart  string  `json:"shift_start"`
	ShiftEnd    string  `json:"shift_end"`
	PatientID   *string `json:"patient_id"`
	PharmacyID  *string `json:"pharmacy_id"`
}

type Principal struct {
	Username string   `json:"username"`
	Roles    []string `json:"roles"`
	Role     string   `json:"role"`
	Staff    *Staff   `json:"staff"`
	ClientIP string   `json:"client_ip"`
}

func (p *Principal) Facility() string {
	if p.Staff != nil && p.Staff.FacilityID != nil {
		return *p.Staff.FacilityID
	}
	return ""
}

func (p *Principal) Is(roles ...string) bool {
	for _, r := range roles {
		if p.Role == r {
			return true
		}
	}
	return false
}

type ctxKey struct{}

func principalFrom(ctx context.Context) *Principal {
	p, _ := ctx.Value(ctxKey{}).(*Principal)
	return p
}

var knownRoles = []string{"doctor", "nurse", "pharmacist", "lab", "mr", "patient", "admin"}

func (a *App) loadStaff(ctx context.Context, username string) (*Staff, error) {
	s := &Staff{}
	err := a.db.QueryRow(ctx, `SELECT username, role, display_name, facility_id, hpr_id, seniority, specialty, ward,
		on_shift, shift_start::text, shift_end::text, patient_id, pharmacy_id FROM clinical.staff WHERE username=$1`, username).
		Scan(&s.Username, &s.Role, &s.DisplayName, &s.FacilityID, &s.HprID, &s.Seniority, &s.Specialty, &s.Ward,
			&s.OnShift, &s.ShiftStart, &s.ShiftEnd, &s.PatientID, &s.PharmacyID)
	if err == pgx.ErrNoRows {
		return nil, nil
	}
	return s, err
}

// verifyToken validates signature, expiry and issuer, then binds the token to a staff record.
func (a *App) verifyToken(ctx context.Context, raw string) (*Principal, error) {
	tok, err := jwt.Parse(raw, func(t *jwt.Token) (any, error) {
		kid, _ := t.Header["kid"].(string)
		return a.jwks.Key(kid)
	}, jwt.WithValidMethods([]string{"RS256"}), jwt.WithExpirationRequired())
	if err != nil {
		return nil, fmt.Errorf("invalid token: %w", err)
	}
	claims := tok.Claims.(jwt.MapClaims)
	iss, _ := claims["iss"].(string)
	if !strings.HasSuffix(iss, a.cfg.IssuerSuffix) {
		return nil, fmt.Errorf("untrusted issuer")
	}
	azp, _ := claims["azp"].(string)
	if azp != "omnimed-web" && azp != "omnimed-mobile" {
		return nil, fmt.Errorf("untrusted client")
	}
	username, _ := claims["preferred_username"].(string)
	p := &Principal{Username: username}
	if ra, ok := claims["realm_access"].(map[string]any); ok {
		if roles, ok := ra["roles"].([]any); ok {
			for _, r := range roles {
				if s, ok := r.(string); ok {
					p.Roles = append(p.Roles, s)
				}
			}
		}
	}
	for _, k := range knownRoles {
		for _, r := range p.Roles {
			if r == k && p.Role == "" {
				p.Role = k
			}
		}
	}
	if p.Role == "" {
		return nil, fmt.Errorf("no OmniMed role on token")
	}
	staff, err := a.loadStaff(ctx, username)
	if err != nil {
		return nil, err
	}
	if staff == nil || staff.Role != p.Role {
		return nil, fmt.Errorf("identity not registered for role %s", p.Role)
	}
	p.Staff = staff
	return p, nil
}

func bearer(r *http.Request) string {
	h := r.Header.Get("Authorization")
	if strings.HasPrefix(h, "Bearer ") {
		return strings.TrimPrefix(h, "Bearer ")
	}
	return ""
}

func (a *App) clientAllowed(ip string) bool {
	if a.cfg.AllowedCIDRs == "" {
		return true
	}
	parsed := net.ParseIP(ip)
	for _, c := range strings.Split(a.cfg.AllowedCIDRs, ",") {
		_, n, err := net.ParseCIDR(strings.TrimSpace(c))
		if err == nil && parsed != nil && n.Contains(parsed) {
			return true
		}
	}
	return false
}

func (a *App) authenticate(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw := bearer(r)
		if raw == "" {
			a.audit(r.Context(), AuditEntry{Actor: "anonymous", Action: "authenticate", Resource: r.URL.Path, Outcome: "denied", Detail: map[string]any{"reason": "missing token"}})
			writeJSON(w, http.StatusForbidden, map[string]string{"error": "authentication required"})
			return
		}
		p, err := a.verifyToken(r.Context(), raw)
		if err != nil {
			a.audit(r.Context(), AuditEntry{Actor: "unknown", Action: "authenticate", Resource: r.URL.Path, Outcome: "denied", Detail: map[string]any{"reason": err.Error()}})
			writeJSON(w, http.StatusForbidden, map[string]string{"error": err.Error()})
			return
		}
		ip, _, _ := net.SplitHostPort(r.RemoteAddr)
		if ip == "" {
			ip = r.RemoteAddr
		}
		p.ClientIP = ip
		if !a.clientAllowed(ip) {
			a.audit(r.Context(), AuditEntry{Actor: p.Username, Role: p.Role, Action: "authenticate", Resource: r.URL.Path, Outcome: "denied", Detail: map[string]any{"reason": "network not allowed", "ip": ip}})
			writeJSON(w, http.StatusForbidden, map[string]string{"error": "access from this network is not permitted"})
			return
		}
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), ctxKey{}, p)))
	})
}

func (a *App) internalOnly(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Internal-Token") != a.cfg.InternalToken {
			writeJSON(w, http.StatusForbidden, map[string]string{"error": "internal endpoint"})
			return
		}
		next.ServeHTTP(w, r)
	})
}

func requireRole(p *Principal, roles ...string) error {
	if !p.Is(roles...) {
		return errf(http.StatusForbidden, fmt.Sprintf("role %s may not perform this action", p.Role))
	}
	return nil
}

func (a *App) me(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, 200, principalFrom(r.Context()))
}
