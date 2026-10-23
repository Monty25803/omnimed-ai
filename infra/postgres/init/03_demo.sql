-- Synthetic demo data. No real patients. PHI in seed_phi is encrypted and
-- cleared by the clinical service on first start.

INSERT INTO clinical.facilities VALUES
 ('F1','OmniMed City Hospital, Bengaluru','IN2910000001',12.9716,77.5946),
 ('F2','Lakeside Clinic, Mysuru','IN2910000002',12.2958,76.6394);

INSERT INTO clinical.staff(username, role, display_name, facility_id, hpr_id, seniority, specialty, ward, patient_id, pharmacy_id) VALUES
 ('dr.rao','doctor','Dr. Anand Rao','F1','71-0001-0001-0001','attending','Internal Medicine','W3',NULL,NULL),
 ('dr.sen','doctor','Dr. Kavya Sen','F1','71-0001-0001-0002','resident','Internal Medicine','W3',NULL,NULL),
 ('dr.mehta','doctor','Dr. Rohit Mehta','F2','71-0001-0001-0003','attending','Cardiology',NULL,NULL,NULL),
 ('nurse.anita','nurse','Anita Joseph, RN','F1','71-0002-0001-0001','nurse',NULL,'W3',NULL,NULL),
 ('nurse.priya','nurse','Priya Menon, Charge Nurse','F1','71-0002-0001-0002','senior_nurse',NULL,'W3',NULL,NULL),
 ('pharm.kiran','pharmacist','Kiran Shetty, PharmD','F1','71-0003-0001-0001',NULL,NULL,NULL,NULL,'PH1'),
 ('lab.suresh','lab','Suresh Pillai, Lab Technologist','F1','71-0004-0001-0001',NULL,'Pathology',NULL,NULL,NULL),
 ('mr.vikram','mr','Vikram Desai (Field Representative)',NULL,NULL,NULL,NULL,NULL,NULL,NULL),
 ('patient.lakshmi','patient','Lakshmi Iyer',NULL,NULL,NULL,NULL,NULL,'P0001',NULL),
 ('admin.audit','admin','Audit Officer','F1',NULL,NULL,NULL,NULL,NULL,NULL);

INSERT INTO clinical.patients(id, mrn, facility_id, birth_date, gender, ward, bed, attending, lat, lon, seed_phi) VALUES
 ('P0001','MRN-100001','F1','1959-03-14','female','W3','12','dr.rao',12.9716,77.5946,
  '{"name":"Lakshmi Iyer","phone":"+91-90000-00001","abha_number":"91-1111-2222-3333","abha_address":"lakshmi.iyer@sbx"}'),
 ('P0002','MRN-100002','F1','1981-07-02','male','W3','14','dr.rao',12.9716,77.5946,
  '{"name":"Arjun Nair","phone":"+91-90000-00002","abha_number":"91-1111-2222-4444","abha_address":"arjun.nair@sbx"}'),
 ('P0003','MRN-100003','F1','1994-11-20','female',NULL,NULL,'dr.rao',12.9716,77.5946,
  '{"name":"Fatima Khan","phone":"+91-90000-00003","abha_number":"91-1111-2222-5555","abha_address":"fatima.khan@sbx"}'),
 ('P0004','MRN-100004','F1','1968-01-09','male','W3','15','dr.rao',12.9716,77.5946,
  '{"name":"Ravi Kumar","phone":"+91-90000-00004","abha_number":"91-1111-2222-6666","abha_address":"ravi.kumar@sbx"}'),
 ('P0101','MRN-200101','F2','1975-05-30','female',NULL,NULL,'dr.mehta',12.2958,76.6394,
  '{"name":"Meera Das","phone":"+91-90000-00101","abha_number":"91-1111-2222-7777","abha_address":"meera.das@sbx"}');

INSERT INTO clinical.care_team VALUES
 ('P0001','dr.rao','attending'),('P0001','dr.sen','resident'),('P0001','nurse.anita','nurse'),('P0001','nurse.priya','senior_nurse'),('P0001','pharm.kiran','pharmacist'),
 ('P0002','dr.rao','attending'),('P0002','dr.sen','resident'),('P0002','nurse.anita','nurse'),('P0002','nurse.priya','senior_nurse'),('P0002','pharm.kiran','pharmacist'),
 ('P0003','dr.rao','attending'),('P0003','pharm.kiran','pharmacist'),
 ('P0004','dr.rao','attending'),('P0004','dr.sen','resident'),('P0004','nurse.anita','nurse'),('P0004','nurse.priya','senior_nurse'),('P0004','pharm.kiran','pharmacist'),
 ('P0101','dr.mehta','attending');

