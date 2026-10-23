package main

import (
	"bytes"
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"sync"
	"time"
)

const transitKey = "omnimed-phi"

// Vault wraps HashiCorp Vault's transit engine as the KMS for envelope encryption:
// each patient row gets its own DEK, and only the Vault-wrapped DEK is stored.
type Vault struct {
	addr, token string
	hc          *http.Client
	cache       sync.Map // wrapped DEK -> cachedKey
}

type cachedKey struct {
	key []byte
	exp time.Time
}

func NewVault(addr, token string) *Vault {
	return &Vault{addr: addr, token: token, hc: &http.Client{Timeout: 5 * time.Second}}
}

func (v *Vault) call(method, path string, body any, out any) (int, error) {
	var rdr io.Reader
	if body != nil {
		b, _ := json.Marshal(body)
		rdr = bytes.NewReader(b)
	}
	req, _ := http.NewRequest(method, v.addr+path, rdr)
	req.Header.Set("X-Vault-Token", v.token)
	req.Header.Set("Content-Type", "application/json")
	resp, err := v.hc.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(resp.Body)
	if out != nil && len(data) > 0 {
		_ = json.Unmarshal(data, out)
	}
	if resp.StatusCode >= 300 {
		return resp.StatusCode, fmt.Errorf("vault %s %s: %d %s", method, path, resp.StatusCode, string(data))
	}
	return resp.StatusCode, nil
}

func (v *Vault) Init() error {
	var mounts map[string]any
	if _, err := v.call("GET", "/v1/sys/mounts", nil, &mounts); err != nil {
		return err
	}
	if _, ok := mounts["transit/"]; !ok {
		if _, err := v.call("POST", "/v1/sys/mounts/transit", map[string]string{"type": "transit"}, nil); err != nil {
			return err
		}
	}
	if code, err := v.call("GET", "/v1/transit/keys/"+transitKey, nil, nil); err != nil && code != 404 {
		return err
	} else if code == 404 {
		if _, err := v.call("POST", "/v1/transit/keys/"+transitKey, map[string]string{"type": "aes256-gcm96"}, nil); err != nil {
			return err
		}
	}
	log.Printf("vault transit key %s ready", transitKey)
	return nil
}

func (v *Vault) NewDataKey() ([]byte, string, error) {
	var out struct {
		Data struct {
			Plaintext  string `json:"plaintext"`
			Ciphertext string `json:"ciphertext"`
		} `json:"data"`
	}
	if _, err := v.call("POST", "/v1/transit/datakey/plaintext/"+transitKey, map[string]any{"bits": 256}, &out); err != nil {
		return nil, "", err
	}
	key, err := base64.StdEncoding.DecodeString(out.Data.Plaintext)
	return key, out.Data.Ciphertext, err
}

func (v *Vault) Unwrap(wrapped string) ([]byte, error) {
	if c, ok := v.cache.Load(wrapped); ok && time.Now().Before(c.(cachedKey).exp) {
		return c.(cachedKey).key, nil
	}
	var out struct {
		Data struct {
			Plaintext string `json:"plaintext"`
		} `json:"data"`
	}
	if _, err := v.call("POST", "/v1/transit/decrypt/"+transitKey, map[string]string{"ciphertext": wrapped}, &out); err != nil {
		return nil, err
	}
	key, err := base64.StdEncoding.DecodeString(out.Data.Plaintext)
	if err != nil {
		return nil, err
	}
	v.cache.Store(wrapped, cachedKey{key, time.Now().Add(5 * time.Minute)})
	return key, nil
}

func seal(key, plaintext []byte) ([]byte, error) {
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, gcm.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}
	return gcm.Seal(nonce, nonce, plaintext, nil), nil
}

func open(key, blob []byte) ([]byte, error) {
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	if len(blob) < gcm.NonceSize() {
		return nil, fmt.Errorf("ciphertext too short")
	}
	return gcm.Open(nil, blob[:gcm.NonceSize()], blob[gcm.NonceSize():], nil)
}

type PHI struct {
	Name        string `json:"name"`
	Phone       string `json:"phone"`
	ABHANumber  string `json:"abha_number"`
	ABHAAddress string `json:"abha_address"`
}

func (a *App) encryptPHI(phi PHI) ([]byte, string, error) {
	key, wrapped, err := a.kms.NewDataKey()
	if err != nil {
		return nil, "", err
	}
	b, _ := json.Marshal(phi)
	blob, err := seal(key, b)
	return blob, wrapped, err
}

func (a *App) decryptPHI(blob []byte, wrapped string) (PHI, error) {
	var phi PHI
	if blob == nil || wrapped == "" {
		return phi, fmt.Errorf("record not encrypted")
	}
	key, err := a.kms.Unwrap(wrapped)
	if err != nil {
		return phi, err
	}
	plain, err := open(key, blob)
	if err != nil {
		return phi, err
	}
	return phi, json.Unmarshal(plain, &phi)
}

// encryptSeedPHI moves plaintext seed identifiers into envelope-encrypted columns.
func (a *App) encryptSeedPHI(ctx context.Context) error {
	rows, err := a.db.Query(ctx, `SELECT id, seed_phi FROM clinical.patients WHERE seed_phi IS NOT NULL`)
	if err != nil {
		return err
	}
	type item struct {
		id  string
		phi PHI
	}
	var items []item
	for rows.Next() {
		var it item
		var raw []byte
		if err := rows.Scan(&it.id, &raw); err != nil {
			rows.Close()
			return err
		}
		_ = json.Unmarshal(raw, &it.phi)
		items = append(items, it)
	}
	rows.Close()
	for _, it := range items {
		blob, wrapped, err := a.encryptPHI(it.phi)
		if err != nil {
			return err
		}
		if _, err := a.db.Exec(ctx, `UPDATE clinical.patients SET phi_enc=$2, phi_dek=$3, seed_phi=NULL WHERE id=$1`, it.id, blob, wrapped); err != nil {
			return err
		}
	}
	if len(items) > 0 {
		log.Printf("encrypted PHI for %d seeded patients", len(items))
	}
	return nil
}
