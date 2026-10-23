import * as Location from "expo-location";
import { useEffect, useRef, useState } from "react";
import { ScrollView, Text, View } from "react-native";
import { api, ApiError } from "../api";
import { Badge, Banner, Button, Card, colors, s } from "../ui";

export default function MrField() {
  const [clinics, setClinics] = useState<any[]>([]);
  const [checkin, setCheckin] = useState<{ id: number; clinic: any } | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => { api<any[]>("life/clinics").then(setClinics).catch((e) => setMsg({ ok: false, text: e.message })); }, []);

  async function checkIn(clinic: any) {
    setBusy(clinic.id); setMsg(null);
    try {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== "granted") throw new Error("Location permission is required to verify the visit.");
      const pos = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High });
      if (pos.mocked) throw new Error("Mock locations are not accepted.");
      const r = await api<any>("life/checkins", { json: { clinic_id: clinic.id, lat: pos.coords.latitude, lon: pos.coords.longitude, accuracy_m: pos.coords.accuracy ?? undefined } });
      setCheckin({ id: r.checkin_id, clinic });
      setMsg({ ok: true, text: `Verified at ${clinic.name} (${r.distance_m} m). Valid ${r.valid_minutes} min.` });
    } catch (e) {
      const d = e instanceof ApiError ? e.body?.detail : null;
      setMsg({ ok: false, text: d?.reason ? `Rejected: ${d.reason} (${d.distance_m} m away)` : (e as Error).message });
    } finally { setBusy(null); }
  }

  return (
    <ScrollView contentContainerStyle={{ padding: 12 }}>
      <Banner tone="warn" text="No patient data is available to field staff. Content unlocks only during a geo-verified visit to a consenting doctor." />
      {msg && <Banner tone={msg.ok ? "ok" : "bad"} text={msg.text} />}
      {checkin ? (
        <>
          <Detailing checkinId={checkin.id} doctor={checkin.clinic.doctor} />
          <Button tone="ghost" title="End visit" onPress={async () => { await api(`life/checkins/${checkin.id}/end`, { method: "POST" }); setCheckin(null); setMsg(null); }} />
        </>
      ) : clinics.map((c) => (
        <Card key={c.id} title={c.name}>
          <Text style={s.muted}>{c.doctor_name} · {c.specialty} · fence {c.radius_m} m</Text>
          <View style={[s.row, { marginTop: 6 }]}>
            <Badge tone={c.detailing_consent ? "ok" : "bad"} text={c.detailing_consent ? "opted in" : "no consent"} />
          </View>
          <Button title="Check in here" onPress={() => checkIn(c)} busy={busy === c.id} />
        </Card>
      ))}
    </ScrollView>
  );
}

function Detailing({ checkinId, doctor }: { checkinId: number; doctor: string }) {
  const [assets, setAssets] = useState<any[]>([]);
  const [asset, setAsset] = useState<any>(null);
  const [slide, setSlide] = useState(0);
  const since = useRef(Date.now());
  useEffect(() => { api<any[]>(`life/assets?doctor=${doctor}`).then(setAssets).catch(() => undefined); }, [doctor]);

  async function log(n: number) {
    const dwell = Date.now() - since.current;
    since.current = Date.now();
    await api("life/engagement", { json: { checkin_id: checkinId, asset_id: asset.id, slide: n, dwell_ms: Math.min(dwell, 3_600_000) } }).catch(() => undefined);
  }
  async function go(n: number) { await log(slide); setSlide(n); }

  if (!asset) {
    return (
      <Card title="Approved content">
        {assets.map((a) => (
          <View key={a.id} style={{ marginBottom: 8 }}>
            <Text style={{ fontWeight: "700", color: colors.ink }}>{a.title}</Text>
            <Text style={s.muted}>{a.product} · {a.mlr_ref}</Text>
            <Button small title="Present" onPress={async () => { setAsset(await api(`life/assets/${a.id}`)); setSlide(0); since.current = Date.now(); }} />
          </View>
        ))}
      </Card>
    );
  }
  const slides = asset.slides || [];
  return (
    <Card title={asset.title}>
      <View style={{ backgroundColor: "#eef6f8", borderRadius: 8, padding: 18, minHeight: 180, justifyContent: "center" }}>
        <Text style={{ fontSize: 20, fontWeight: "700", color: colors.ink, marginBottom: 6 }}>{slides[slide]?.title}</Text>
        <Text style={s.text}>{slides[slide]?.body}</Text>
      </View>
      <View style={[s.row, { justifyContent: "space-between", marginTop: 8 }]}>
        <Button small title="Prev" disabled={slide === 0} onPress={() => go(slide - 1)} />
        <Text style={s.muted}>{slide + 1} / {slides.length}</Text>
        <Button small title="Next" disabled={slide >= slides.length - 1} onPress={() => go(slide + 1)} />
      </View>
      <Button tone="ghost" title="Finish" onPress={async () => { await log(slide); setAsset(null); }} />
      <Text style={s.muted}>{asset.mlr_ref} · {asset.source}</Text>
    </Card>
  );
}