INSERT INTO clinical.allergies(patient_id, substance, drug_class, snomed, reaction, severity) VALUES
 ('P0001','Penicillin','penicillin','91936005','Anaphylaxis (2014)','severe'),
 ('P0004','Sulfonamides','sulfonamide','91939003','Rash','moderate');

INSERT INTO clinical.conditions(patient_id, icd10, snomed, display, onset, status) VALUES
 ('P0001','I10','38341003','Essential hypertension','2009-06-01','active'),
 ('P0001','E11.9','44054006','Type 2 diabetes mellitus','2012-02-10','active'),
 ('P0001','N18.3','709044004','Chronic kidney disease stage 3','2021-09-15','active'),
 ('P0002','J18.9','233604007','Pneumonia', (now() - interval '2 days')::date,'active'),
 ('P0003','J45.909','195967001','Asthma','2005-01-01','active'),
 ('P0004','I50.9','84114007','Heart failure','2019-04-11','active'),
 ('P0004','I48.91','49436004','Atrial fibrillation','2019-04-11','active'),
 ('P0101','I10','38341003','Essential hypertension','2016-03-03','active');

INSERT INTO clinical.encounters(patient_id, kind, facility_id, practitioner, reason, started_at, ended_at) VALUES
 ('P0001','inpatient','F1','dr.rao','Admission: CKD with poorly controlled BP', now() - interval '3 days', NULL),
 ('P0001','outpatient','F1','dr.rao','Diabetes review', now() - interval '120 days', now() - interval '120 days' + interval '20 minutes'),
 ('P0001','inpatient','F1','dr.rao','Hospitalisation: hypertensive urgency', now() - interval '400 days', now() - interval '395 days'),
 ('P0002','inpatient','F1','dr.rao','Admission: community acquired pneumonia', now() - interval '2 days', NULL),
 ('P0003','outpatient','F1','dr.rao','Asthma review', now() - interval '60 days', now() - interval '60 days' + interval '15 minutes'),
 ('P0004','inpatient','F1','dr.rao','Admission: decompensated heart failure', now() - interval '1 day', NULL),
 ('P0101','outpatient','F2','dr.mehta','Hypertension follow-up', now() - interval '30 days', now() - interval '30 days' + interval '15 minutes');

INSERT INTO clinical.observations(patient_id, loinc, display, value, unit, flag, source, effective_at) VALUES
 ('P0001','2160-0','Creatinine',1.4,'mg/dL','H','lab', now() - interval '180 days'),
 ('P0001','2160-0','Creatinine',1.6,'mg/dL','H','lab', now() - interval '3 days'),
 ('P0001','33914-3','eGFR',48,'mL/min/1.73m2','L','lab', now() - interval '180 days'),
 ('P0001','33914-3','eGFR',42,'mL/min/1.73m2','L','lab', now() - interval '3 days'),
 ('P0001','2823-3','Potassium',4.9,'mmol/L','N','lab', now() - interval '180 days'),
 ('P0001','2823-3','Potassium',5.3,'mmol/L','H','lab', now() - interval '3 days'),
 ('P0001','4548-4','Hemoglobin A1c',7.8,'%','H','lab', now() - interval '120 days'),
 ('P0002','6690-2','WBC count',15.2,'10*3/uL','H','lab', now() - interval '2 days'),
 ('P0002','1988-5','C reactive protein',96,'mg/L','H','lab', now() - interval '2 days'),
 ('P0002','2524-7','Lactate',2.4,'mmol/L','H','lab', now() - interval '1 day'),
 ('P0004','33762-6','NT-proBNP',3400,'pg/mL','H','lab', now() - interval '1 day'),
 ('P0004','6301-6','INR',2.6,'','H','lab', now() - interval '1 day'),
 ('P0101','2823-3','Potassium',4.2,'mmol/L','N','lab', now() - interval '30 days');

