CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA clinical;
CREATE SCHEMA diag;
CREATE SCHEMA life;
CREATE SCHEMA ai;
CREATE SCHEMA audit;

-- ---------------------------------------------------------------- clinical
CREATE TABLE clinical.facilities (
  id text PRIMARY KEY,
  name text NOT NULL,
  hfr_id text,
  lat double precision NOT NULL,
  lon double precision NOT NULL
);

CREATE TABLE clinical.staff (
  username text PRIMARY KEY,
  role text NOT NULL,
  display_name text NOT NULL,
  facility_id text REFERENCES clinical.facilities(id),
  hpr_id text,
  seniority text,
  specialty text,
  ward text,
  on_shift boolean NOT NULL DEFAULT true,
  shift_start time NOT NULL DEFAULT '00:00',
  shift_end time NOT NULL DEFAULT '23:59:59',
  patient_id text,
  pharmacy_id text
);

CREATE TABLE clinical.patients (
  id text PRIMARY KEY,
  mrn text UNIQUE NOT NULL,
  facility_id text NOT NULL REFERENCES clinical.facilities(id),
  birth_date date NOT NULL,
  gender text NOT NULL,
  ward text,
  bed text,
  attending text REFERENCES clinical.staff(username),
  lat double precision,
  lon double precision,
  phi_enc bytea,
  phi_dek text,
  seed_phi jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE clinical.care_team (
  patient_id text REFERENCES clinical.patients(id),
  username text REFERENCES clinical.staff(username),
  member_role text NOT NULL,
  PRIMARY KEY (patient_id, username)
);

CREATE TABLE clinical.allergies (
  id serial PRIMARY KEY,
  patient_id text REFERENCES clinical.patients(id),
  substance text NOT NULL,
  drug_class text,
  snomed text,
  reaction text,
  severity text NOT NULL
);

CREATE TABLE clinical.conditions (
  id serial PRIMARY KEY,
  patient_id text REFERENCES clinical.patients(id),
  icd10 text,
  snomed text,
  display text NOT NULL,
  onset date,
  status text NOT NULL DEFAULT 'active'
);

CREATE TABLE clinical.encounters (
  id serial PRIMARY KEY,
  patient_id text REFERENCES clinical.patients(id),
  kind text NOT NULL,
  facility_id text REFERENCES clinical.facilities(id),
  practitioner text,
  reason text,
  started_at timestamptz NOT NULL,
  ended_at timestamptz,
  note jsonb
);

CREATE TABLE clinical.observations (
  id serial PRIMARY KEY,
  patient_id text REFERENCES clinical.patients(id),
  loinc text NOT NULL,
  display text NOT NULL,
  value double precision,
  unit text,
  flag text,
  source text NOT NULL,
  effective_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON clinical.observations (patient_id, loinc, effective_at DESC);

CREATE TABLE clinical.vitals (
  id serial PRIMARY KEY,
  patient_id text REFERENCES clinical.patients(id),
  hr int, sbp int, dbp int, rr int,
  temp double precision, spo2 int, gcs int,
  mews int NOT NULL, qsofa int NOT NULL,
  red_alert boolean NOT NULL DEFAULT false,
  recorded_by text,
  recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON clinical.vitals (patient_id, recorded_at DESC);

CREATE TABLE clinical.drugs (
  rxcui text PRIMARY KEY,
  ingredient text NOT NULL,
  drug_class text[] NOT NULL DEFAULT '{}',
  max_daily_mg double precision,
  renal_min_egfr double precision,
  raises_potassium boolean NOT NULL DEFAULT false
);

CREATE TABLE clinical.interactions (
  a text NOT NULL,
  b text NOT NULL,
  severity text NOT NULL,
  effect text NOT NULL,
  PRIMARY KEY (a, b)
);

CREATE TABLE clinical.pharmacies (
  id text PRIMARY KEY,
  name text NOT NULL,
  facility_id text REFERENCES clinical.facilities(id),
  lat double precision NOT NULL,
  lon double precision NOT NULL
);

CREATE TABLE clinical.inventory (
  id serial PRIMARY KEY,
  pharmacy_id text REFERENCES clinical.pharmacies(id),
  rxcui text REFERENCES clinical.drugs(rxcui),
  brand text NOT NULL,
  strength_mg double precision NOT NULL,
  form text NOT NULL,
  is_generic boolean NOT NULL,
  bioequivalence_ok boolean NOT NULL DEFAULT true,
  unit_price numeric(10,2) NOT NULL,
  qty int NOT NULL,
  reorder_level int NOT NULL DEFAULT 20,
  barcode text UNIQUE NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE clinical.medication_requests (
  id serial PRIMARY KEY,
  patient_id text REFERENCES clinical.patients(id),
  rxcui text REFERENCES clinical.drugs(rxcui),
  drug text NOT NULL,
  strength_mg double precision NOT NULL,
  dose_units int NOT NULL DEFAULT 1,
  route text NOT NULL DEFAULT 'oral',
  frequency text NOT NULL,
  duration_days int NOT NULL DEFAULT 30,
  status text NOT NULL DEFAULT 'active',
  hold_reason text,
  prescriber text,
  facility_id text,
  override_reason text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE clinical.dispenses (
  id serial PRIMARY KEY,
  med_request_id int REFERENCES clinical.medication_requests(id),
  inventory_id int REFERENCES clinical.inventory(id),
  qty int NOT NULL,
  dispensed_by text,
  dispensed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE clinical.refill_history (
  id serial PRIMARY KEY,
  patient_id text REFERENCES clinical.patients(id),
  rxcui text,
  filled_at timestamptz NOT NULL,
  days_supply int NOT NULL
);

CREATE TABLE clinical.outbox (
  id serial PRIMARY KEY,
  patient_id text,
  channel text NOT NULL,
  destination text,
  message text NOT NULL,
  status text NOT NULL DEFAULT 'queued',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE clinical.emar (
  id serial PRIMARY KEY,
  med_request_id int REFERENCES clinical.medication_requests(id),
  scheduled_at timestamptz NOT NULL,
  administered_at timestamptz,
  administered_by text,
  status text NOT NULL,
  override_reason text,
  UNIQUE (med_request_id, scheduled_at)
);

CREATE TABLE clinical.alerts (
  id serial PRIMARY KEY,
  patient_id text,
  kind text NOT NULL,
  severity text NOT NULL,
  message text NOT NULL,
  recipient text NOT NULL,
  tier int NOT NULL DEFAULT 0,
  status text NOT NULL DEFAULT 'open',
  payload jsonb,
  source_ts timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  acked_at timestamptz,
  escalated_from int
);
CREATE INDEX ON clinical.alerts (recipient, status);

CREATE TABLE clinical.tasks (
  id serial PRIMARY KEY,
  patient_id text,
  kind text NOT NULL,
  detail text,
  facility_id text,
  ward text,
  status text NOT NULL DEFAULT 'open',
  created_at timestamptz NOT NULL DEFAULT now(),
  done_by text,
  done_at timestamptz
);

CREATE TABLE clinical.availability (
  id serial PRIMARY KEY,
  practitioner text REFERENCES clinical.staff(username),
  starts_at timestamptz NOT NULL,
  duration_min int NOT NULL DEFAULT 15,
  mode text NOT NULL DEFAULT 'any',
  booked boolean NOT NULL DEFAULT false,
  UNIQUE (practitioner, starts_at)
);

CREATE TABLE clinical.appointments (
  id serial PRIMARY KEY,
  slot_id int REFERENCES clinical.availability(id),
  patient_id text REFERENCES clinical.patients(id),
  practitioner text,
  mode text NOT NULL,
  reason text,
  video_url text,
  status text NOT NULL DEFAULT 'booked',
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE clinical.consents (
  id serial PRIMARY KEY,
  patient_id text REFERENCES clinical.patients(id),
  grantee_facility text REFERENCES clinical.facilities(id),
  purpose text NOT NULL,
  status text NOT NULL,
  artefact jsonb,
  expires_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE clinical.locker_links (
  id serial PRIMARY KEY,
  patient_id text REFERENCES clinical.patients(id),
  abha_address text NOT NULL,
  hip_id text NOT NULL,
  hip_name text NOT NULL,
  care_context text NOT NULL,
  linked_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- audit
CREATE TABLE audit.ledger (
  seq bigserial PRIMARY KEY,
  at_raw text NOT NULL,
  actor text NOT NULL,
  role text,
  action text NOT NULL,
  resource text NOT NULL,
  resource_id text,
  facility text,
  outcome text NOT NULL,
  detail text NOT NULL DEFAULT '{}',
  prev_hash text NOT NULL,
  hash text NOT NULL,
  mirrored boolean NOT NULL DEFAULT false
);

CREATE FUNCTION audit.block_mutation() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'UPDATE' AND NEW.mirrored AND NOT OLD.mirrored
     AND (to_jsonb(NEW) - 'mirrored') = (to_jsonb(OLD) - 'mirrored') THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'audit ledger is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER ledger_append_only BEFORE UPDATE OR DELETE ON audit.ledger
  FOR EACH ROW EXECUTE FUNCTION audit.block_mutation();

-- ---------------------------------------------------------------- diagnostics
CREATE TABLE diag.lab_results (
  id serial PRIMARY KEY,
  patient_id text NOT NULL,
  loinc text NOT NULL,
  analyte text NOT NULL,
  value double precision NOT NULL,
  unit text,
  ref_low double precision,
  ref_high double precision,
  flag text NOT NULL,
  instrument text,
  message_id text,
  protocol text NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE diag.reports (
  id serial PRIMARY KEY,
  patient_id text NOT NULL,
  title text NOT NULL,
  result_ids int[] NOT NULL,
  status text NOT NULL DEFAULT 'draft',
  created_at timestamptz NOT NULL DEFAULT now(),
  signed_by text,
  signed_at timestamptz,
  sha256 text,
  signature text,
  pdf_key text
);

CREATE TABLE diag.annotations (
  id serial PRIMARY KEY,
  instance_id text NOT NULL,
  frame int NOT NULL DEFAULT 0,
  plugin text NOT NULL,
  result jsonb NOT NULL,
  created_by text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE diag.study_patients (
  study_uid text PRIMARY KEY,
  patient_id text NOT NULL,
  description text,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- life sciences
CREATE TABLE life.clinics (
  id text PRIMARY KEY,
  name text NOT NULL,
  doctor text NOT NULL,
  specialty text NOT NULL,
  lat double precision NOT NULL,
  lon double precision NOT NULL,
  radius_m double precision NOT NULL
);

CREATE TABLE life.doctor_profiles (
  doctor text PRIMARY KEY,
  specialty text NOT NULL,
  interests text[] NOT NULL DEFAULT '{}',
  publication_topics text[] NOT NULL DEFAULT '{}',
  practice_mix jsonb NOT NULL DEFAULT '{}',
  detailing_consent boolean NOT NULL DEFAULT false,
  consent_at timestamptz
);

CREATE TABLE life.checkins (
  id serial PRIMARY KEY,
  rep text NOT NULL,
  clinic_id text REFERENCES life.clinics(id),
  lat double precision NOT NULL,
  lon double precision NOT NULL,
  accuracy_m double precision,
  distance_m double precision NOT NULL,
  accepted boolean NOT NULL,
  started_at timestamptz NOT NULL DEFAULT now(),
  ended_at timestamptz
);

CREATE TABLE life.sample_stock (
  rep text NOT NULL,
  product text NOT NULL,
  lot text NOT NULL,
  qty int NOT NULL,
  PRIMARY KEY (rep, product)
);

CREATE TABLE life.sample_ledger (
  id serial PRIMARY KEY,
  rep text NOT NULL,
  clinic_id text,
  checkin_id int REFERENCES life.checkins(id),
  doctor text NOT NULL,
  product text NOT NULL,
  lot text NOT NULL,
  qty int NOT NULL,
  signature_png text NOT NULL,
  signature_sha256 text NOT NULL,
  at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE life.assets (
  id text PRIMARY KEY,
  title text NOT NULL,
  kind text NOT NULL,
  product text,
  tags text[] NOT NULL DEFAULT '{}',
  status text NOT NULL,
  mlr_ref text,
  source text,
  slides jsonb NOT NULL
);

CREATE TABLE life.engagement (
  id serial PRIMARY KEY,
  rep text NOT NULL,
  checkin_id int REFERENCES life.checkins(id),
  asset_id text REFERENCES life.assets(id),
  slide int NOT NULL,
  dwell_ms int NOT NULL,
  at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE life.rep_slots (
  id serial PRIMARY KEY,
  doctor text NOT NULL,
  starts_at timestamptz NOT NULL,
  duration_min int NOT NULL DEFAULT 10,
  status text NOT NULL DEFAULT 'open',
  rep text,
  topic text,
  UNIQUE (doctor, starts_at)
);

-- ---------------------------------------------------------------- ai / ontology
CREATE TABLE ai.concepts (
  system text NOT NULL,
  code text NOT NULL,
  display text NOT NULL,
  synonyms text[] NOT NULL DEFAULT '{}',
  kind text NOT NULL,
  attrs jsonb NOT NULL DEFAULT '{}',
  PRIMARY KEY (system, code)
);

CREATE TABLE ai.guidelines (
  id serial PRIMARY KEY,
  condition_icd10 text NOT NULL,
  title text NOT NULL,
  source text NOT NULL,
  url text NOT NULL,
  body text NOT NULL,
  embedding vector(256)
);

CREATE TABLE ai.grounding_log (
  id serial PRIMARY KEY,
  at timestamptz NOT NULL DEFAULT now(),
  source text NOT NULL,
  accepted boolean NOT NULL,
  rejected jsonb NOT NULL DEFAULT '[]',
  generator text NOT NULL
);
