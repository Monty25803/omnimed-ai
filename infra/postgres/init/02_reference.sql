-- Local reference subset used by the grounding engine and codifier.
-- Codes are a curated demo subset; production deployments must load licensed
-- SNOMED-CT, RxNorm and LOINC releases in place of this file.

-- Diagnoses: SNOMED-CT concept, paired ICD-10 code, synonyms used by the scribe.
CREATE TEMP TABLE dx(snomed text, icd10 text, display text, synonyms text[]);
INSERT INTO dx VALUES
 ('38341003','I10','Essential hypertension','{hypertension,high blood pressure,htn}'),
 ('44054006','E11.9','Type 2 diabetes mellitus','{type 2 diabetes,diabetes,t2dm,diabetes mellitus,sugar problem}'),
 ('709044004','N18.3','Chronic kidney disease stage 3','{chronic kidney disease,ckd,kidney disease,renal impairment}'),
 ('14140009','E87.5','Hyperkalemia','{hyperkalaemia,high potassium,raised potassium}'),
 ('233604007','J18.9','Pneumonia','{community acquired pneumonia,chest infection,lung infection,cap}'),
 ('91302008','A41.9','Sepsis','{septicemia,septicaemia,blood infection}'),
 ('22298006','I21.9','Acute myocardial infarction','{myocardial infarction,heart attack,mi,stemi,nstemi}'),
 ('195967001','J45.909','Asthma','{bronchial asthma,asthma exacerbation}'),
 ('13645005','J44.9','Chronic obstructive pulmonary disease','{copd,chronic bronchitis,emphysema}'),
 ('68566005','N39.0','Urinary tract infection','{uti,bladder infection,cystitis,urine infection}'),
 ('84114007','I50.9','Heart failure','{congestive heart failure,chf,cardiac failure}'),
 ('40930008','E03.9','Hypothyroidism','{underactive thyroid,low thyroid}'),
 ('54150009','J06.9','Upper respiratory tract infection','{urti,common cold,upper respiratory infection,uri}'),
 ('235595009','K21.9','Gastroesophageal reflux disease','{gerd,gord,acid reflux,reflux}'),
 ('271737000','D64.9','Anemia','{anaemia,low hemoglobin,low haemoglobin}'),
 ('89627008','E87.1','Hyponatremia','{hyponatraemia,low sodium}'),
 ('49436004','I48.91','Atrial fibrillation','{af,afib,a fib}'),
 ('34095006','E86.0','Dehydration','{volume depletion}'),
 ('74400008','K35.80','Appendicitis','{acute appendicitis}'),
 ('37796009','G43.909','Migraine','{migraine headache}'),
 ('36971009','J01.90','Sinusitis','{acute sinusitis}'),
 ('43116000','K52.9','Gastroenteritis','{stomach flu,gastro}'),
 ('56717001','A15.9','Tuberculosis','{tb,pulmonary tuberculosis}'),
 ('38362002','A90','Dengue fever','{dengue}'),
 ('61462000','B54','Malaria','{malarial fever}');

INSERT INTO ai.concepts(system, code, display, synonyms, kind, attrs)
SELECT 'SNOMED-CT', snomed, display, synonyms, 'diagnosis', jsonb_build_object('icd10', icd10) FROM dx;
INSERT INTO ai.concepts(system, code, display, synonyms, kind, attrs)
SELECT 'ICD-10', icd10, display, synonyms, 'diagnosis', jsonb_build_object('snomed', snomed) FROM dx;

-- Clinical findings / symptoms (SNOMED-CT).
INSERT INTO ai.concepts(system, code, display, synonyms, kind) VALUES
 ('SNOMED-CT','386661006','Fever','{fever,febrile,high temperature,pyrexia,feverish}','finding'),
 ('SNOMED-CT','49727002','Cough','{cough,coughing}','finding'),
 ('SNOMED-CT','28743005','Productive cough','{productive cough,cough with phlegm,cough with sputum,yellow sputum,green sputum}','finding'),
 ('SNOMED-CT','267036007','Dyspnea','{shortness of breath,short of breath,breathless,breathlessness,dyspnoea,difficulty breathing,sob}','finding'),