INSERT INTO clinical.vitals(patient_id, hr, sbp, dbp, rr, temp, spo2, gcs, mews, qsofa, red_alert, recorded_by, recorded_at) VALUES
 ('P0001',82,158,94,16,36.8,97,15,1,0,false,'nurse.anita', now() - interval '8 hours'),
 ('P0001',86,162,96,17,36.9,96,15,1,0,false,'nurse.anita', now() - interval '2 hours'),
 ('P0002',98,118,72,20,38.1,95,15,1,0,false,'nurse.anita', now() - interval '12 hours'),
 ('P0002',108,106,64,23,38.6,93,15,5,1,true,'nurse.anita', now() - interval '6 hours'),
 ('P0002',114,102,60,24,38.9,91,15,5,1,true,'nurse.anita', now() - interval '1 hour'),
 ('P0004',92,112,70,20,36.7,94,15,1,0,false,'nurse.anita', now() - interval '4 hours');

INSERT INTO clinical.medication_requests(patient_id, rxcui, drug, strength_mg, frequency, duration_days, status, prescriber, facility_id, created_at) VALUES
 ('P0001','29046','lisinopril',10,'OD',90,'active','dr.rao','F1', now() - interval '60 days'),
 ('P0001','9997','spironolactone',25,'OD',90,'active','dr.rao','F1', now() - interval '20 days'),
 ('P0001','6809','metformin',500,'BD',90,'active','dr.rao','F1', now() - interval '200 days'),
 ('P0002','2193','ceftriaxone',1000,'OD',7,'active','dr.rao','F1', now() - interval '2 days'),
 ('P0002','18631','azithromycin',500,'OD',5,'active','dr.rao','F1', now() - interval '2 days'),
 ('P0003','435','albuterol',0.1,'PRN',90,'active','dr.rao','F1', now() - interval '60 days'),
 ('P0004','4603','furosemide',40,'BD',30,'active','dr.rao','F1', now() - interval '1 day'),
 ('P0004','11289','warfarin',5,'OD',30,'active','dr.rao','F1', now() - interval '300 days'),
 ('P0101','17767','amlodipine',5,'OD',90,'active','dr.mehta','F2', now() - interval '30 days');

INSERT INTO clinical.pharmacies VALUES
 ('PH1','OmniMed City Hospital Pharmacy','F1',12.9716,77.5946),
 ('PH2','MG Road Community Pharmacy',NULL,12.9756,77.6066),
 ('PH3','Indiranagar Care Pharmacy',NULL,12.9784,77.6408),
 ('PH4','Whitefield Wellness Pharmacy',NULL,12.9698,77.7500),
 ('PH5','Lakeside Clinic Pharmacy','F2',12.2958,76.6394);

INSERT INTO clinical.inventory(pharmacy_id, rxcui, brand, strength_mg, form, is_generic, unit_price, qty, reorder_level, barcode) VALUES
 ('PH1','29046','Zestril 10',10,'tablet',false,9.50,120,30,'8901000000011'),
 ('PH1','29046','Lisinopril 10 (generic)',10,'tablet',true,2.10,300,30,'8901000000012'),
 ('PH1','9997','Aldactone 25',25,'tablet',false,6.00,60,20,'8901000000021'),
 ('PH1','9997','Spironolactone 25 (generic)',25,'tablet',true,1.80,15,20,'8901000000022'),
 ('PH1','6809','Glycomet 500',500,'tablet',false,3.20,400,50,'8901000000031'),
 ('PH1','6809','Metformin 500 (generic)',500,'tablet',true,0.90,800,50,'8901000000032'),
 ('PH1','2193','Monocef 1g',1000,'injection',false,65.00,40,10,'8901000000041'),
 ('PH1','18631','Zithromax 500',500,'tablet',false,48.00,0,10,'8901000000051'),
 ('PH1','18631','Azithral 500',500,'tablet',false,22.00,0,10,'8901000000052'),
 ('PH1','435','Asthalin inhaler 100mcg',0.1,'inhaler',false,145.00,25,10,'8901000000061'),
 ('PH1','4603','Lasix 40',40,'tablet',false,4.00,200,30,'8901000000071'),
 ('PH1','11289','Warfarin 5 (generic)',5,'tablet',true,3.00,90,30,'8901000000081'),
 ('PH1','723','Novamox 500',500,'capsule',false,8.00,150,30,'8901000000091'),
 ('PH1','161','Dolo 650',650,'tablet',false,2.00,1000,100,'8901000000101'),
 ('PH1','8591','K-Cl 600',600,'tablet',false,1.50,200,30,'8901000000111'),
 ('PH1','40790','Pan 40',40,'tablet',false,7.00,300,30,'8901000000121'),
 ('PH1','3640','Doxy 100',100,'capsule',false,5.50,0,20,'8901000000131'),
 ('PH1','17767','Amlong 5',5,'tablet',false,3.00,250,30,'8901000000141'),
 ('PH1','52175','Losartan 50 (generic)',50,'tablet',true,2.50,200,30,'8901000000151'),
 ('PH2','18631','Azithromycin 500 (generic)',500,'tablet',true,9.00,60,10,'8901000000201'),
 ('PH2','723','Amoxicillin 500 (generic)',500,'capsule',true,3.00,100,20,'8901000000202'),
 ('PH2','9997','Spironolactone 25 (generic)',25,'tablet',true,1.70,80,20,'8901000000203'),
 ('PH3','2551','Ciplox 500',500,'tablet',false,6.50,70,20,'8901000000301'),
 ('PH3','18631','Azithral 500',500,'tablet',false,21.00,5,10,'8901000000302'),
 ('PH4','3640','Doxycycline 100 (generic)',100,'capsule',true,2.00,120,20,'8901000000401'),
 ('PH5','17767','Amlodipine 5 (generic)',5,'tablet',true,1.00,300,30,'8901000000501');

