package main

import (
	"context"
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"os"
	"strconv"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"
	"github.com/nats-io/nats.go"
	"github.com/redis/go-redis/v9"
)

type Config struct {
	Port              string
	DatabaseURL       string
	RedisURL          string
	NatsURL           string
	JWKSURL           string
	IssuerSuffix      string
	InternalToken     string
	VaultAddr         string
	VaultToken        string
	MinioEndpoint     string
	MinioAccessKey    string
	MinioSecretKey    string
	MinioSecure       bool
	AIURL             string
	EscalationTimeout time.Duration
	AllowedCIDRs      string
}

type App struct {
	cfg   Config
	db    *pgxpool.Pool
	rdb   *redis.Client
	nc    *nats.Conn
	js    nats.JetStreamContext
	kms   *Vault
	store *minio.Client
	jwks  *JWKS
}

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func loadConfig() Config {
	timeout, _ := strconv.Atoi(env("ESCALATION_TIMEOUT_SECONDS", "120"))
	return Config{
		Port:              env("PORT", "8081"),
		DatabaseURL:       env("DATABASE_URL", "postgres://omnimed:omnimed@localhost:5432/omnimed"),
		RedisURL:          env("REDIS_URL", "redis://localhost:6379/0"),
		NatsURL:           env("NATS_URL", "nats://localhost:4222"),
		JWKSURL:           env("JWKS_URL", "http://localhost:8080/realms/omnimed/protocol/openid-connect/certs"),
		IssuerSuffix:      env("ISSUER_SUFFIX", "/realms/omnimed"),
		InternalToken:     env("INTERNAL_TOKEN", "dev-internal-token"),
		VaultAddr:         env("VAULT_ADDR", "http://localhost:8200"),
		VaultToken:        env("VAULT_TOKEN", "root"),
		MinioEndpoint:     env("MINIO_ENDPOINT", "localhost:9000"),
		MinioAccessKey:    env("MINIO_ACCESS_KEY", "omnimed"),
		MinioSecretKey:    env("MINIO_SECRET_KEY", "omnimed-secret"),
		MinioSecure:       env("MINIO_SECURE", "false") == "true",
		AIURL:             env("AI_URL", "http://localhost:8084"),
		EscalationTimeout: time.Duration(timeout) * time.Second,
		AllowedCIDRs:      env("ALLOWED_CIDRS", ""),
	}
}

func retry[T any](name string, fn func() (T, error)) T {
	var lastErr error
	for i := 0; i < 60; i++ {
		v, err := fn()
		if err == nil {
			return v
		}
		lastErr = err
		log.Printf("waiting for %s: %v", name, err)
		time.Sleep(2 * time.Second)
	}
	log.Fatalf("%s unavailable: %v", name, lastErr)
	var zero T
	return zero
}

func main() {
	cfg := loadConfig()
	ctx := context.Background()
	app := &App{cfg: cfg}

	app.db = retry("postgres", func() (*pgxpool.Pool, error) {
		p, err := pgxpool.New(ctx, cfg.DatabaseURL)
		if err != nil {
			return nil, err
		}
		return p, p.Ping(ctx)
	})
	opt, err := redis.ParseURL(cfg.RedisURL)
	if err != nil {
		log.Fatal(err)
	}
	app.rdb = redis.NewClient(opt)
	retry("redis", func() (string, error) { return app.rdb.Ping(ctx).Result() })

	app.nc = retry("nats", func() (*nats.Conn, error) {
		return nats.Connect(cfg.NatsURL, nats.Name("clinical"), nats.MaxReconnects(-1))
	})
	app.js, err = app.nc.JetStream()
	if err != nil {
		log.Fatal(err)
	}

	app.kms = NewVault(cfg.VaultAddr, cfg.VaultToken)
	retry("vault", func() (bool, error) { return true, app.kms.Init() })

	app.store = retry("minio", func() (*minio.Client, error) {
		c, err := minio.New(cfg.MinioEndpoint, &minio.Options{Creds: credentials.NewStaticV4(cfg.MinioAccessKey, cfg.MinioSecretKey, ""), Secure: cfg.MinioSecure})
		if err != nil {
			return nil, err
		}
		return c, ensureWormBucket(ctx, c)
	})

	app.jwks = NewJWKS(cfg.JWKSURL)

	retry("jetstream", func() (bool, error) { return true, app.ensureStream() })
	if err := app.encryptSeedPHI(ctx); err != nil {
		log.Fatalf("seed encryption: %v", err)
	}
	if err := app.startSynapse(); err != nil {
		log.Fatalf("synapse: %v", err)
	}
	go app.escalationLoop(ctx)
	go app.auditMirrorLoop(ctx)

	log.Printf("clinical service listening on :%s", cfg.Port)
	srv := &http.Server{Addr: ":" + cfg.Port, Handler: app.routes(), ReadHeaderTimeout: 10 * time.Second}
	log.Fatal(srv.ListenAndServe())
}