('SNOMED-CT','48409008','Respiratory crackles','{crackles,crepitations,creps,rales}','finding'),
 ('SNOMED-CT','29857009','Chest pain','{chest pain,chest tightness,chest discomfort}','finding'),
 ('SNOMED-CT','25064002','Headache','{headache,head ache,head pain}','finding'),
 ('SNOMED-CT','84229001','Fatigue','{fatigue,tired,tiredness,weakness,lethargy}','finding'),
 ('SNOMED-CT','422587007','Nausea','{nausea,nauseous,feel sick}','finding'),
 ('SNOMED-CT','422400008','Vomiting','{vomiting,vomit,throwing up}','finding'),
 ('SNOMED-CT','49650001','Dysuria','{dysuria,burning urination,pain passing urine,burning when urinating}','finding'),
 ('SNOMED-CT','56018004','Wheezing','{wheeze,wheezing}','finding'),
 ('SNOMED-CT','80313002','Palpitations','{palpitations,racing heart,heart racing}','finding'),
 ('SNOMED-CT','267038008','Edema','{edema,oedema,swelling of legs,ankle swelling,swollen ankles,leg swelling}','finding'),
 ('SNOMED-CT','21522001','Abdominal pain','{abdominal pain,stomach pain,belly pain,tummy pain}','finding'),
 ('SNOMED-CT','62315008','Diarrhea','{diarrhea,diarrhoea,loose stools,loose motions}','finding'),
 ('SNOMED-CT','271807003','Rash','{rash,skin rash}','finding'),
 ('SNOMED-CT','404640003','Dizziness','{dizziness,dizzy,lightheaded,giddiness}','finding'),
 ('SNOMED-CT','161891005','Back pain','{back pain,backache}','finding'),
 ('SNOMED-CT','68962001','Muscle pain','{muscle pain,myalgia,body ache,body pain}','finding'),
 ('SNOMED-CT','28442001','Polyuria','{polyuria,frequent urination,passing urine frequently}','finding'),
 ('SNOMED-CT','17173007','Excessive thirst','{polydipsia,excessive thirst,very thirsty}','finding'),
 ('SNOMED-CT','419284004','Altered mental status','{confusion,confused,altered mental status,drowsy}','finding'),
 ('SNOMED-CT','43724002','Chills','{chills,rigors,shivering}','finding'),
 ('SNOMED-CT','267102003','Sore throat','{sore throat,throat pain}','finding'),
 ('SNOMED-CT','91936005','Allergy to penicillin','{penicillin allergy,allergic to penicillin}','allergy'),
 ('SNOMED-CT','91939003','Allergy to sulfonamide','{sulfa allergy,allergic to sulfa,sulphonamide allergy}','allergy');