INSERT INTO clinical.refill_history(patient_id, rxcui, filled_at, days_supply) VALUES
 ('P0001','29046', now() - interval '150 days',30),('P0001','29046', now() - interval '118 days',30),
 ('P0001','29046', now() - interval '80 days',30),('P0001','29046', now() - interval '38 days',30),
 ('P0001','6809', now() - interval '150 days',30),('P0001','6809', now() - interval '121 days',30),
 ('P0001','6809', now() - interval '90 days',30),('P0001','6809', now() - interval '61 days',30),('P0001','6809', now() - interval '31 days',30),
 ('P0003','435', now() - interval '200 days',60),('P0003','435', now() - interval '95 days',60),
 ('P0004','11289', now() - interval '90 days',30),('P0004','11289', now() - interval '60 days',30),('P0004','11289', now() - interval '29 days',30),
 ('P0101','17767', now() - interval '65 days',30),('P0101','17767', now() - interval '30 days',30);

-- Doctor availability: next 7 days, 09:00-12:00 IST, 15 minute slots.
INSERT INTO clinical.availability(practitioner, starts_at, duration_min, mode)
SELECT p, ((d::date + t::time) AT TIME ZONE 'Asia/Kolkata'), 15, CASE WHEN extract(minute FROM t) = 45 THEN 'video' ELSE 'any' END
FROM unnest(ARRAY['dr.rao','dr.sen','dr.mehta']) p,
     generate_series((now() AT TIME ZONE 'Asia/Kolkata')::date + 1, (now() AT TIME ZONE 'Asia/Kolkata')::date + 7, interval '1 day') d,
     generate_series(timestamp '2000-01-01 09:00', timestamp '2000-01-01 11:45', interval '15 minutes') t;

INSERT INTO clinical.consents(patient_id, grantee_facility, purpose, status, artefact, expires_at) VALUES
 ('P0001','F1','CAREMGT','granted','{"hiTypes":["OPConsultation","DiagnosticReport","Prescription"],"source":"seed"}', now() + interval '365 days');

-- ---------------------------------------------------------------- life sciences
INSERT INTO life.clinics VALUES
 ('C1','Dr. Rao OPD, OmniMed City Hospital','dr.rao','Internal Medicine',12.9716,77.5946,150),
 ('C2','Dr. Sen OPD, OmniMed City Hospital','dr.sen','Internal Medicine',12.9718,77.5949,150),
 ('C3','Dr. Mehta Cardiology, Lakeside Clinic','dr.mehta','Cardiology',12.2958,76.6394,150);

INSERT INTO life.doctor_profiles(doctor, specialty, interests, publication_topics, practice_mix, detailing_consent, consent_at) VALUES
 ('dr.rao','Internal Medicine','{hypertension,diabetes,ckd}','{renal outcomes,glycaemic control}','{"age_60_plus":0.45,"diabetes":0.38,"ckd":0.21}',true, now() - interval '30 days'),
 ('dr.sen','Internal Medicine','{infection,sepsis,pneumonia}','{antimicrobial stewardship}','{"age_60_plus":0.30,"infection":0.40}',true, now() - interval '10 days'),
 ('dr.mehta','Cardiology','{heart failure,hypertension,atrial fibrillation}','{heart failure outcomes}','{"heart_failure":0.35,"af":0.20}',false,NULL);

