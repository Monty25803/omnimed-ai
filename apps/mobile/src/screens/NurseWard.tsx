import { CameraView, useCameraPermissions } from "expo-camera";
import { useCallback, useEffect, useState } from "react";
import { Pressable, RefreshControl, ScrollView, Text, Vibration, View } from "react-native";
import { api } from "../api";
import { Badge, Banner, Button, Card, Field, colors, s } from "../ui";

type Tab = "ward" | "emar" | "tasks";

export default function NurseWard() {
  const [tab, setTab] = useState<Tab>("ward");
  return (
    <View style={{ flex: 1 }}>
      <View style={[s.row, { padding: 8, backgroundColor: "#fff", borderBottomWidth: 1, borderColor: colors.line }]}>
        {(["ward", "emar", "tasks"] as Tab[]).map((t) => <Button key={t} small tone={tab === t ? "brand" : "ghost"} title={t === "emar" ? "eMAR scan" : t[0].toUpperCase() + t.slice(1)} onPress={() => setTab(t)} />)}
      </View>
      {tab === "ward" && <Ward />}
      {tab === "emar" && <Emar />}
      {tab === "tasks" && <Tasks />}
    </View>
  );
}

function Ward() {
  const [rows, setRows] = useState<any[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [sel, setSel] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const load = useCallback(async () => {
    setLoading(true);
    try { setRows((await api<any>("clinical/ward/grid")).patients); setErr(null); } catch (e) { setErr((e as Error).message); } finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 30_000); return () => clearInterval(t); }, [load]);
  return (
    <ScrollView contentContainerStyle={{ padding: 12 }} refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />}>
      {err && <Banner tone="bad" text={err} />}
      {sel && <Vitals patient={sel} onDone={() => { setSel(null); load(); }} />}
      {rows.map((r) => {
        const v = r.latest || {};
        return (
          <Pressable key={r.patient.id} onPress={() => setSel(r.patient)}>
            <Card style={v.red_alert ? { borderColor: colors.bad, borderWidth: 2 } : undefined}>
              <View style={[s.row, { justifyContent: "space-between" }]}>
                <Text style={{ fontWeight: "700", fontSize: 15 }}>Bed {r.patient.bed} · {r.patient.name}</Text>
                {v.red_alert && <Badge tone="bad" text="RED ALERT" />}
              </View>
              <Text style={{ fontSize: 22, fontWeight: "800", color: v.mews >= 5 ? colors.bad : v.mews >= 3 ? colors.warn : colors.ok }}>MEWS {v.mews ?? "-"} · qSOFA {v.qsofa ?? "-"}</Text>
              <Text style={s.text}>BP {v.sbp}/{v.dbp} · HR {v.hr} · RR {v.rr} · SpO2 {v.spo2}% · T {v.temp}</Text>
              <Text style={s.muted}>{r.patient.mrn} · tap to record vitals</Text>
            </Card>
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

function Vitals({ patient, onDone }: { patient: any; onDone: () => void }) {
  const [v, setV] = useState<Record<string, string>>({ sbp: "", dbp: "", hr: "", rr: "", temp: "", spo2: "", gcs: "15" });
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true); setErr(null);
    const body: Record<string, number> = {};
    for (const [k, x] of Object.entries(v)) if (x !== "") body[k] = Number(x);
    try {
      const r = await api<any>(`clinical/patients/${patient.id}/vitals`, { json: body });
      setRes(r);
      if (r.score.red_alert) Vibration.vibrate([0, 400, 200, 400]);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  const f = (k: string, label: string) => <Field key={k} label={label} value={v[k]} onChangeText={(x) => setV({ ...v, [k]: x })} keyboardType="decimal-pad" style={{ minWidth: 80 }} />;
  return (
    <Card title={`Vitals · ${patient.name}`}>
      <View style={s.row}>{f("sbp", "Systolic")}{f("dbp", "Diastolic")}{f("hr", "HR")}</View>
      <View style={s.row}>{f("rr", "RR")}{f("temp", "Temp °C")}{f("spo2", "SpO2")}{f("gcs", "GCS")}</View>
      <Button title="Save and score" onPress={save} busy={busy} />
      {err && <Banner tone="bad" text={err} />}
      {res && <Banner tone={res.score.red_alert ? "bad" : "ok"} text={`MEWS ${res.score.mews} · qSOFA ${res.score.qsofa}. ${res.score.red_alert ? "Red alert raised; doctor paged." : "No escalation."}`} />}
      <Button tone="ghost" title="Close" onPress={onDone} />
    </Card>
  );
}

function Emar() {
  const [perm, requestPerm] = useCameraPermissions();
  const [step, setStep] = useState<"wrist" | "med" | null>(null);
  const [wrist, setWrist] = useState("");
  const [med, setMed] = useState("");
  const [override, setOverride] = useState("");
  const [res, setRes] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  function onScan({ data }: { data: string }) {
    Vibration.vibrate(60);
    if (step === "wrist") { setWrist(data); setStep("med"); } else { setMed(data); setStep(null); }
  }
  async function give() {
    setBusy(true); setRes(null);
    try {
      const r = await api<any>("clinical/emar/administer", { json: { wristband: wrist, med_barcode: med, override_reason: override } });
      setRes({ ok: true, text: `Recorded ${r.drug} ${r.strength_mg} mg for ${r.patient_id}.` });
      setWrist(""); setMed(""); setOverride("");
    } catch (e) {
      Vibration.vibrate([0, 300, 150, 300]);
      setRes({ ok: false, text: (e as Error).message });
    } finally { setBusy(false); }
  }
  return (
    <ScrollView contentContainerStyle={{ padding: 12 }}>
      <Card title="Two-barcode check">
        {step && (perm?.granted ? (
          <View style={{ height: 260, borderRadius: 8, overflow: "hidden", marginBottom: 8 }}>
            <CameraView style={{ flex: 1 }} facing="back" barcodeScannerSettings={{ barcodeTypes: ["ean13", "code128", "qr", "datamatrix"] }} onBarcodeScanned={onScan} />
            <Text style={{ position: "absolute", top: 8, left: 8, color: "#fff", fontWeight: "700" }}>Scan {step === "wrist" ? "patient wristband" : "medication"}</Text>
          </View>
        ) : <Button title="Allow camera" onPress={requestPerm} />)}
        <Button title={step ? "Stop scanning" : "Start scan (wristband, then medication)"} tone={step ? "ghost" : "brand"} onPress={() => setStep(step ? null : "wrist")} />
        <Field label="Wristband (MRN)" value={wrist} onChangeText={setWrist} autoCapitalize="characters" />
        <Field label="Medication barcode" value={med} onChangeText={setMed} keyboardType="number-pad" />
        <Field label="Override reason (outside ±60 min)" value={override} onChangeText={setOverride} />
        <Button title="Verify and record" onPress={give} busy={busy} disabled={!wrist || !med} />
        {res && <Banner tone={res.ok ? "ok" : "bad"} text={res.text} />}
      </Card>
    </ScrollView>
  );
}

function Tasks() {
  const [tasks, setTasks] = useState<any[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(async () => { try { setTasks(await api("clinical/tasks")); } catch (e) { setErr((e as Error).message); } }, []);
  useEffect(() => { load(); const t = setInterval(load, 15_000); return () => clearInterval(t); }, [load]);
  return (
    <ScrollView contentContainerStyle={{ padding: 12 }}>
      {err && <Banner tone="bad" text={err} />}
      {tasks.filter((t) => t.status === "open").map((t) => (
        <Card key={t.id}>
          <Badge tone={t.kind === "stat_ecg" ? "bad" : "neutral"} text={t.kind} />
          <Text style={[s.text, { marginVertical: 4 }]}>{t.patient_id} · {t.detail}</Text>
          <Button small title="Mark done" onPress={async () => { await api(`clinical/tasks/${t.id}/complete`, { method: "POST" }); load(); }} />
        </Card>
      ))}
      {!tasks.some((t) => t.status === "open") && <Text style={s.muted}>No open tasks.</Text>}
    </ScrollView>
  );
}