-- Medications (RxNorm ingredient RxCUIs).
CREATE TEMP TABLE rx(rxcui text, display text, synonyms text[], cls text[], strengths numeric[], max_daily numeric, renal_min numeric, raises_k boolean);
INSERT INTO rx VALUES
 ('29046','lisinopril','{lisinopril,zestril,prinivil}','{ace-inhibitor,antihypertensive}','{2.5,5,10,20,40}',80,NULL,true),
 ('3827','enalapril','{enalapril,vasotec}','{ace-inhibitor,antihypertensive}','{2.5,5,10,20}',40,NULL,true),
 ('52175','losartan','{losartan,cozaar}','{arb,antihypertensive}','{25,50,100}',100,NULL,true),
 ('17767','amlodipine','{amlodipine,norvasc,amlong}','{calcium-channel-blocker,antihypertensive}','{2.5,5,10}',10,NULL,false),
 ('6918','metoprolol','{metoprolol,lopressor,betaloc}','{beta-blocker,antihypertensive}','{25,50,100}',400,NULL,false),
 ('9997','spironolactone','{spironolactone,aldactone}','{potassium-sparing,diuretic}','{25,50,100}',400,30,true),
 ('644','amiloride','{amiloride,midamor}','{potassium-sparing,diuretic}','{5}',20,30,true),
 ('298869','eplerenone','{eplerenone,inspra}','{potassium-sparing,diuretic}','{25,50}',100,30,true),
 ('4603','furosemide','{furosemide,frusemide,lasix}','{loop-diuretic,diuretic}','{20,40,80}',600,NULL,false),
 ('8591','potassium chloride','{potassium chloride,kcl,potassium supplement}','{potassium-supplement}','{600,750}',6000,NULL,true),
 ('6809','metformin','{metformin,glucophage,glycomet}','{biguanide,antidiabetic}','{500,850,1000}',2550,30,false),
 ('274783','insulin glargine','{insulin glargine,lantus,glargine}','{insulin,antidiabetic}','{100}',NULL,NULL,false),
 ('83367','atorvastatin','{atorvastatin,lipitor,atorva}','{statin}','{10,20,40,80}',80,NULL,false),
 ('1191','aspirin','{aspirin,ecosprin,acetylsalicylic acid}','{antiplatelet,nsaid}','{75,150,325}',4000,NULL,false),
 ('32968','clopidogrel','{clopidogrel,plavix}','{antiplatelet}','{75}',300,NULL,false),
 ('11289','warfarin','{warfarin,coumadin}','{anticoagulant}','{1,2,5}',15,NULL,false),
 ('161','acetaminophen','{paracetamol,acetaminophen,crocin,dolo,calpol}','{analgesic,antipyretic}','{500,650}',4000,NULL,false),
 ('5640','ibuprofen','{ibuprofen,brufen,advil}','{nsaid,analgesic}','{200,400,600}',3200,30,false),
 ('723','amoxicillin','{amoxicillin,amoxycillin,mox,novamox}','{penicillin,antibiotic}','{250,500,875}',3000,NULL,false),
 ('18631','azithromycin','{azithromycin,azithral,zithromax}','{macrolide,antibiotic}','{250,500}',500,NULL,false),
 ('2551','ciprofloxacin','{ciprofloxacin,cipro,ciplox}','{fluoroquinolone,antibiotic}','{250,500,750}',1500,NULL,false),
 ('2193','ceftriaxone','{ceftriaxone,rocephin,monocef}','{cephalosporin,antibiotic}','{250,500,1000,2000}',4000,NULL,false),
 ('3640','doxycycline','{doxycycline,doxy}','{tetracycline,antibiotic}','{100}',200,NULL,false),
 ('10831','sulfamethoxazole-trimethoprim','{cotrimoxazole,co-trimoxazole,bactrim,septran}','{sulfonamide,antibiotic}','{480,960}',1920,30,true),
 ('7646','omeprazole','{omeprazole,omez,prilosec}','{ppi}','{20,40}',40,NULL,false),
 ('40790','pantoprazole','{pantoprazole,pan,protonix}','{ppi}','{20,40}',80,NULL,false),
 ('10582','levothyroxine','{levothyroxine,thyroxine,eltroxin,thyronorm}','{thyroid-hormone}','{0.025,0.05,0.1}',0.3,NULL,false),
 ('8640','prednisone','{prednisone,prednisolone,wysolone}','{corticosteroid}','{5,10,20,40}',80,NULL,false),
 ('435','albuterol','{salbutamol,albuterol,asthalin,ventolin}','{beta-agonist,bronchodilator}','{0.1,2,4}',32,NULL,false),
 ('41493','meropenem','{meropenem,merrem}','{carbapenem,antibiotic}','{500,1000}',6000,NULL,false),
 ('6922','metronidazole','{metronidazole,flagyl,metrogyl}','{nitroimidazole,antibiotic}','{200,400,500}',4000,NULL,false),
 ('7052','morphine','{morphine}','{opioid,analgesic}','{5,10,15}',200,30,false),
 ('10689','tramadol','{tramadol,ultram}','{opioid,analgesic}','{50,100}',400,30,false),
 ('2764','chloroquine','{chloroquine}','{antimalarial}','{250,500}',1000,NULL,false),
 ('36567','simvastatin','{simvastatin,zocor}','{statin}','{10,20,40}',40,NULL,false),
 ('4815','glimepiride','{glimepiride,amaryl}','{sulfonylurea,antidiabetic}','{1,2,4}',8,NULL,false);

INSERT INTO ai.concepts(system, code, display, synonyms, kind, attrs)
SELECT 'RxNorm', rxcui, display, synonyms, 'drug',
       jsonb_build_object('classes', cls, 'strengths_mg', strengths, 'max_daily_mg', max_daily) FROM rx;

INSERT INTO clinical.drugs(rxcui, ingredient, drug_class, max_daily_mg, renal_min_egfr, raises_potassium)
SELECT rxcui, display, cls, max_daily, renal_min, raises_k FROM rx;