INSERT INTO life.sample_stock VALUES
 ('mr.vikram','Renovia 10 mg (fictional)','LOT-RV-2409',40),
 ('mr.vikram','Glucaris XR 500 mg (fictional)','LOT-GX-2408',60),
 ('mr.vikram','Cardiovex 25 mg (fictional)','LOT-CV-2407',30);

INSERT INTO life.assets(id, title, kind, product, tags, status, mlr_ref, source, slides) VALUES
 ('A1','RENOVIA-3 Phase III outcomes in CKD with hypertension','trial','Renovia 10 mg (fictional)','{ckd,hypertension,nephrology,internal medicine}','approved','MLR-2026-014','Synthetic demo content',
  '[{"title":"Study design","body":"Randomised, double-blind, 2,400 adults with CKD stage 3 and hypertension. Primary endpoint: sustained 40% eGFR decline. (Synthetic demo data.)"},
    {"title":"Primary outcome","body":"Hazard ratio 0.78 (95% CI 0.66-0.92) versus placebo over 30 months. (Synthetic demo data.)"},
    {"title":"Safety","body":"Hyperkalemia (K > 6.0) in 3.1% vs 1.4%. Monitor potassium at 2 and 4 weeks. (Synthetic demo data.)"},
    {"title":"Prescribing information","body":"Refer to the approved label. Contraindicated with potassium supplements."}]'),
 ('A2','Glucaris XR monograph update and boxed warning','monograph','Glucaris XR 500 mg (fictional)','{diabetes,internal medicine,endocrinology}','approved','MLR-2026-021','Synthetic demo content',
  '[{"title":"Indication","body":"Adjunct to diet and exercise in type 2 diabetes. (Synthetic demo content.)"},
    {"title":"Boxed warning","body":"Lactic acidosis risk; do not start if eGFR < 30. (Synthetic demo content.)"},
    {"title":"Dosing","body":"500 mg once daily with evening meal; titrate by 500 mg weekly. (Synthetic demo content.)"}]'),
 ('A3','Cardiovex patient access programme','pap','Cardiovex 25 mg (fictional)','{heart failure,cardiology}','approved','MLR-2026-030','Synthetic demo content',
  '[{"title":"Eligibility","body":"Patients with HFrEF and household income below the programme threshold. (Synthetic demo content.)"},
    {"title":"How to enrol","body":"Doctor submits the enrolment form; the patient receives 6 months of therapy at no cost. (Synthetic demo content.)"}]'),
 ('A4','Draft: Renovia head-to-head comparison','trial','Renovia 10 mg (fictional)','{ckd,hypertension}','draft','PENDING','Synthetic demo content',
  '[{"title":"Unapproved","body":"This draft has not passed medical-legal review and must never be shown."}]');

INSERT INTO life.rep_slots(doctor, starts_at, duration_min)
SELECT dname, ((d::date + t::time) AT TIME ZONE 'Asia/Kolkata'), 10
FROM unnest(ARRAY['dr.rao','dr.sen','dr.mehta']) dname,
     generate_series((now() AT TIME ZONE 'Asia/Kolkata')::date + 1, (now() AT TIME ZONE 'Asia/Kolkata')::date + 3, interval '1 day') d,
     generate_series(timestamp '2000-01-01 13:00', timestamp '2000-01-01 13:30', interval '10 minutes') t;

