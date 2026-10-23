import { StatusBar } from "expo-status-bar";
import { useEffect, useState } from "react";
import { Pressable, SafeAreaView, ScrollView, Text, View } from "react-native";
import { ApiError, login, logout, restore } from "./src/api";
import { Banner, Button, Field, colors, s } from "./src/ui";
import DoctorAlerts from "./src/screens/DoctorAlerts";
import MrField from "./src/screens/MrField";
import NurseWard from "./src/screens/NurseWard";

type Session = { username: string; roles: string[] };
const PERSONAS = [["nurse.anita", "Nurse"], ["mr.vikram", "Medical rep"], ["dr.rao", "Doctor"]];

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => { restore().then(setSession).finally(() => setReady(true)); }, []);

  const role = session?.roles.find((r) => ["nurse", "mr", "doctor"].includes(r));
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }}>
      <StatusBar style="light" />
      <View style={{ backgroundColor: colors.brand, padding: 14, flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
        <Text style={{ color: "#fff", fontSize: 18, fontWeight: "700" }}>OmniMed <Text style={{ color: "#7fe0e8" }}>AI</Text></Text>
        {session && <Pressable onPress={async () => { await logout(); setSession(null); }}><Text style={{ color: "#fff" }}>{session.username} · Sign out</Text></Pressable>}
      </View>
      <Text style={{ backgroundColor: "#3b2f00", color: "#ffd76a", textAlign: "center", fontSize: 11, paddingVertical: 2 }}>Synthetic demonstration data. Not for clinical use.</Text>
      {!ready ? null : !session ? <Login onDone={setSession} /> : role === "nurse" ? <NurseWard /> : role === "mr" ? <MrField /> : role === "doctor" ? <DoctorAlerts /> : (
        <View style={{ padding: 16 }}><Banner tone="bad" text="The mobile app supports nurse, medical rep and doctor accounts. Use the web portal for other roles." /></View>
      )}
    </SafeAreaView>
  );
}

function Login({ onDone }: { onDone: (s: Session) => void }) {
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function go(user = u, pass = p) {
    setBusy(true); setErr(null);
    try { onDone(await login(user, pass)); } catch (e) { setErr(e instanceof ApiError ? e.message : `Cannot reach the identity server (${(e as Error).message})`); } finally { setBusy(false); }
  }
  return (
    <ScrollView contentContainerStyle={{ padding: 16 }}>
      <Field label="Username" value={u} onChangeText={setU} autoCapitalize="none" autoCorrect={false} />
      <Field label="Password" value={p} onChangeText={setP} secureTextEntry />
      <Button title="Sign in" onPress={() => go()} busy={busy} />
      {err && <Banner tone="bad" text={err} />}
      <Text style={[s.muted, { marginTop: 16 }]}>Demo personas (password omnimed)</Text>
      {PERSONAS.map(([user, label]) => <Button key={user} tone="ghost" title={`${label} · ${user}`} onPress={() => go(user, "omnimed")} />)}
    </ScrollView>
  );
}