-- Laboratory and vital observations (LOINC). attrs carries reference and panic ranges.
INSERT INTO ai.concepts(system, code, display, synonyms, kind, attrs) VALUES
 ('LOINC','2823-3','Potassium','{potassium,k,serum potassium,k+}','lab','{"unit":"mmol/L","low":3.5,"high":5.1,"panic_low":2.5,"panic_high":6.5,"astm":"K"}'),
 ('LOINC','2951-2','Sodium','{sodium,na,serum sodium,na+}','lab','{"unit":"mmol/L","low":135,"high":145,"panic_low":120,"panic_high":160,"astm":"NA"}'),
 ('LOINC','2075-0','Chloride','{chloride,cl}','lab','{"unit":"mmol/L","low":98,"high":107,"astm":"CL"}'),
 ('LOINC','1963-8','Bicarbonate','{bicarbonate,hco3,co2}','lab','{"unit":"mmol/L","low":22,"high":29,"panic_low":10,"astm":"HCO3"}'),
 ('LOINC','2160-0','Creatinine','{creatinine,serum creatinine,creat}','lab','{"unit":"mg/dL","low":0.6,"high":1.2,"panic_high":7.0,"astm":"CREA"}'),
 ('LOINC','3094-0','Urea nitrogen','{bun,urea,blood urea nitrogen}','lab','{"unit":"mg/dL","low":7,"high":20,"astm":"BUN"}'),
 ('LOINC','33914-3','eGFR','{egfr,gfr,estimated gfr,kidney function}','lab','{"unit":"mL/min/1.73m2","low":90,"high":200,"astm":"EGFR"}'),
 ('LOINC','2345-7','Glucose','{glucose,blood sugar,sugar,blood glucose}','lab','{"unit":"mg/dL","low":70,"high":140,"panic_low":40,"panic_high":450,"astm":"GLU"}'),
 ('LOINC','4548-4','Hemoglobin A1c','{hba1c,a1c,glycated hemoglobin,glycosylated hemoglobin}','lab','{"unit":"%","low":4.0,"high":5.6,"astm":"HBA1C"}'),
 ('LOINC','718-7','Hemoglobin','{hemoglobin,haemoglobin,hb,hgb}','lab','{"unit":"g/dL","low":12.0,"high":17.5,"panic_low":7.0,"astm":"HGB"}'),
 ('LOINC','6690-2','WBC count','{wbc,white cell count,white blood cells,leukocytes,tlc}','lab','{"unit":"10*3/uL","low":4.0,"high":11.0,"panic_low":2.0,"panic_high":30.0,"astm":"WBC"}'),
 ('LOINC','58410-2','CBC panel','{cbc,complete blood count,full blood count,fbc,hemogram}','lab','{"unit":""}'),