-- ---------------------------------------------------------------- guidelines (embeddings are computed by the AI service)
INSERT INTO ai.guidelines(condition_icd10, title, source, url, body) VALUES
 ('E87.5','Treatment of acute hyperkalaemia in adults','UK Kidney Association','https://ukkidney.org/health-professionals/guidelines/treatment-acute-hyperkalaemia-adults','Hyperkalaemia potassium above 6.5 mmol/L is a medical emergency. Obtain a 12-lead ECG, give intravenous calcium for ECG changes, insulin-glucose to shift potassium, stop potassium-sparing drugs, ACE inhibitors and potassium supplements.'),
 ('A41.9','Sepsis: recognition, diagnosis and early management (NG51)','NICE','https://www.nice.org.uk/guidance/ng51','Suspect sepsis with fever, tachycardia, tachypnoea, hypotension or new confusion. Measure lactate, take blood cultures, give broad-spectrum antibiotics within one hour for high risk patients and assess fluid resuscitation.'),
 ('J18.9','Pneumonia (community-acquired): antimicrobial prescribing (NG138)','NICE','https://www.nice.org.uk/guidance/ng138','Community acquired pneumonia presents with cough, fever, productive sputum, dyspnoea and crackles. Use CURB-65 to assess severity, obtain chest x-ray, start antibiotics such as amoxicillin, or a macrolide if penicillin allergy.'),
 ('I10','Hypertension in adults: diagnosis and management (NG136)','NICE','https://www.nice.org.uk/guidance/ng136','Hypertension is confirmed with ambulatory or home blood pressure monitoring. Offer lifestyle advice and antihypertensive treatment with an ACE inhibitor or ARB for diabetes, or a calcium channel blocker.'),
 ('I50.9','Chronic heart failure in adults (NG106)','NICE','https://www.nice.org.uk/guidance/ng106','Heart failure presents with breathlessness, oedema, fatigue and raised NT-proBNP. Use loop diuretics for congestion, and ACE inhibitor, beta blocker and mineralocorticoid receptor antagonist for reduced ejection fraction with potassium monitoring.'),
 ('N39.0','Urinary tract infection (lower): antimicrobial prescribing (NG109)','NICE','https://www.nice.org.uk/guidance/ng109','Lower urinary tract infection presents with dysuria, frequency and urgency. Use urine dipstick, consider nitrofurantoin or trimethoprim guided by local resistance and renal function.'),
 ('R07.9','Recent-onset chest pain of suspected cardiac origin (CG95)','NICE','https://www.nice.org.uk/guidance/cg95','Chest pain with suspected acute coronary syndrome needs a 12-lead ECG and high-sensitivity troponin. Give aspirin unless contraindicated and refer urgently.'),
 ('I21.9','Recent-onset chest pain of suspected cardiac origin (CG95)','NICE','https://www.nice.org.uk/guidance/cg95','Myocardial infarction presents with chest pain, sweating, raised troponin and ECG changes. Arrange urgent reperfusion assessment.'),
 ('E11.9','Standards of Care in Diabetes','American Diabetes Association','https://professional.diabetes.org/standards-of-care','Type 2 diabetes is diagnosed with HbA1c 6.5 percent or higher, fasting glucose or polyuria and polydipsia with hyperglycaemia. Metformin is first line if eGFR permits, adjust dose below eGFR 45 and stop below 30.'),
 ('N18.3','KDIGO clinical practice guidelines for CKD','KDIGO','https://kdigo.org/guidelines/','Chronic kidney disease is staged by eGFR and albuminuria. Monitor potassium with ACE inhibitors, avoid NSAIDs, adjust renally cleared drugs.'),
 ('J45.909','Global Strategy for Asthma Management and Prevention','GINA','https://ginasthma.org/reports/','Asthma presents with wheeze, cough, chest tightness and variable airflow limitation. Use inhaled corticosteroid-containing controller therapy and as-needed reliever.'),
 ('J44.9','Global Strategy for COPD','GOLD','https://goldcopd.org/','COPD presents with chronic cough, sputum and dyspnoea in smokers. Confirm with spirometry, use long-acting bronchodilators.'),
 ('I48.91','Atrial fibrillation: diagnosis and management (NG196)','NICE','https://www.nice.org.uk/guidance/ng196','Atrial fibrillation presents with palpitations and irregular pulse. Confirm with ECG, assess stroke risk with CHA2DS2-VASc and bleeding risk before anticoagulation.'),
 ('E87.1','Hyponatraemia management','Society for Endocrinology','https://www.endocrinology.org/clinical-practice/clinical-guidance/','Hyponatraemia sodium below 135 presents with confusion, nausea and headache. Assess volume status and correct slowly.'),
 ('J06.9','Sore throat (acute): antimicrobial prescribing (NG84)','NICE','https://www.nice.org.uk/guidance/ng84','Upper respiratory infections with sore throat, cough and fever are usually viral and self limiting; avoid antibiotics unless high risk.');