func (a *App) routes() http.Handler {
	r := chi.NewRouter()
	r.Use(middleware.RequestID, middleware.RealIP, middleware.Recoverer)

	r.Get("/health", func(w http.ResponseWriter, r *http.Request) { writeJSON(w, 200, map[string]string{"status": "ok"}) })
	r.Get("/fhir/metadata", a.capabilityStatement)

	r.Group(func(r chi.Router) {
		r.Use(a.internalOnly)
		r.Post("/internal/authz", a.internalAuthz)
	})

	r.Group(func(r chi.Router) {
		r.Use(a.authenticate)
		r.Get("/me", a.me)
		r.Get("/facilities", a.listFacilities)
		r.Get("/practitioners", a.listPractitioners)

		r.Get("/patients", a.listPatients)
		r.Get("/patients/{id}", a.getPatient)
		r.Get("/patients/{id}/timeline", a.patientTimeline)
		r.Get("/patients/{id}/fhir", a.patientFHIR)
		r.Post("/patients/{id}/vitals", a.recordVitals)
		r.Post("/patients/{id}/encounters", a.saveEncounterNote)

		r.Get("/ward/grid", a.wardGrid)
		r.Get("/tasks", a.listTasks)
		r.Post("/tasks/{id}/complete", a.completeTask)

		r.Post("/prescriptions/check", a.checkPrescription)
		r.Post("/prescriptions", a.createPrescription)
		r.Get("/prescriptions", a.listPrescriptions)
		r.Post("/prescriptions/{id}/hold", a.holdPrescription)
		r.Post("/prescriptions/{id}/release", a.releasePrescription)
		r.Post("/prescriptions/{id}/dispense", a.dispensePrescription)
		r.Get("/drugs", a.listDrugs)

		r.Get("/pharmacy/inventory", a.pharmacyInventory)
		r.Post("/pharmacy/inventory/sync", a.inventorySync)
		r.Get("/pharmacy/substitutes", a.substitutes)
		r.Get("/pharmacy/refill-risk", a.refillRisk)
		r.Post("/pharmacy/reminders/run", a.runReminders)
		r.Get("/outbox", a.listOutbox)

		r.Get("/emar", a.emarDue)
		r.Post("/emar/administer", a.emarAdminister)

		r.Get("/alerts", a.listAlerts)
		r.Get("/alerts/stream", a.alertStream)
		r.Post("/alerts/{id}/ack", a.ackAlert)

		r.Get("/availability", a.listAvailability)
		r.Get("/appointments", a.listAppointments)
		r.Post("/appointments", a.bookAppointment)
		r.Delete("/appointments/{id}", a.cancelAppointment)

		r.Post("/abdm/abha/verify", a.verifyABHA)
		r.Get("/abdm/registry/facilities", a.hfrRegistry)
		r.Get("/abdm/registry/professionals", a.hprRegistry)
		r.Get("/locker", a.locker)
		r.Post("/locker/link", a.lockerLink)
		r.Get("/consents", a.listConsents)
		r.Post("/consents", a.grantConsent)
		r.Post("/consents/{id}/revoke", a.revokeConsent)

		r.Get("/audit", a.listAudit)
		r.Get("/audit/verify", a.verifyAudit)
	})
	return r
}

// ---------------------------------------------------------------- helpers

type apiError struct {
	Status  int    `json:"-"`
	Message string `json:"error"`
	Details any    `json:"details,omitempty"`
}

func (e *apiError) Error() string { return e.Message }

func errf(status int, msg string) *apiError { return &apiError{Status: status, Message: msg} }

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func writeErr(w http.ResponseWriter, err error) {
	var ae *apiError
	if errors.As(err, &ae) {
		writeJSON(w, ae.Status, ae)
		return
	}
	log.Printf("internal error: %v", err)
	writeJSON(w, 500, map[string]string{"error": "internal error"})
}

func decode(r *http.Request, v any) error {
	dec := json.NewDecoder(http.MaxBytesReader(nil, r.Body, 1<<20))
	if err := dec.Decode(v); err != nil {
		return errf(400, "invalid JSON body: "+err.Error())
	}
	return nil
}