('LOINC','777-3','Platelet count','{platelets,platelet count,plt}','lab','{"unit":"10*3/uL","low":150,"high":450,"panic_low":20,"panic_high":1000,"astm":"PLT"}'),
 ('LOINC','10839-9','Troponin I','{troponin,troponin i,trop i}','lab','{"unit":"ng/mL","low":0,"high":0.04,"panic_high":0.5,"astm":"TNI"}'),
 ('LOINC','2524-7','Lactate','{lactate,lactic acid}','lab','{"unit":"mmol/L","low":0.5,"high":2.0,"panic_high":4.0,"astm":"LAC"}'),
 ('LOINC','1742-6','ALT','{alt,sgpt,alanine aminotransferase}','lab','{"unit":"U/L","low":7,"high":56,"astm":"ALT"}'),
 ('LOINC','3016-3','TSH','{tsh,thyroid stimulating hormone,thyroid test}','lab','{"unit":"mIU/L","low":0.4,"high":4.0,"astm":"TSH"}'),
 ('LOINC','6301-6','INR','{inr,pt inr,prothrombin time}','lab','{"unit":"","low":0.8,"high":1.2,"panic_high":5.0,"astm":"INR"}'),
 ('LOINC','1988-5','C reactive protein','{crp,c reactive protein}','lab','{"unit":"mg/L","low":0,"high":10,"astm":"CRP"}'),
 ('LOINC','17861-6','Calcium','{calcium,ca,serum calcium}','lab','{"unit":"mg/dL","low":8.6,"high":10.3,"panic_low":6.0,"panic_high":13.0,"astm":"CA"}'),
 ('LOINC','33762-6','NT-proBNP','{bnp,nt probnp,nt-probnp}','lab','{"unit":"pg/mL","low":0,"high":125,"astm":"BNP"}'),
 ('LOINC','5794-3','Hemoglobin in urine','{urine blood,urine dipstick,urinalysis}','lab','{"unit":"","astm":"UBLD"}'),
 ('LOINC','600-7','Blood culture','{blood culture,culture}','lab','{"unit":"","astm":"BCX"}'),
 ('LOINC','11524-6','ECG study','{ecg,ekg,electrocardiogram}','lab','{"unit":""}'),
 ('LOINC','36643-5','Chest X-ray','{chest x-ray,chest xray,cxr,chest x ray}','lab','{"unit":""}'),
 ('LOINC','8867-4','Heart rate','{heart rate,pulse,hr}','vital','{"unit":"/min"}'),
 ('LOINC','9279-1','Respiratory rate','{respiratory rate,resp rate,rr}','vital','{"unit":"/min"}'),
 ('LOINC','8480-6','Systolic blood pressure','{systolic,sbp}','vital','{"unit":"mm[Hg]"}'),
 ('LOINC','8462-4','Diastolic blood pressure','{diastolic,dbp}','vital','{"unit":"mm[Hg]"}'),
 ('LOINC','8310-5','Body temperature','{temperature,temp,body temperature}','vital','{"unit":"Cel"}'),
 ('LOINC','59408-5','Oxygen saturation','{spo2,oxygen saturation,o2 sat,saturation}','vital','{"unit":"%"}'),
 ('LOINC','9269-2','Glasgow coma score total','{gcs,glasgow coma scale}','vital','{"unit":"{score}"}');

-- Drug-drug interactions keyed by RxCUI (a < b not required; checker tests both directions).
INSERT INTO clinical.interactions(a, b, severity, effect) VALUES
 ('9997','29046','hard','Spironolactone with an ACE inhibitor raises the risk of severe hyperkalemia.'),
 ('9997','3827','hard','Spironolactone with an ACE inhibitor raises the risk of severe hyperkalemia.'),
 ('9997','52175','hard','Spironolactone with an ARB raises the risk of severe hyperkalemia.'),
 ('9997','8591','hard','Potassium supplement with a potassium-sparing diuretic can cause life-threatening hyperkalemia.'),
 ('644','8591','hard','Potassium supplement with a potassium-sparing diuretic can cause life-threatening hyperkalemia.'),
 ('298869','8591','hard','Potassium supplement with a potassium-sparing diuretic can cause life-threatening hyperkalemia.'),
 ('11289','1191','hard','Warfarin with aspirin substantially increases bleeding risk.'),
 ('11289','5640','hard','Warfarin with an NSAID substantially increases bleeding risk.'),
 ('11289','2551','soft','Ciprofloxacin can potentiate warfarin; monitor INR.'),
 ('11289','10831','hard','Co-trimoxazole markedly potentiates warfarin; INR may rise sharply.'),
 ('11289','6922','hard','Metronidazole markedly potentiates warfarin.'),
 ('11289','18631','soft','Azithromycin may increase INR; monitor.'),
 ('32968','7646','soft','Omeprazole may reduce clopidogrel activation; prefer pantoprazole.'),
 ('29046','5640','soft','NSAIDs reduce ACE inhibitor effect and can worsen renal function.'),
 ('29046','8591','soft','ACE inhibitor with potassium supplement can raise potassium; monitor.'),
 ('36567','18631','soft','Macrolides may raise statin levels; monitor for myopathy.'),
 ('83367','18631','soft','Macrolides may raise statin levels; monitor for myopathy.'),
 ('10689','7052','soft','Two opioids together increase respiratory depression risk.'),
 ('1191','5640','soft','Ibuprofen may reduce the antiplatelet effect of aspirin and increases GI bleeding risk.'),
 ('10831','29046','soft','Trimethoprim with an ACE inhibitor can raise potassium.'),
 ('10831','9997','hard','Trimethoprim with spironolactone can cause severe hyperkalemia.');
