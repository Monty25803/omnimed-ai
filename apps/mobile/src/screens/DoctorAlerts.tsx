import { useCallback, useEffect, useRef, useState } from "react";
import { AppState, RefreshControl, ScrollView, Text, Vibration, View } from "react-native";
import { api } from "../api";
import { Badge, Banner, Button, Card, colors, s } from "../ui";

/** Polls the alert feed; React Native has no built-in EventSource, and polling survives flaky mobile networks. */
export default function DoctorAlerts() {
  const [alerts, setAlerts] = useState<any[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const seen = useRef<Set<number>>(new Set());
  const first = useRef(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const xs = await api<any[]>("clinical/alerts");
      const fresh = xs.filter((a) => !seen.current.has(a.id) && (a.status === "open" || a.status === "escalated"));
      if (!first.current && fresh.some((a) => a.severity === "critical")) Vibration.vibrate([0, 500, 250, 500, 250, 500]);
      xs.forEach((a) => seen.current.add(a.id));
      first.current = false;
      setAlerts(xs);
      setErr(null);
    } catch (e) { setErr((e as Error).message); } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    load();
    let t = setInterval(load, 10_000);
    const sub = AppState.addEventListener("change", (st) => {
      clearInterval(t);
      if (st === "active") { load(); t = setInterval(load, 10_000); }
    });
    return () => { clearInterval(t); sub.remove(); };
  }, [load]);

  async function ack(id: number) {
    await api(`clinical/alerts/${id}/ack`, { method: "POST" });
    load();
  }

  const open = alerts.filter((a) => a.status === "open" || a.status === "escalated");
  return (
    <ScrollView contentContainerStyle={{ padding: 12 }} refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />}>
      {err && <Banner tone="bad" text={err} />}
      <Text style={[s.text, { fontWeight: "700", marginBottom: 6 }]}>{open.length} open alert(s)</Text>
      {alerts.slice(0, 40).map((a) => (
        <Card key={a.id} style={{ borderLeftWidth: 5, borderLeftColor: a.severity === "critical" ? colors.bad : a.severity === "high" ? colors.warn : colors.line }}>
          <View style={[s.row, { justifyContent: "space-between" }]}>
            <Text style={{ fontWeight: "700" }}>{a.kind}</Text>
            <Badge tone={a.status === "open" || a.status === "escalated" ? "bad" : "ok"} text={a.status} />
          </View>
          <Text style={[s.text, { marginVertical: 4 }]}>{a.message}</Text>
          <Text style={s.muted}>{new Date(a.created_at).toLocaleString()}{a.tier ? ` · escalation tier ${a.tier}` : ""}</Text>
          {(a.status === "open" || a.status === "escalated") && <Button small title="Acknowledge" onPress={() => ack(a.id)} />}
        </Card>
      ))}
    </ScrollView>
  );
}
